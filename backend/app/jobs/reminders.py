"""Reminders 24 h before each appointment (milestone 1D, spec function 4).

Every 15 minutes the scheduler gives each clinic a run. It queues recordatorio_cita in the
conversation's outbox, which sends it at most once, for each active appointment of the next 24 h
that has no reminder for its current time. The reminder row is unique per appointment and time,
so a retried or repeated run, or two workers, never queue two.
"""

import logging
from datetime import datetime, timedelta
from uuid import UUID, uuid4

from app.db import conversations
from app.db import reminders as db
from app.jobs import ops_alerts
from app.jobs.context import WorkerContext
from app.jobs.queue import ConversationJob, enqueue
from app.jobs.timing import clinic_zone
from app.whatsapp import outbound, templates

logger = logging.getLogger(__name__)

LEAD = timedelta(hours=24)
# More than any clinic books in 15 minutes; the next run takes the rest.
MAX_PER_RUN = 200


async def queue_reminders(ctx: WorkerContext, clinic_id: UUID, now: datetime) -> int:
    """Queues the reminders that are due. Returns how many."""
    queued = too_late = without_template = declined = 0
    async with ctx.db.clinic_transaction(clinic_id) as conn:
        clinic = await db.reminder_clinic(conn)
        if clinic.phone_number_id is None:
            return 0  # a clinic without its WhatsApp number sends nothing yet
        due = await db.due_appointments(conn, now, LEAD, MAX_PER_RUN)
        if not due:
            return 0
        template = await conversations.approved_template(conn, templates.REMINDER, "utility")
        tz = clinic_zone(clinic.timezone)
        tomorrow = now.astimezone(tz).date() + timedelta(days=1)
        for appointment in due:
            local = appointment.starts_at.astimezone(tz)
            if appointment.declined:
                # They were told the assistant would not write to them again.
                declined += await db.insert_reminder(
                    conn,
                    reminder_id=uuid4(),
                    clinic_id=clinic_id,
                    appointment=appointment,
                    status="skipped",
                    skip_reason="declined",
                )
                continue
            if local.date() != tomorrow:
                # The template says "mañana": a reminder that comes too late (the worker was down)
                # is not sent, and Jared hears of it.
                too_late += await db.insert_reminder(
                    conn,
                    reminder_id=uuid4(),
                    clinic_id=clinic_id,
                    appointment=appointment,
                    status="skipped",
                    skip_reason="too_late",
                )
                continue
            if template is None:
                # Not approved by Meta, or moved to marketing: tried again on the next run.
                without_template += 1
                continue
            reminder_id = uuid4()
            if not await db.insert_reminder(
                conn, reminder_id=reminder_id, clinic_id=clinic_id, appointment=appointment, status="queued"
            ):
                continue  # another run claimed it first
            parameters = templates.reminder_parameters(
                patient_name=appointment.patient_name,
                service_name=appointment.service_name,
                local_start=local,
                clinic_name=clinic.name,
            )
            conversation = await conversations.open_conversation(conn, clinic_id, appointment.patient_id)
            message = await conversations.insert_outbound(
                conn,
                clinic_id=clinic_id,
                conversation_id=conversation.id,
                sender="system",
                message_type="template",
                content=templates.fill(templates.REMINDER_BODY, parameters),
                send_payload=outbound.template(template.name, template.language, parameters),
                billing_category=template.category,
                delivery_status="pending",
                idempotency_key=f"reminder:{appointment.id}:{int(appointment.starts_at.timestamp())}",
            )
            await db.attach_message(conn, reminder_id, message.id)
            await enqueue(conn, ConversationJob(clinic_id=clinic_id, conversation_id=conversation.id))
            queued += 1
    logger.info(
        "reminders.queued",
        extra={
            "clinic_id": str(clinic_id),
            "queued": queued,
            "too_late": too_late,
            "without_template": without_template,
            "declined": declined,
        },
    )
    if too_late or without_template:
        await ops_alerts.raise_alert(ctx.db, "reminder_failed", clinic_id, too_late + without_template, now)
    return queued
