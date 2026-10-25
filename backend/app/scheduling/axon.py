"""Axon's own agenda, in Postgres. The same rules serve the bot and the panel.

Two guarantees against double bookings: each booking locks its provider while it checks the slot
and inserts (so the buffer holds too), and the exclusion constraint on appointments rejects any
overlap that could still slip through.
"""

from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from uuid import UUID

from psycopg import AsyncConnection
from psycopg.errors import ExclusionViolation

from app.db import agenda as sql
from app.db.connection import Database
from app.scheduling.availability import BOT_LEAD_TIME, Slot, find_slots, is_open
from app.scheduling.connector import (
    Actor,
    Appointment,
    AppointmentStatus,
    Booking,
    InvalidChangeError,
    NotFoundError,
    Shift,
    SlotUnavailableError,
)

# Appointments and exceptions are loaded with this margin around the window, for the buffer.
_MARGIN = timedelta(days=1)
# How far ahead alternatives are looked for when a slot is no longer free.
_ALTERNATIVES_WINDOW = timedelta(days=14)
_ALTERNATIVES = 3
_ACTIVE = ("pending", "confirmed")
# Noon in the clinic's time zone splits the morning from the afternoon.
_NOON = 12


class _UnavailableError(Exception):
    """Internal: the slot was not free; the caller computes alternatives after the rollback."""


def _utc_now() -> datetime:
    return datetime.now(UTC)


class AxonAgenda:
    def __init__(self, db: Database, clock: Callable[[], datetime] = _utc_now) -> None:
        self._db = db
        self._clock = clock

    async def find_slots(  # noqa: PLR0913 - the connector's signature
        self,
        clinic_id: UUID,
        service_id: UUID,
        window_start: datetime,
        window_end: datetime,
        *,
        actor: Actor,
        shift: Shift | None = None,
    ) -> list[Slot]:
        async with self._db.clinic_transaction(clinic_id) as conn:
            service = await _active_service(conn, service_id)
            slots = await self._free_slots(
                conn,
                duration=timedelta(minutes=service.duration_minutes),
                window_start=window_start,
                window_end=window_end,
                lead_time=_lead_time(actor),
            )
            if shift is None:
                return slots
            tz = await sql.clinic_timezone(conn)
            morning = shift == "morning"
            return [slot for slot in slots if (slot.starts_at.astimezone(tz).hour < _NOON) == morning]

    async def create(self, clinic_id: UUID, booking: Booking, *, actor: Actor) -> Appointment:
        now = self._clock()
        try:
            async with self._db.clinic_transaction(clinic_id) as conn:
                service = await _active_service(conn, booking.service_id)
                if not await sql.patient_exists(conn, booking.patient_id):
                    raise NotFoundError("Ese paciente no existe en este consultorio.")
                duration = timedelta(minutes=service.duration_minutes)
                provider_id = await self._lock_free_provider(
                    conn, booking.provider_id, booking.starts_at, duration, _lead_time(actor)
                )
                appointment = await sql.insert_appointment(
                    conn,
                    clinic_id=clinic_id,
                    patient_id=booking.patient_id,
                    service_id=booking.service_id,
                    provider_id=provider_id,
                    starts_at=booking.starts_at,
                    ends_at=booking.starts_at + duration,
                    actor=actor,
                )
                event = "appointment_booked"
                if actor.type == "bot" and not await self._clinic_open(conn, now):
                    # The bot answered while the clinic was closed: an appointment recovered.
                    event = "appointment_booked_after_hours"
                await sql.insert_event(
                    conn,
                    clinic_id=clinic_id,
                    event_type=event,
                    appointment_id=appointment.id,
                    occurred_at=now,
                )
                await sql.insert_audit(
                    conn,
                    clinic_id=clinic_id,
                    actor=actor,
                    action="appointment.create",
                    record_id=appointment.id,
                    changes=_snapshot(appointment),
                )
                return appointment
        except (_UnavailableError, ExclusionViolation):
            alternatives = await self._alternatives(
                clinic_id, booking.service_id, booking.starts_at, _lead_time(actor), now
            )
            raise SlotUnavailableError(alternatives) from None

    async def move(
        self,
        clinic_id: UUID,
        appointment_id: UUID,
        starts_at: datetime,
        *,
        actor: Actor,
        provider_id: UUID | None = None,
    ) -> Appointment:
        now = self._clock()
        service_id: UUID | None = None
        try:
            async with self._db.clinic_transaction(clinic_id) as conn:
                current = await _changeable(conn, appointment_id)
                service_id = current.service_id
                duration = current.ends_at - current.starts_at
                provider = await self._lock_free_provider(
                    conn,
                    provider_id or current.provider_id,
                    starts_at,
                    duration,
                    _lead_time(actor),
                    exclude_id=current.id,
                )
                moved = await sql.reschedule(
                    conn, current.id, provider_id=provider, starts_at=starts_at, ends_at=starts_at + duration
                )
                await sql.insert_event(
                    conn,
                    clinic_id=clinic_id,
                    event_type="appointment_rescheduled",
                    appointment_id=moved.id,
                    occurred_at=now,
                )
                await sql.insert_audit(
                    conn,
                    clinic_id=clinic_id,
                    actor=actor,
                    action="appointment.reschedule",
                    record_id=moved.id,
                    changes={"before": _snapshot(current), "after": _snapshot(moved)},
                )
                return moved
        except (_UnavailableError, ExclusionViolation):
            if service_id is None:
                raise
            alternatives = await self._alternatives(clinic_id, service_id, starts_at, _lead_time(actor), now)
            raise SlotUnavailableError(alternatives) from None

    async def cancel(
        self, clinic_id: UUID, appointment_id: UUID, *, actor: Actor, reason: str | None = None
    ) -> Appointment:
        now = self._clock()
        async with self._db.clinic_transaction(clinic_id) as conn:
            current = await _changeable(conn, appointment_id)
            cancelled = await sql.set_status(conn, current.id, "cancelled", cancelled_at=now, reason=reason)
            await sql.insert_event(
                conn,
                clinic_id=clinic_id,
                event_type="appointment_cancelled",
                appointment_id=cancelled.id,
                occurred_at=now,
            )
            await sql.insert_audit(
                conn,
                clinic_id=clinic_id,
                actor=actor,
                action="appointment.cancel",
                record_id=cancelled.id,
                changes={"status": [current.status, "cancelled"]},
            )
            return cancelled

    async def record_outcome(
        self, clinic_id: UUID, appointment_id: UUID, *, attended: bool, actor: Actor
    ) -> Appointment:
        """Marks that the patient came or not. Attending records the value at the service's price."""
        now = self._clock()
        async with self._db.clinic_transaction(clinic_id) as conn:
            current = await _changeable(conn, appointment_id)
            if current.starts_at > now:
                raise InvalidChangeError("Todavía no es la hora de esa cita.")
            status: AppointmentStatus = "attended" if attended else "no_show"
            updated = await sql.set_status(conn, current.id, status)
            service = await sql.get_service(conn, current.service_id)
            await sql.insert_event(
                conn,
                clinic_id=clinic_id,
                event_type=f"appointment_{status}",
                appointment_id=updated.id,
                occurred_at=now,
                value_cents=service.reference_price_cents if attended and service else None,
            )
            await sql.insert_audit(
                conn,
                clinic_id=clinic_id,
                actor=actor,
                action=f"appointment.{status}",
                record_id=updated.id,
                changes={"status": [current.status, status]},
            )
            return updated

    # Internals ---------------------------------------------------------------------------

    async def _free_slots(  # noqa: PLR0913 - the inputs of the availability calculation
        self,
        conn: AsyncConnection,
        *,
        duration: timedelta,
        window_start: datetime,
        window_end: datetime,
        lead_time: timedelta,
        provider_ids: list[UUID] | None = None,
        exclude_id: UUID | None = None,
    ) -> list[Slot]:
        providers = provider_ids if provider_ids is not None else await sql.active_provider_ids(conn)
        return find_slots(
            hours=await sql.weekly_hours(conn, providers),
            appointments=await sql.active_appointments_between(
                conn, window_start - _MARGIN, window_end + _MARGIN, exclude_id=exclude_id
            ),
            exceptions=await sql.exceptions_between(conn, window_start - _MARGIN, window_end + _MARGIN),
            duration=duration,
            tz=await sql.clinic_timezone(conn),
            now=self._clock(),
            window_start=window_start,
            window_end=window_end,
            lead_time=lead_time,
        )

    async def _lock_free_provider(  # noqa: PLR0913 - what a booking needs to be checked
        self,
        conn: AsyncConnection,
        provider_id: UUID | None,
        starts_at: datetime,
        duration: timedelta,
        lead_time: timedelta,
        *,
        exclude_id: UUID | None = None,
    ) -> UUID:
        """Locks and returns a provider that has `starts_at` free, or raises _UnavailableError."""
        candidates = [provider_id] if provider_id else await sql.active_provider_ids(conn)
        start = starts_at.astimezone(UTC)
        for candidate in candidates:
            if not await sql.lock_provider(conn, candidate):
                if provider_id:
                    raise NotFoundError("Ese dentista no existe o no está activo en este consultorio.")
                continue
            free = await self._free_slots(
                conn,
                duration=duration,
                window_start=start,
                window_end=start + timedelta(minutes=1),
                lead_time=lead_time,
                provider_ids=[candidate],
                exclude_id=exclude_id,
            )
            if any(slot.starts_at == start for slot in free):
                return candidate
        raise _UnavailableError

    async def _clinic_open(self, conn: AsyncConnection, at: datetime) -> bool:
        providers = await sql.active_provider_ids(conn)
        return is_open(
            await sql.weekly_hours(conn, providers),
            await sql.exceptions_between(conn, at - _MARGIN, at + _MARGIN),
            await sql.clinic_timezone(conn),
            at,
        )

    async def _alternatives(
        self, clinic_id: UUID, service_id: UUID, wanted: datetime, lead_time: timedelta, now: datetime
    ) -> list[Slot]:
        """The free slots nearest after the one that was asked for."""
        start = max(wanted, now)
        async with self._db.clinic_transaction(clinic_id) as conn:
            service = await sql.get_service(conn, service_id)
            if service is None or not service.active:
                return []
            slots = await self._free_slots(
                conn,
                duration=timedelta(minutes=service.duration_minutes),
                window_start=start,
                window_end=start + _ALTERNATIVES_WINDOW,
                lead_time=lead_time,
            )
        return slots[:_ALTERNATIVES]


def _lead_time(actor: Actor) -> timedelta:
    return BOT_LEAD_TIME if actor.type == "bot" else timedelta(0)


async def _active_service(conn: AsyncConnection, service_id: UUID) -> sql.Service:
    service = await sql.get_service(conn, service_id)
    if service is None:
        raise NotFoundError("Ese servicio no existe en este consultorio.")
    if not service.active:
        raise InvalidChangeError("Ese servicio ya no se ofrece.")
    return service


async def _changeable(conn: AsyncConnection, appointment_id: UUID) -> Appointment:
    current = await sql.appointment_for_update(conn, appointment_id)
    if current is None:
        raise NotFoundError("Esa cita no existe en este consultorio.")
    if current.status not in _ACTIVE:
        raise InvalidChangeError("Solo se puede cambiar una cita pendiente o confirmada.")
    return current


def _snapshot(appointment: Appointment) -> dict[str, str]:
    return {
        "status": appointment.status,
        "provider_id": str(appointment.provider_id),
        "service_id": str(appointment.service_id),
        "starts_at": appointment.starts_at.isoformat(),
        "ends_at": appointment.ends_at.isoformat(),
    }
