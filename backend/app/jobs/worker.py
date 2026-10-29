"""The worker: takes jobs from the queue and runs them.

    uv run python -m app.jobs.worker

Several workers can run at once: each job is taken by one of them, and a conversation is worked
on by one worker at a time (its turn lease). SIGTERM or Ctrl+C stops reading new jobs and lets the
running ones finish; a job cut short becomes visible again after its visibility timeout.
"""

import asyncio
import contextlib
import logging
import random
import signal
import sys
from collections.abc import Awaitable, Callable

import httpx
from pydantic import ValidationError

from app.bot.transcription import OpenAITranscription
from app.core.config import Environment, Settings, SettingsError, load_settings
from app.core.logs import configure_logging, log_asyncio_exception
from app.core.observability import init_sentry
from app.db.connection import Database, create_pool
from app.db.media_store import S3MediaStore
from app.jobs import queue
from app.jobs.context import WorkerContext
from app.jobs.ingest import PermanentJobError, handle_event
from app.jobs.queue import ConversationJob, EventJob, ExpireHoldJob, QueuedJob, parse_job
from app.jobs.timing import MAX_ATTEMPTS, backoff_seconds
from app.jobs.turns import RetryLaterError, handle_conversation, handle_expire_hold
from app.whatsapp.client import WhatsAppClient
from app.whatsapp.media import MetaMediaDownloader

logger = logging.getLogger(__name__)

# How long running jobs get to finish after a stop signal.
SHUTDOWN_GRACE_SECONDS = 30.0
# After the database fails, wait before reading again.
READ_ERROR_PAUSE_SECONDS = 2.0


async def run_job(ctx: WorkerContext, queued: QueuedJob) -> None:
    """Runs one job and settles it in the queue: done, retried later or dead-lettered."""
    try:
        job = parse_job(queued.body)
    except ValidationError:
        logger.error("jobs.unreadable", extra={"msg_id": queued.msg_id})  # noqa: TRY400 - no body in logs
        await queue.dead_letter(ctx.db, queued, "unreadable_job")
        return

    async def keep_alive() -> None:
        await queue.keep_hidden(ctx.db, queued.msg_id)

    log_extra: dict[str, object] = {"msg_id": queued.msg_id, "kind": job.kind, "attempt": queued.attempt}
    try:
        await _dispatch(ctx, job, final_attempt=queued.attempt >= MAX_ATTEMPTS, keep_alive=keep_alive)
    except PermanentJobError as error:
        logger.error("jobs.permanent_failure", extra={**log_extra, "reason": str(error)})  # noqa: TRY400
        await queue.dead_letter(ctx.db, queued, "permanent")
        return
    except Exception as error:
        await _failed(ctx, queued, error, log_extra)
        return
    await queue.done(ctx.db, queued.msg_id)


async def _dispatch(
    ctx: WorkerContext,
    job: EventJob | ConversationJob | ExpireHoldJob,
    *,
    final_attempt: bool,
    keep_alive: Callable[[], Awaitable[None]],
) -> None:
    if isinstance(job, EventJob):
        await handle_event(ctx, job)
    elif isinstance(job, ConversationJob):
        await handle_conversation(ctx, job, final_attempt=final_attempt, keep_alive=keep_alive)
    else:
        await handle_expire_hold(ctx, job)


async def _failed(
    ctx: WorkerContext, queued: QueuedJob, error: Exception, log_extra: dict[str, object]
) -> None:
    error_type = type(error).__name__
    if queued.attempt >= MAX_ATTEMPTS:
        # Someone has to look at it: Sentry gets the error, the job keeps its ids in the DLQ.
        logger.error("jobs.dead_lettered", extra={**log_extra, "error": error_type})
        await queue.dead_letter(ctx.db, queued, error_type)
        return
    delay = backoff_seconds(queued.attempt, ctx.jitter())
    if isinstance(error, RetryLaterError) and error.retry_after is not None:
        delay = max(delay, error.retry_after)
    level = logging.WARNING if isinstance(error, RetryLaterError) else logging.ERROR
    logger.log(level, "jobs.retry_scheduled", extra={**log_extra, "error": error_type, "delay": round(delay)})
    await queue.retry_later(ctx.db, queued.msg_id, delay)


async def work(ctx: WorkerContext, stop: asyncio.Event) -> None:
    """Reads and runs jobs until `stop` is set, at most `worker_concurrency` at a time."""
    running: set[asyncio.Task[None]] = set()
    while not stop.is_set():
        free = ctx.settings.worker_concurrency - len(running)
        if free <= 0:
            await asyncio.wait(running, return_when=asyncio.FIRST_COMPLETED)
            continue
        try:
            jobs = await queue.read(ctx.db, quantity=free)
        except Exception:
            logger.exception("jobs.read_failed")
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(stop.wait(), READ_ERROR_PAUSE_SECONDS)
            continue
        for queued in jobs:
            task = asyncio.create_task(_run_settled(ctx, queued))
            running.add(task)
            task.add_done_callback(running.discard)
    if running:
        await asyncio.wait(running, timeout=SHUTDOWN_GRACE_SECONDS)


async def _run_settled(ctx: WorkerContext, queued: QueuedJob) -> None:
    try:
        await run_job(ctx, queued)
    except Exception:
        # The queue itself failed while settling the job: it shows up again after its timeout.
        logger.exception("jobs.settle_failed", extra={"msg_id": queued.msg_id})


async def main(settings: Settings) -> None:
    asyncio.get_running_loop().set_exception_handler(log_asyncio_exception)
    stop = asyncio.Event()
    for signum in (signal.SIGINT, signal.SIGTERM):
        with contextlib.suppress(NotImplementedError):  # Windows
            asyncio.get_running_loop().add_signal_handler(signum, stop.set)

    pool = create_pool(settings)
    await pool.open(wait=False)
    async with httpx.AsyncClient() as http:
        ctx = WorkerContext(
            settings=settings,
            db=Database(pool),
            whatsapp=WhatsAppClient(settings, http),
            media_source=MetaMediaDownloader(settings, http) if settings.media_storage_configured else None,
            media_store=S3MediaStore(settings) if settings.media_storage_configured else None,
            transcriber=OpenAITranscription(settings, http) if settings.transcription_api_key else None,
            jitter=random.random,
        )
        logger.info(
            "jobs.worker_started",
            extra={
                "concurrency": settings.worker_concurrency,
                "media": ctx.media_store is not None,
                "transcription": ctx.transcriber is not None,
                "echo_bot": settings.echo_bot_enabled,
            },
        )
        try:
            await work(ctx, stop)
        finally:
            await pool.close()
    logger.info("jobs.worker_stopped")


def _check(settings: Settings) -> None:
    if settings.environment is not Environment.DEVELOPMENT and not settings.media_storage_configured:
        # Outside development a voice note must never stay only at Meta, where it expires in 7 days.
        raise SettingsError("Configuración incompleta o inválida: STORAGE_S3_* (falta)")


if __name__ == "__main__":
    try:
        loaded = load_settings()
        _check(loaded)
    except SettingsError as exc:
        print(exc, file=sys.stderr)  # noqa: T201 - before logging exists
        sys.exit(2)
    configure_logging(loaded.log_level)
    init_sentry(loaded)
    if sys.platform == "win32":
        # psycopg's async mode does not work with Windows' default Proactor event loop.
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())  # type: ignore[attr-defined,unused-ignore]
    asyncio.run(main(loaded))
