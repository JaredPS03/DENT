"""Jobs of the bot besides its turns (milestone 1C): alerting the clinic's team and erasing what a
patient who never accepted the privacy notice wrote."""

import logging
from datetime import timedelta
from uuid import UUID

from app.db import bot as data
from app.db.conversations import database_now
from app.db.media_store import MediaStoreError
from app.jobs.context import WorkerContext
from app.jobs.errors import RetryLaterError
from app.jobs.queue import EraseUnconsentedJob, TeamAlertJob, enqueue, erase_scheduled
from app.whatsapp import outbound
from app.whatsapp.client import (
    RECIPIENT_NOT_ALLOWED_CODE,
    NotSentError,
    OutcomeUnknownError,
    RecipientNotAllowedError,
    WhatsAppApiError,
)

logger = logging.getLogger(__name__)

ALERT_TEMPLATE = "alerta_equipo"
ALERT_LANGUAGE = "es_MX"
PRIORITY_WORDS = {"urgent": "urgente", "normal": "normal"}
CONSENT_WINDOW = timedelta(hours=24)
MAX_TEMPLATE_NAME = 60


async def handle_team_alert(ctx: WorkerContext, job: TeamAlertJob) -> None:
    """Sends alerta_equipo from Axon's number to each team member with a phone for alerts. It
    carries the member's name, the clinic's name, the priority and the link to the conversation:
    never patient data. Each alert goes out at most once (team_alerts)."""
    phone_number_id = ctx.settings.axon_phone_number_id
    if phone_number_id is None:
        logger.warning("bot.team_alert_not_configured", extra={"escalation_id": str(job.escalation_id)})
        return
    while True:
        async with ctx.db.clinic_transaction(job.clinic_id) as conn:
            alert = await data.claim_team_alert(conn, job.escalation_id)
        if alert is None:
            return
        message = outbound.template(
            ALERT_TEMPLATE,
            ALERT_LANGUAGE,
            [
                _one_line(alert.member_name) or "equipo",
                _one_line(alert.clinic_name) or "el consultorio",
                PRIORITY_WORDS.get(alert.priority, "normal"),
            ],
            url_button_suffix=str(alert.conversation_id),
        )
        meta_id: str | None = None
        error_code: int | None = None
        try:
            sent = await ctx.whatsapp.send(
                phone_number_id, alert.phone, None, message, callback_data=f"alert:{alert.id}"
            )
            meta_id = sent.meta_message_id
        except RecipientNotAllowedError:
            error_code = RECIPIENT_NOT_ALLOWED_CODE
        except NotSentError:
            async with ctx.db.clinic_transaction(job.clinic_id) as conn:
                await data.release_team_alert(conn, alert.id)
            raise RetryLaterError("alert not sent") from None
        except WhatsAppApiError as error:
            if error.retryable:
                async with ctx.db.clinic_transaction(job.clinic_id) as conn:
                    await data.release_team_alert(conn, alert.id)
                raise RetryLaterError(f"meta {error.meta_code}", error.retry_after) from None
            error_code = error.meta_code or error.http_status
        except OutcomeUnknownError:
            pass  # stays unknown: it may have gone out, so it is never sent again
        async with ctx.db.clinic_transaction(job.clinic_id) as conn:
            await data.finish_team_alert(conn, alert.id, meta_message_id=meta_id, error_code=error_code)
        log = logger.info if meta_id else logger.error
        log(
            "bot.team_alert",
            extra={"alert_id": str(alert.id), "sent": meta_id is not None, "error_code": error_code},
        )


async def handle_erase_unconsented(ctx: WorkerContext, job: EraseUnconsentedJob) -> None:
    """Erases what a patient who never accepted the notice wrote more than 24 h ago, and comes
    back for what is newer. Rows first, then files: once a row no longer points to a file, the
    file is never shown again, and a patient who taps Acepto meanwhile keeps everything (the rows
    are erased only under the lock of the patient's row, after checking again)."""
    async with ctx.db.clinic_transaction(job.clinic_id) as conn:
        now = await database_now(conn)
        paths = await data.erase_unconsented(conn, job.conversation_id, now - CONSENT_WINDOW)
        oldest = await data.oldest_unerased_unconsented(conn, job.conversation_id)
        if oldest is not None and not await erase_scheduled(conn, job.conversation_id):
            delay = (oldest + CONSENT_WINDOW - now).total_seconds() + 60
            await enqueue(conn, job, delay_seconds=max(60, int(delay)))
    files = [path for path in paths if path]
    left = 0
    for path in files:
        try:
            await _delete_file(ctx, job.clinic_id, path)
        except MediaStoreError:
            # No row points to it any more and its path carries ids only; the Storage cleanup
            # of milestone 1D removes files without a message.
            left += 1
    log = logger.error if left else logger.info
    log(
        "bot.unconsented_erased",
        extra={
            "conversation_id": str(job.conversation_id),
            "messages": len(paths),
            "files": len(files),
            "files_left": left,
        },
    )


async def _delete_file(ctx: WorkerContext, clinic_id: UUID, path: str) -> None:
    if ctx.media_store is None:
        raise MediaStoreError("storage not configured")
    await ctx.media_store.delete(clinic_id, path)


def _one_line(text: str | None) -> str | None:
    """Template parameters cannot carry new lines, tabs or be too long."""
    if not text:
        return None
    single = " ".join(text.split())
    return single[:MAX_TEMPLATE_NAME] or None
