"""Processing of one stored webhook item: a patient's message, a delivery status or a coexistence
echo. Everything happens in one transaction with no outside calls, and the item is marked as
processed in it, so a job read twice changes nothing the second time."""

import logging
from datetime import datetime
from typing import Any
from uuid import UUID

from psycopg import AsyncConnection

from app.core.logs import mask_message_id
from app.db import conversations as db
from app.jobs.queue import ConversationJob, EventJob, enqueue
from app.jobs.timing import BURST_QUIET
from app.whatsapp.inbound import (
    EchoMessage,
    InboundMessage,
    StatusUpdate,
    UnreadableItemError,
    read_echo,
    read_message,
    read_status,
)
from app.whatsapp.window import META_STATUSES, status_advances

from .context import WorkerContext

logger = logging.getLogger(__name__)


class PermanentJobError(Exception):
    """Retrying will not help: the job goes straight to the dead-letter queue."""


async def handle_event(ctx: WorkerContext, job: EventJob) -> None:
    async with ctx.db.clinic_transaction(job.clinic_id) as conn:
        cursor = await conn.execute(
            """
            select kind, payload, processed_at, received_at from public.webhook_events
            where id = %s for update
            """,
            (job.event_id,),
        )
        row = await cursor.fetchone()
        if row is None or row[2] is not None:
            return
        kind, payload, _, received_at = row
        if payload is not None:
            await _process(conn, job.clinic_id, kind, payload, received_at)
        await conn.execute(
            "update public.webhook_events set processed_at = now() where id = %s", (job.event_id,)
        )


async def _process(
    conn: AsyncConnection, clinic_id: UUID, kind: str, payload: dict[str, Any], received_at: datetime
) -> None:
    try:
        match kind:
            case "message":
                await _patient_message(conn, clinic_id, read_message(payload, received_at))
            case "status":
                await _delivery_status(conn, read_status(payload, received_at))
            case "message_echo":
                await _echo(conn, clinic_id, read_echo(payload, received_at))
    except UnreadableItemError as exc:
        # The stored item will not change by retrying: the dead-letter queue keeps it for a person.
        raise PermanentJobError(str(exc)) from None


async def _patient_message(conn: AsyncConnection, clinic_id: UUID, message: InboundMessage) -> None:
    if message.in_group:
        logger.info("whatsapp.group_message_ignored", extra={"meta_id": mask_message_id(message.meta_id)})
        return
    if message.phone is None and message.bsuid is None:
        logger.error("whatsapp.message_without_sender", extra={"meta_id": mask_message_id(message.meta_id)})
        return
    patient_id = await db.find_or_create_patient(conn, clinic_id, message.phone, message.bsuid)
    conversation = await db.open_conversation(conn, clinic_id, patient_id)
    message_id = await db.insert_inbound(conn, clinic_id, conversation.id, message)
    if message_id is None:
        return  # already stored by an earlier copy of this message
    await db.record_patient_activity(conn, conversation.id, message.occurred_at)
    # Messages written from the panel while the window was closed go out now.
    await db.release_held(conn, conversation.id)
    # The turn runs once the patient has been quiet for a moment, so a burst gets one answer.
    await enqueue(
        conn,
        ConversationJob(clinic_id=clinic_id, conversation_id=conversation.id),
        delay_seconds=int(BURST_QUIET.total_seconds()),
    )
    logger.info(
        "whatsapp.message_received",
        extra={"message_id": str(message_id), "conversation_id": str(conversation.id), "type": message.type},
    )


async def _delivery_status(conn: AsyncConnection, update: StatusUpdate) -> None:
    if update.status not in META_STATUSES:
        return
    tracked = await db.message_for_status(conn, update.meta_id, _uuid(update.callback_data))
    if tracked is None:
        # Sent outside Axon's outbox (the Phase 0 test command) or already erased.
        logger.info("whatsapp.status_for_unknown_message", extra={"meta_id": mask_message_id(update.meta_id)})
        return
    advances = status_advances(tracked.delivery_status, update.status)
    await db.apply_status(
        conn,
        tracked.id,
        meta_message_id=update.meta_id,
        new_status=update.status if advances else None,
        error_code=update.error_code,
        billable=update.billable,
        billing_category=update.category,
    )
    if update.status == "failed" and advances:
        # Meta did not deliver it: someone should know, with the code only.
        logger.warning(
            "whatsapp.delivery_failed", extra={"message_id": str(tracked.id), "error_code": update.error_code}
        )


async def _echo(conn: AsyncConnection, clinic_id: UUID, echo: EchoMessage) -> None:
    """The clinic wrote from its WhatsApp Business app: the message is the team's, and the bot
    stays quiet in that chat for the clinic's configured time. It does not open the window."""
    if echo.phone is None:
        logger.error("whatsapp.echo_without_recipient", extra={"meta_id": mask_message_id(echo.meta_id)})
        return
    patient_id = await db.find_or_create_patient(conn, clinic_id, echo.phone, None)
    conversation = await db.open_conversation(conn, clinic_id, patient_id)
    if echo.action == "message" and await db.insert_echo(conn, clinic_id, conversation.id, echo) is None:
        return  # a message Axon itself sent, or a repeated echo
    pause = await db.human_reply_pause(conn)
    await db.pause_bot(conn, conversation.id, until=echo.occurred_at + pause, activity_at=echo.occurred_at)
    logger.info(
        "whatsapp.clinic_replied_from_app",
        extra={"conversation_id": str(conversation.id), "action": echo.action},
    )


def _uuid(value: str | None) -> UUID | None:
    try:
        return UUID(value) if value else None
    except ValueError:
        return None
