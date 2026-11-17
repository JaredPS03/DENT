"""Reminders 24 h before each appointment and the taps on their buttons (milestone 1D).

Every function runs inside a clinic transaction (Database.clinic_transaction): RLS shows only the
clinic fixed in it.
"""

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Literal
from uuid import UUID

from psycopg import AsyncConnection

ReminderStatus = Literal["queued", "voided", "skipped"]


@dataclass(frozen=True, slots=True)
class ReminderClinic:
    name: str
    timezone: str
    phone_number_id: str | None


async def reminder_clinic(conn: AsyncConnection) -> ReminderClinic:
    cursor = await conn.execute("select name, timezone, whatsapp_phone_number_id from public.clinics")
    row = await cursor.fetchone()
    if row is None:
        raise LookupError("the clinic of the transaction is not there")
    return ReminderClinic(*row)


@dataclass(frozen=True, slots=True)
class DueAppointment:
    id: UUID
    patient_id: UUID
    patient_name: str | None
    service_name: str
    starts_at: datetime


async def due_appointments(
    conn: AsyncConnection, now: datetime, lead: timedelta, limit: int
) -> list[DueAppointment]:
    """Active appointments starting within `lead` with no reminder for their current time yet, and
    whose time was set at least `lead` before it: one booked or moved later gets none, because
    the patient just got the confirmation. Locked, so a second run waits instead of repeating."""
    cursor = await conn.execute(
        """
        select a.id, a.patient_id, p.name, s.name, a.starts_at
        from public.appointments a
        join public.patients p on p.id = a.patient_id
        join public.services s on s.id = a.service_id
        where a.status in ('pending', 'confirmed')
          and a.starts_at > %(now)s and a.starts_at <= %(now)s + %(lead)s
          and a.time_set_at <= a.starts_at - %(lead)s
          and not exists (
            select 1 from public.appointment_reminders r
            where r.appointment_id = a.id and r.starts_at = a.starts_at
          )
        order by a.starts_at, a.id
        limit %(limit)s
        for update of a skip locked
        """,
        {"now": now, "lead": lead, "limit": limit},
    )
    return [DueAppointment(*row) for row in await cursor.fetchall()]


async def insert_reminder(  # noqa: PLR0913 - one value per column
    conn: AsyncConnection,
    *,
    reminder_id: UUID,
    clinic_id: UUID,
    appointment: DueAppointment,
    status: ReminderStatus,
    skip_reason: str | None = None,
    message_id: UUID | None = None,
) -> None:
    await conn.execute(
        """
        insert into public.appointment_reminders
          (id, clinic_id, appointment_id, starts_at, status, skip_reason, message_id)
        values (%s, %s, %s, %s, %s, %s, %s)
        """,
        (reminder_id, clinic_id, appointment.id, appointment.starts_at, status, skip_reason, message_id),
    )


async def void_if_stale(conn: AsyncConnection, message_id: UUID) -> bool:
    """Right before a queued template goes to Meta: if it is a reminder and its appointment was
    cancelled or moved meanwhile, it is voided instead of sent."""
    cursor = await conn.execute(
        """
        update public.appointment_reminders r set status = 'voided'
        from public.appointments a
        where r.message_id = %s and r.status = 'queued' and a.id = r.appointment_id
          and (a.status not in ('pending', 'confirmed') or a.starts_at <> r.starts_at)
        returning r.id
        """,
        (message_id,),
    )
    return await cursor.fetchone() is not None


# Taps on the buttons ------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class TappedReminder:
    id: UUID
    # The time the reminder announced.
    reminded_at: datetime
    appointment_id: UUID
    service_id: UUID
    service_name: str
    status: str
    starts_at: datetime


_TAPPED_COLUMNS = """
    r.id, r.starts_at, a.id, a.service_id, s.name, a.status, a.starts_at
"""


async def reminder_answered(
    conn: AsyncConnection, conversation_id: UUID, context_meta_message_id: str | None, since: datetime
) -> TappedReminder | None:
    """The reminder a tap on a template button answers: the one whose message has the wamid the tap
    points to. If Meta's id of that message is unknown (its send outcome was lost), the latest
    reminder sent in this conversation since `since`. Never one of another conversation."""
    if context_meta_message_id is not None:
        cursor = await conn.execute(
            f"""
            select {_TAPPED_COLUMNS}
            from public.appointment_reminders r
            join public.messages m on m.id = r.message_id
            join public.appointments a on a.id = r.appointment_id
            join public.services s on s.id = a.service_id
            where m.meta_message_id = %s and m.conversation_id = %s
            """,  # noqa: S608 - fixed column list
            (context_meta_message_id, conversation_id),
        )
        row = await cursor.fetchone()
        if row is not None:
            return TappedReminder(*row)
    cursor = await conn.execute(
        f"""
        select {_TAPPED_COLUMNS}
        from public.appointment_reminders r
        join public.messages m on m.id = r.message_id
        join public.appointments a on a.id = r.appointment_id
        join public.services s on s.id = a.service_id
        where m.conversation_id = %s and r.status = 'queued' and r.created_at >= %s
          and m.delivery_status <> 'failed'
        order by r.created_at desc, r.id desc
        limit 1
        """,  # noqa: S608 - fixed column list
        (conversation_id, since),
    )
    row = await cursor.fetchone()
    return TappedReminder(*row) if row else None


async def reminder_in_conversation(
    conn: AsyncConnection, conversation_id: UUID, reminder_id: UUID
) -> TappedReminder | None:
    """The reminder behind a slot button of "Cambiar", only if it was sent in this conversation."""
    cursor = await conn.execute(
        f"""
        select {_TAPPED_COLUMNS}
        from public.appointment_reminders r
        join public.messages m on m.id = r.message_id
        join public.appointments a on a.id = r.appointment_id
        join public.services s on s.id = a.service_id
        where r.id = %s and m.conversation_id = %s
        """,  # noqa: S608 - fixed column list
        (reminder_id, conversation_id),
    )
    row = await cursor.fetchone()
    return TappedReminder(*row) if row else None


@dataclass(frozen=True, slots=True)
class ShownSlot:
    provider_id: UUID
    starts_at: datetime


async def shown_slot(
    conn: AsyncConnection, conversation_id: UUID, slot_key: str, service_id: UUID
) -> ShownSlot | None:
    """A slot this conversation showed the patient for that service, in a message that reached
    WhatsApp."""
    cursor = await conn.execute(
        """
        select o.provider_id, o.starts_at
        from public.offered_slots o
        join public.messages m on m.id = o.shown_in_message_id
        where o.conversation_id = %s and o.slot_key = %s and o.service_id = %s
          and m.delivery_status in ('sent', 'delivered', 'read')
        """,
        (conversation_id, slot_key, service_id),
    )
    row = await cursor.fetchone()
    return ShownSlot(*row) if row else None
