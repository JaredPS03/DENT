"""Operation alerts for Jared (milestone 1D; decision 001): a patient's message without an answer,
a reminder that did not go out, Meta's notices about templates, the number's quality or the
account, jobs that exhausted their retries and sends with an unknown outcome.

Each alert goes to Sentry (an error-level log line, the backup by email) and by WhatsApp from
Axon's number with the template alerta_axon. At most one per kind and clinic each hour, with how
many cases there were. Alerts carry the kind, the clinic's name and a count: never patient data.
"""

import logging
from datetime import datetime, timedelta
from typing import TYPE_CHECKING
from uuid import UUID

from app.db import claims
from app.db.connection import Database
from app.jobs.errors import RetryLaterError
from app.jobs.queue import OpsAlertJob, OpsAlertKind, enqueue
from app.jobs.timing import slot_of
from app.whatsapp import outbound, templates
from app.whatsapp.client import NotSentError, OutcomeUnknownError, RecipientNotAllowedError, WhatsAppApiError

if TYPE_CHECKING:
    from app.jobs.context import WorkerContext

logger = logging.getLogger(__name__)

EVERY = timedelta(hours=1)

# {{1}} of alerta_axon.
PHRASES: dict[OpsAlertKind, str] = {
    "unanswered": "mensajes de pacientes sin respuesta",
    "uncertain_sends": "envíos con resultado incierto",
    "reminder_failed": "recordatorios que no salieron",
    "template": "plantillas rechazadas, pausadas o cambiadas de categoría",
    "quality": "baja de calidad o de límite del número",
    "account": "avisos de restricción de la cuenta de WhatsApp",
    "dead_letters": "trabajos fallidos en la cola",
}


async def raise_alert(
    db: Database, alert: OpsAlertKind, clinic_id: UUID | None, count: int, now: datetime
) -> bool:
    """Queues the alert unless one of its kind for this clinic (or for Axon) already went this
    hour. True if it was queued."""
    if count < 1:
        return False
    async with db.unscoped_transaction() as conn:
        if not await claims.claim(conn, f"ops:{alert}:{clinic_id or 'axon'}", slot_of(now, EVERY)):
            return False
        await enqueue(conn, OpsAlertJob(alert=alert, clinic_id=clinic_id, count=count))
    logger.error(
        "ops.alert",
        extra={"alert": alert, "clinic_id": str(clinic_id) if clinic_id else None, "count": count},
    )
    return True


async def handle_ops_alert(ctx: "WorkerContext", job: OpsAlertJob) -> None:
    """Sends alerta_axon to Jared from Axon's number. Without that number or Jared's phone the
    alert stays in Sentry only."""
    phone, phone_number_id = ctx.settings.ops_alert_phone, ctx.settings.axon_phone_number_id
    if phone is None or phone_number_id is None:
        logger.warning("ops.alert_not_configured", extra={"alert": job.alert})
        return
    where = "Axon"
    if job.clinic_id is not None:
        async with ctx.db.clinic_transaction(job.clinic_id) as conn:
            cursor = await conn.execute("select name from public.clinics")
            row = await cursor.fetchone()
        where = templates.one_line(row[0] if row else None) or "un consultorio"
    message = outbound.template(
        templates.OPS_ALERT, templates.LANGUAGE, [PHRASES[job.alert], where, str(job.count)]
    )
    try:
        await ctx.whatsapp.send(phone_number_id, phone, None, message, callback_data=f"ops:{job.alert}")
    except RecipientNotAllowedError:
        logger.warning("ops.alert_recipient_not_allowed", extra={"alert": job.alert})
    except NotSentError:
        raise RetryLaterError("ops alert not sent") from None
    except WhatsAppApiError as error:
        if error.retryable:
            raise RetryLaterError(f"meta {error.meta_code}", error.retry_after) from None
        logger.error(  # noqa: TRY400 - codes only
            "ops.alert_rejected", extra={"alert": job.alert, "error_code": error.meta_code}
        )
    except OutcomeUnknownError:
        # Maybe sent: never again. Sentry already has it.
        logger.warning("ops.alert_outcome_unknown", extra={"alert": job.alert})
    else:
        logger.info("ops.alert_sent", extra={"alert": job.alert})
