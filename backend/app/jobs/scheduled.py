"""One clinic's share of each scheduled run (milestone 1D):

- reminders, every 15 minutes: queue recordatorio_cita 24 h before each appointment;
- watchdog, every minute: alert the team again about an urgency nobody attended, and tell Jared
  about patients without an answer, sends with an unknown outcome and reminders that failed;
- cleanup, every hour, acting only at 3:00 in the clinic's time zone: delete audios and photos
  past the clinic's retention and files no message points to, erase the content of Meta's
  notices processed 7 days ago, and delete offered slots older than 7 days.
"""

import logging
from datetime import datetime, timedelta
from uuid import UUID

from app.db import bot as bot_data
from app.db import watch as db
from app.db.media_store import MediaStoreError
from app.jobs import ops_alerts
from app.jobs.context import WorkerContext
from app.jobs.queue import ScheduledJob, TeamAlertJob, enqueue
from app.jobs.reminders import queue_reminders
from app.jobs.timing import clinic_zone

logger = logging.getLogger(__name__)

CLEANUP_HOUR = 3
WEBHOOK_PAYLOAD_TTL = timedelta(days=7)
OFFERED_SLOTS_TTL = timedelta(days=7)
# A file younger than this may be on its way to its message (stored, row not yet updated).
ORPHAN_MIN_AGE = timedelta(days=1)
MAX_FILES_PER_RUN = 1000


async def handle_scheduled(ctx: WorkerContext, job: ScheduledJob) -> None:
    now = ctx.clock()
    match job.task:
        case "reminders":
            await queue_reminders(ctx, job.clinic_id, now)
        case "watchdog":
            await watch(ctx, job.clinic_id, now)
        case "cleanup":
            await clean_up(ctx, job.clinic_id, now)


async def watch(ctx: WorkerContext, clinic_id: UUID, now: datetime) -> None:
    realerted = 0
    async with ctx.db.clinic_transaction(clinic_id) as conn:
        for escalation_id in await db.urgencies_to_realert(conn, now):
            await db.mark_realerted(conn, escalation_id, now)
            if await bot_data.queue_team_alerts(conn, clinic_id, escalation_id, round_=2):
                await enqueue(conn, TeamAlertJob(clinic_id=clinic_id, escalation_id=escalation_id))
            realerted += 1
        troubles = await db.troubles(conn, now)
    if realerted:
        logger.warning("bot.urgency_realerted", extra={"clinic_id": str(clinic_id), "escalations": realerted})
    await ops_alerts.raise_alert(ctx.db, "unanswered", clinic_id, troubles.unanswered, now)
    await ops_alerts.raise_alert(ctx.db, "uncertain_sends", clinic_id, troubles.uncertain_sends, now)
    await ops_alerts.raise_alert(ctx.db, "reminder_failed", clinic_id, troubles.failed_reminders, now)


async def clean_up(ctx: WorkerContext, clinic_id: UUID, now: datetime) -> None:
    """Runs once a day, in the run of the hour that is CLEANUP_HOUR in the clinic's time zone."""
    async with ctx.db.clinic_transaction(clinic_id) as conn:
        settings = await db.cleanup_settings(conn)
    if now.astimezone(clinic_zone(settings.timezone)).hour != CLEANUP_HOUR:
        return
    expired: list[str] = []
    async with ctx.db.clinic_transaction(clinic_id) as conn:
        if ctx.media_store is not None:
            expired = await db.expire_media(conn, now, MAX_FILES_PER_RUN)
        purged = await db.purge_webhook_payloads(conn, now - WEBHOOK_PAYLOAD_TTL, now)
        slots = await db.delete_old_offered_slots(conn, now - OFFERED_SLOTS_TTL)
    deleted, left = await _delete_files(ctx, clinic_id, expired)
    orphans = await _delete_orphans(ctx, clinic_id, now)
    log = logger.error if left else logger.info
    log(
        "jobs.cleanup_done",
        extra={
            "clinic_id": str(clinic_id),
            "media_expired": deleted,
            "media_left": left,
            "orphan_files": orphans,
            "webhook_payloads_purged": purged,
            "offered_slots_deleted": slots,
        },
    )


async def _delete_files(ctx: WorkerContext, clinic_id: UUID, paths: list[str]) -> tuple[int, int]:
    """Rows first, files after: a file no row points to is never shown again, and one that could
    not be deleted now is an orphan the next cleanup removes."""
    if ctx.media_store is None:
        return 0, 0
    deleted = left = 0
    for path in paths:
        try:
            await ctx.media_store.delete(clinic_id, path)
            deleted += 1
        except MediaStoreError:
            left += 1
    return deleted, left


async def _delete_orphans(ctx: WorkerContext, clinic_id: UUID, now: datetime) -> int:
    """Files of the clinic no message points to (left by an erasure or an expiry whose delete
    failed, or by a crash between storing a file and its row), older than a day."""
    if ctx.media_store is None:
        return 0
    try:
        stored = await ctx.media_store.list_older(clinic_id, now - ORPHAN_MIN_AGE, MAX_FILES_PER_RUN)
    except MediaStoreError:
        logger.error("jobs.cleanup_list_failed", extra={"clinic_id": str(clinic_id)})  # noqa: TRY400
        return 0
    async with ctx.db.clinic_transaction(clinic_id) as conn:
        used = await db.referenced_paths(conn, stored)
    orphans = [path for path in stored if path not in used]
    deleted, _ = await _delete_files(ctx, clinic_id, orphans)
    return deleted
