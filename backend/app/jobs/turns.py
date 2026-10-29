"""A turn of one conversation: send what is queued, then answer what the patient wrote.

Only one worker at a time works on a conversation, holding its turn lease (two columns of
`conversations`, taken and released in short transactions). No transaction stays open while
waiting on Meta, Storage or the transcription. If a worker dies, the lease expires on its own.

Sending is at most once: a message is claimed (send_started_at) right before calling Meta. If
Meta never got it, the claim is undone and a retry sends it; if the answer was lost, it is left
claimed and alerted, and its status webhook settles it later through biz_opaque_callback_data.
"""

import logging
import math
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, replace
from datetime import datetime
from uuid import UUID, uuid4

from app.bot.echo import echo_reply
from app.bot.transcription import TranscriptionFailedError, TranscriptionUnavailableError
from app.core.config import Settings
from app.db import conversations as db
from app.db.media_store import MediaStoreError, media_path
from app.jobs.context import WorkerContext
from app.jobs.queue import ConversationJob, ExpireHoldJob, enqueue
from app.jobs.timing import burst_ready, seconds_until_ready
from app.whatsapp import outbound
from app.whatsapp.client import (
    RECIPIENT_NOT_ALLOWED_CODE,
    NotSentError,
    OutcomeUnknownError,
    RecipientNotAllowedError,
    WhatsAppApiError,
    ensure_recipient_allowed,
)
from app.whatsapp.media import (
    EXTENSIONS,
    MediaRejectedError,
    MediaUnavailableError,
    base_mime_type,
    is_downloadable,
)
from app.whatsapp.window import WINDOW_CLOSED_CODE, window_is_open

logger = logging.getLogger(__name__)

# Longer than the slowest step of a turn; renewed before every call outside.
LEASE_SECONDS = 120
# Another worker holds the conversation: look again shortly.
LEASE_BUSY_RETRY_SECONDS = 2

KeepAlive = Callable[[], Awaitable[None]]


class LeaseLostError(Exception):
    """The lease expired and another worker may have taken over: stop and retry later."""


class RetryLaterError(Exception):
    """Something outside (Meta, Storage, the transcription) failed for now."""

    def __init__(self, reason: str, retry_after: float | None = None) -> None:
        super().__init__(reason)
        self.retry_after = retry_after


@dataclass(frozen=True, slots=True)
class _Lease:
    clinic_id: UUID
    conversation_id: UUID
    token: UUID


async def handle_conversation(
    ctx: WorkerContext, job: ConversationJob, *, final_attempt: bool, keep_alive: KeepAlive
) -> None:
    lease = _Lease(job.clinic_id, job.conversation_id, uuid4())
    async with ctx.db.clinic_transaction(lease.clinic_id) as conn:
        outcome = await db.acquire_lease(conn, lease.conversation_id, lease.token, LEASE_SECONDS)
        if outcome == "busy":
            # Never dropped: the worker holding it may already be past the message that brought
            # this job. A new job (not a retry) comes back shortly.
            await enqueue(conn, job, delay_seconds=LEASE_BUSY_RETRY_SECONDS)
    if outcome != "acquired":
        return

    async def renew() -> None:
        await keep_alive()
        async with ctx.db.clinic_transaction(lease.clinic_id) as conn:
            if not await db.renew_lease(conn, lease.conversation_id, lease.token, LEASE_SECONDS):
                raise LeaseLostError

    try:
        await _flush_outbox(ctx, lease, renew)
        if await _answer(ctx, lease, renew, final_attempt=final_attempt):
            await _flush_outbox(ctx, lease, renew)
    finally:
        try:
            async with ctx.db.clinic_transaction(lease.clinic_id) as conn:
                await db.release_lease(conn, lease.conversation_id, lease.token)
        except Exception:
            # It expires on its own.
            logger.warning("jobs.lease_release_failed", extra={"conversation_id": str(lease.conversation_id)})


async def handle_expire_hold(ctx: WorkerContext, job: ExpireHoldJob) -> None:
    async with ctx.db.clinic_transaction(job.clinic_id) as conn:
        if await db.expire_held(conn, job.message_id):
            logger.info("whatsapp.held_message_expired", extra={"message_id": str(job.message_id)})


# Answering the patient -------------------------------------------------------------------------


async def _answer(ctx: WorkerContext, lease: _Lease, renew: KeepAlive, *, final_attempt: bool) -> bool:
    """Takes care of the patient's pending messages once their burst is over. True if a reply was
    queued."""
    async with ctx.db.clinic_transaction(lease.clinic_id) as conn:
        batch, now = await db.unhandled_inbound(conn, lease.conversation_id)
    if not batch:
        return False
    arrivals = [message.created_at for message in batch]
    if not burst_ready(min(arrivals), max(arrivals), now):
        # The patient is still typing: come back when the burst is over. The latest message's own
        # job is due then too; whichever runs second finds nothing left to answer.
        wait = seconds_until_ready(min(arrivals), max(arrivals), now)
        async with ctx.db.clinic_transaction(lease.clinic_id) as conn:
            await enqueue(
                conn,
                ConversationJob(clinic_id=lease.clinic_id, conversation_id=lease.conversation_id),
                delay_seconds=math.ceil(wait),
            )
        return False

    batch = [
        await _prepare_media(ctx, lease, message, renew, final_attempt=final_attempt) for message in batch
    ]

    async with ctx.db.clinic_transaction(lease.clinic_id) as conn:
        if not await db.holds_lease(conn, lease.conversation_id, lease.token):
            raise LeaseLostError
        conversation = await db.conversation_for_update(conn, lease.conversation_id)
        bot = await db.bot_settings(conn)
        reply: str | None = None
        if conversation is not None and _bot_answers(ctx.settings, conversation, bot, now=now):
            reply = echo_reply(batch)
            await db.insert_outbound(
                conn,
                clinic_id=lease.clinic_id,
                conversation_id=lease.conversation_id,
                sender="bot",
                message_type="text",
                content=reply,
                send_payload=outbound.text(reply),
                billing_category="service",
                delivery_status="pending",
                # One answer per batch, even if this turn is retried.
                idempotency_key=f"reply:{max(batch, key=lambda message: message.created_at).id}",
            )
        await db.mark_handled(conn, [message.id for message in batch])
    logger.info(
        "jobs.turn_done",
        extra={
            "conversation_id": str(lease.conversation_id),
            "messages": len(batch),
            "answered": reply is not None,
        },
    )
    return reply is not None


def _bot_answers(
    settings: Settings, conversation: db.Conversation, bot: db.BotSettings, *, now: datetime
) -> bool:
    """Bot rule 3: off for the clinic, paused in this chat or taken by a person, the message only
    reaches the inbox. Until milestone 1C the only bot is the echo of milestone 1B."""
    paused = conversation.bot_paused_until is not None and conversation.bot_paused_until > now
    return (
        settings.echo_bot_enabled and bot.clinic_bot_enabled and conversation.status == "bot" and not paused
    )


async def _prepare_media(
    ctx: WorkerContext, lease: _Lease, message: db.PendingMessage, renew: KeepAlive, *, final_attempt: bool
) -> db.PendingMessage:
    """Brings the file from Meta to the private bucket and transcribes voice notes. A file that can
    never be fetched is given up; a temporary failure retries the turn, except on its last attempt,
    when the patient gets an answer without it."""
    content: bytes | None = None
    if message.meta_media_id is not None and message.media_path is None:
        if ctx.media_source is None or ctx.media_store is None:
            return message  # this environment keeps media at Meta
        if not is_downloadable(message.media_mime_type):
            await _set_media(ctx, lease, message.id, None)
            return replace(message, meta_media_id=None)
        await renew()
        try:
            media = await ctx.media_source.download(message.meta_media_id)
            path = media_path(lease.clinic_id, lease.conversation_id, message.id, media.extension)
            await ctx.media_store.put(path, media.content, media.mime_type)
        except MediaRejectedError as rejected:
            logger.warning(
                "whatsapp.media_rejected", extra={"message_id": str(message.id), "reason": str(rejected)}
            )
            await _set_media(ctx, lease, message.id, None)
            return replace(message, meta_media_id=None)
        except (MediaUnavailableError, MediaStoreError) as failure:
            if final_attempt:
                # Without exc_info on purpose: the error's text could quote the request.
                logger.error("whatsapp.media_given_up", extra={"message_id": str(message.id)})  # noqa: TRY400
                return message
            raise RetryLaterError(type(failure).__name__) from None
        await _set_media(ctx, lease, message.id, path)
        message = replace(message, meta_media_id=None, media_path=path)
        content = media.content

    if message.type == "audio" and message.transcription is None and message.media_path and ctx.transcriber:
        return await _transcribe(ctx, lease, message, content, renew, final_attempt=final_attempt)
    return message


async def _transcribe(  # noqa: PLR0913 - the turn's state, passed explicitly
    ctx: WorkerContext,
    lease: _Lease,
    message: db.PendingMessage,
    content: bytes | None,
    renew: KeepAlive,
    *,
    final_attempt: bool,
) -> db.PendingMessage:
    assert ctx.transcriber is not None  # noqa: S101 - checked by the caller
    assert message.media_path is not None  # noqa: S101
    mime_type = base_mime_type(message.media_mime_type) or "audio/ogg"
    extension = EXTENSIONS.get(mime_type, "ogg")
    await renew()
    try:
        if content is None and ctx.media_store is not None:
            content = await ctx.media_store.get(message.media_path)
        if content is None:
            return message
        text = await ctx.transcriber.transcribe(content, mime_type, extension)
    except TranscriptionFailedError as failed:
        logger.warning(
            "bot.transcription_failed", extra={"message_id": str(message.id), "reason": str(failed)}
        )
        return message
    except (TranscriptionUnavailableError, MediaStoreError) as failure:
        if final_attempt:
            logger.error("bot.transcription_given_up", extra={"message_id": str(message.id)})  # noqa: TRY400
            return message
        raise RetryLaterError(type(failure).__name__) from None
    if not text:
        return message
    async with ctx.db.clinic_transaction(lease.clinic_id) as conn:
        await db.set_transcription(conn, message.id, text)
    return replace(message, transcription=text)


async def _set_media(ctx: WorkerContext, lease: _Lease, message_id: UUID, path: str | None) -> None:
    async with ctx.db.clinic_transaction(lease.clinic_id) as conn:
        await db.set_media_stored(conn, message_id, path)


# Sending -----------------------------------------------------------------------------------------


async def _flush_outbox(ctx: WorkerContext, lease: _Lease, renew: KeepAlive) -> None:
    """Sends the conversation's queued messages in order, each at most once."""
    while True:
        async with ctx.db.clinic_transaction(lease.clinic_id) as conn:
            if not await db.holds_lease(conn, lease.conversation_id, lease.token):
                raise LeaseLostError
            item = await db.next_outbound(conn, lease.conversation_id)
            if item is None:
                return
            refusal = _refusal(ctx.settings, item)
            if refusal is not None:
                code, reason = refusal
                await db.mark_failed(conn, item.id, code)
                logger.warning("whatsapp.send_refused", extra={"message_id": str(item.id), "reason": reason})
                continue
            await db.mark_send_started(conn, item.id)

        await renew()
        assert item.phone_number_id is not None  # noqa: S101 - checked by _refusal
        assert item.send_payload is not None  # noqa: S101
        try:
            sent = await ctx.whatsapp.send(
                item.phone_number_id,
                item.patient_phone,
                item.patient_bsuid,
                item.send_payload,
                callback_data=str(item.id),
            )
        except WhatsAppApiError as error:
            if error.retryable:
                await _not_sent(ctx, lease, item.id)
                raise RetryLaterError(f"meta {error.meta_code}", error.retry_after) from None
            await _failed(ctx, lease, item.id, error.meta_code)
            # A data error (4xx): logged and alerted, never retried.
            logger.error(  # noqa: TRY400 - status and code only
                "whatsapp.send_rejected",
                extra={
                    "message_id": str(item.id),
                    "http_status": error.http_status,
                    "error_code": error.meta_code,
                },
            )
        except NotSentError as error:
            await _not_sent(ctx, lease, item.id)
            raise RetryLaterError(str(error)) from None
        except OutcomeUnknownError as error:
            # Maybe sent: never again. Its status webhook will tell.
            logger.error(  # noqa: TRY400 - ids only
                "whatsapp.send_outcome_unknown", extra={"message_id": str(item.id), "reason": str(error)}
            )
        except RecipientNotAllowedError:
            await _failed(ctx, lease, item.id, RECIPIENT_NOT_ALLOWED_CODE)
        else:
            async with ctx.db.clinic_transaction(lease.clinic_id) as conn:
                await db.mark_sent(conn, item.id, sent.meta_message_id)
            logger.info("whatsapp.message_sent", extra={"message_id": str(item.id), "type": item.type})


def _refusal(settings: Settings, item: db.OutboxItem) -> tuple[int | None, str] | None:
    """Why a queued message cannot go out, checked before calling Meta."""
    if item.phone_number_id is None:
        return None, "clinic_without_whatsapp_number"
    if item.send_payload is None:
        return None, "nothing_to_send"
    if item.type != "template" and not window_is_open(item.window_expires_at, item.now):
        return WINDOW_CLOSED_CODE, "window_closed"
    try:
        ensure_recipient_allowed(settings, item.patient_phone)
    except RecipientNotAllowedError:
        return RECIPIENT_NOT_ALLOWED_CODE, "recipient_not_allowed"
    return None


async def _not_sent(ctx: WorkerContext, lease: _Lease, message_id: UUID) -> None:
    async with ctx.db.clinic_transaction(lease.clinic_id) as conn:
        await db.mark_not_sent(conn, message_id)


async def _failed(ctx: WorkerContext, lease: _Lease, message_id: UUID, code: int | None) -> None:
    async with ctx.db.clinic_transaction(lease.clinic_id) as conn:
        await db.mark_failed(conn, message_id, code)
