"""SQL of the agenda. Every function takes a connection from Database.clinic_transaction, so RLS
already limits it to that clinic; nothing here receives a clinic id it did not get from there."""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any, cast
from uuid import UUID
from zoneinfo import ZoneInfo

from psycopg import AsyncConnection
from psycopg.types.json import Jsonb

from app.scheduling.availability import Interval, WeeklyHours
from app.scheduling.connector import Actor, Appointment, AppointmentStatus

_APPOINTMENT_COLUMNS = "id, patient_id, service_id, provider_id, starts_at, ends_at, status"


@dataclass(frozen=True, slots=True)
class Service:
    id: UUID
    duration_minutes: int
    reference_price_cents: int | None
    active: bool


async def clinic_timezone(conn: AsyncConnection) -> ZoneInfo:
    cursor = await conn.execute("select timezone from public.clinics")
    row = await cursor.fetchone()
    if row is None:
        raise LookupError("no clinic is fixed in this transaction")
    return ZoneInfo(row[0])


async def get_service(conn: AsyncConnection, service_id: UUID) -> Service | None:
    cursor = await conn.execute(
        "select id, duration_minutes, reference_price_cents, active from public.services where id = %s",
        (service_id,),
    )
    row = await cursor.fetchone()
    return Service(*row) if row else None


async def lock_patient(conn: AsyncConnection, patient_id: UUID) -> bool:
    """Serializes the bookings of one patient, so a retried request cannot give them two
    appointments at once with different providers. False if the patient is not in this clinic."""
    cursor = await conn.execute(
        "select 1 from public.patients where id = %s for no key update", (patient_id,)
    )
    return await cursor.fetchone() is not None


async def patient_has_appointment_between(
    conn: AsyncConnection, patient_id: UUID, start: datetime, end: datetime, *, exclude_id: UUID | None = None
) -> bool:
    cursor = await conn.execute(
        """
        select 1 from public.appointments
        where patient_id = %s and status in ('pending', 'confirmed')
          and starts_at < %s and ends_at > %s and id is distinct from %s
        limit 1
        """,
        (patient_id, end, start, exclude_id),
    )
    return await cursor.fetchone() is not None


async def active_provider_ids(conn: AsyncConnection) -> list[UUID]:
    cursor = await conn.execute("select id from public.providers where active order by created_at, id")
    return [row[0] for row in await cursor.fetchall()]


async def lock_provider(conn: AsyncConnection, provider_id: UUID) -> bool:
    """Serializes bookings of one provider until the transaction ends, so checking that a slot
    is free and inserting the appointment happen as one step (the buffer included)."""
    cursor = await conn.execute(
        "select id from public.providers where id = %s and active for update", (provider_id,)
    )
    return await cursor.fetchone() is not None


async def weekly_hours(conn: AsyncConnection, provider_ids: Sequence[UUID]) -> list[WeeklyHours]:
    cursor = await conn.execute(
        """
        select provider_id, weekday, start_time, end_time, break_start, break_end, buffer_minutes
        from public.schedules
        where provider_id = any(%s)
        """,
        (list(provider_ids),),
    )
    return [WeeklyHours(*row) for row in await cursor.fetchall()]


async def exceptions_between(conn: AsyncConnection, start: datetime, end: datetime) -> list[Interval]:
    cursor = await conn.execute(
        """
        select starts_at, ends_at, provider_id from public.schedule_exceptions
        where starts_at < %s and ends_at > %s
        """,
        (end, start),
    )
    return [Interval(*row) for row in await cursor.fetchall()]


async def active_appointments_between(
    conn: AsyncConnection, start: datetime, end: datetime, *, exclude_id: UUID | None = None
) -> list[Interval]:
    cursor = await conn.execute(
        """
        select starts_at, ends_at, provider_id from public.appointments
        where status in ('pending', 'confirmed') and starts_at < %s and ends_at > %s
          and id is distinct from %s
        """,
        (end, start, exclude_id),
    )
    return [Interval(*row) for row in await cursor.fetchall()]


async def insert_appointment(  # noqa: PLR0913 - one value per column
    conn: AsyncConnection,
    *,
    clinic_id: UUID,
    patient_id: UUID,
    service_id: UUID,
    provider_id: UUID,
    starts_at: datetime,
    ends_at: datetime,
    actor: Actor,
) -> Appointment:
    cursor = await conn.execute(
        f"""
        insert into public.appointments
          (clinic_id, patient_id, service_id, provider_id, starts_at, ends_at, created_by, created_by_user_id)
        values (%s, %s, %s, %s, %s, %s, %s, %s)
        returning {_APPOINTMENT_COLUMNS}
        """,  # noqa: S608 - fixed column list, values are parameters
        (
            clinic_id,
            patient_id,
            service_id,
            provider_id,
            starts_at,
            ends_at,
            "bot" if actor.type == "bot" else "human",
            actor.user_id,
        ),
    )
    return _appointment(await cursor.fetchone())


async def appointment_for_update(conn: AsyncConnection, appointment_id: UUID) -> Appointment | None:
    cursor = await conn.execute(
        f"select {_APPOINTMENT_COLUMNS} from public.appointments where id = %s for update",  # noqa: S608
        (appointment_id,),
    )
    row = await cursor.fetchone()
    return _appointment(row) if row else None


async def reschedule(
    conn: AsyncConnection, appointment_id: UUID, *, provider_id: UUID, starts_at: datetime, ends_at: datetime
) -> Appointment:
    cursor = await conn.execute(
        f"""
        update public.appointments set provider_id = %s, starts_at = %s, ends_at = %s
        where id = %s returning {_APPOINTMENT_COLUMNS}
        """,  # noqa: S608
        (provider_id, starts_at, ends_at, appointment_id),
    )
    return _appointment(await cursor.fetchone())


async def set_status(
    conn: AsyncConnection,
    appointment_id: UUID,
    status: AppointmentStatus,
    *,
    cancelled_at: datetime | None = None,
    reason: str | None = None,
) -> Appointment:
    cursor = await conn.execute(
        f"""
        update public.appointments set status = %s, cancelled_at = %s, cancellation_reason = %s
        where id = %s returning {_APPOINTMENT_COLUMNS}
        """,  # noqa: S608
        (status, cancelled_at, reason, appointment_id),
    )
    return _appointment(await cursor.fetchone())


async def void_stale_reminders(conn: AsyncConnection, appointment_id: UUID) -> int:
    """Run right after a cancellation or a move, in its transaction: a reminder still waiting in the
    outbox for a time the appointment no longer has is voided and never goes out. One already on its
    way to Meta is not touched (it may have left)."""
    cursor = await conn.execute(
        """
        with voided as (
          update public.appointment_reminders r set status = 'voided'
          from public.appointments a, public.messages m
          where r.appointment_id = %s and r.status = 'queued'
            and a.id = r.appointment_id
            and (a.status not in ('pending', 'confirmed') or a.starts_at <> r.starts_at)
            and m.id = r.message_id and m.delivery_status = 'pending' and m.send_started_at is null
          returning r.message_id
        )
        update public.messages set delivery_status = 'failed'
        where id in (select message_id from voided)
        """,
        (appointment_id,),
    )
    return cursor.rowcount


async def insert_event(  # noqa: PLR0913 - one value per column
    conn: AsyncConnection,
    *,
    clinic_id: UUID,
    event_type: str,
    appointment_id: UUID,
    occurred_at: datetime,
    value_cents: int | None = None,
    metadata: dict[str, Any] | None = None,
) -> None:
    """A measurable fact for the monthly report. Metadata never carries personal data."""
    await conn.execute(
        """
        insert into public.events (clinic_id, type, appointment_id, occurred_at, value_cents, metadata)
        values (%s, %s, %s, %s, %s, %s)
        """,
        (clinic_id, event_type, appointment_id, occurred_at, value_cents, Jsonb(metadata or {})),
    )


async def insert_audit(  # noqa: PLR0913 - one value per column
    conn: AsyncConnection,
    *,
    clinic_id: UUID,
    actor: Actor,
    action: str,
    record_id: UUID,
    changes: dict[str, Any],
) -> None:
    """Who changed which appointment and how. Ids, states and times only."""
    await conn.execute(
        """
        insert into public.audit_log
          (clinic_id, actor_type, actor_user_id, action, table_name, record_id, changes)
        values (%s, %s, %s, %s, 'appointments', %s, %s)
        """,
        (clinic_id, actor.type, actor.user_id, action, record_id, Jsonb(changes)),
    )


def _appointment(row: tuple[Any, ...] | None) -> Appointment:
    if row is None:
        raise LookupError("the appointment row disappeared inside its own transaction")
    return Appointment(*cast(tuple[UUID, UUID, UUID, UUID, datetime, datetime, AppointmentStatus], row))
