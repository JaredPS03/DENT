"""Axon's agenda against the local database: booking, moving, cancelling and outcomes, with
their events and audit trail, and zero double bookings under concurrency."""

import asyncio
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest
from psycopg_pool import AsyncConnectionPool

from app.db.connection import Database
from app.scheduling.availability import BOT_LEAD_TIME, Slot
from app.scheduling.axon import AxonAgenda
from app.scheduling.connector import (
    Actor,
    Booking,
    InvalidChangeError,
    NotFoundError,
    SlotUnavailableError,
)
from tests.conftest import CLINIC_A, CLINIC_B
from tests.integration.conftest import rows_as_clinic

pytestmark = pytest.mark.anyio

# From supabase/seed.sql: clinic A's dentist, services and patients; one patient of clinic B.
CLEANING = UUID("a3000000-0000-4000-8000-000000000001")  # 45 min, 600.00 MXN
PATIENT_A = UUID("a6000000-0000-4000-8000-000000000002")
PATIENT_B = UUID("b6000000-0000-4000-8000-000000000001")
OWNER_A = UUID("a1000000-0000-4000-8000-000000000001")
STAFF = Actor("user", OWNER_A)
BOT = Actor("bot")


@pytest.fixture
async def db(database_url: str) -> AsyncIterator[Database]:
    pool = AsyncConnectionPool(
        database_url,
        min_size=1,
        max_size=20,
        open=False,
        kwargs={"autocommit": True, "prepare_threshold": None},
    )
    await pool.open(wait=True)
    try:
        yield Database(pool)
    finally:
        await pool.close()


@pytest.fixture
def agenda(db: Database) -> AxonAgenda:
    return AxonAgenda(db)


async def free_slots(agenda: AxonAgenda, days_ahead: int = 40, actor: Actor = STAFF) -> list[Slot]:
    """Free cleaning slots far from the seed's appointments, which sit within three weeks."""
    start = datetime.now(UTC) + timedelta(days=days_ahead)
    return await agenda.find_slots(CLINIC_A, CLEANING, start, start + timedelta(days=7), actor=actor)


def booking_at(slot: Slot, patient_id: UUID = PATIENT_A) -> Booking:
    return Booking(patient_id=patient_id, service_id=CLEANING, starts_at=slot.starts_at)


def events_of(database_url: str, appointment_id: UUID) -> list[tuple[str, int | None]]:
    rows = rows_as_clinic(
        database_url,
        CLINIC_A,
        "select type, value_cents from public.events where appointment_id = %s"
        " order by occurred_at, created_at",
        (appointment_id,),
    )
    return [(row[0], row[1]) for row in rows]


def audit_of(database_url: str, appointment_id: UUID) -> list[tuple[str, str, UUID | None]]:
    rows = rows_as_clinic(
        database_url,
        CLINIC_A,
        "select action, actor_type, actor_user_id from public.audit_log where record_id = %s"
        " order by occurred_at",
        (appointment_id,),
    )
    return [(row[0], row[1], row[2]) for row in rows]


async def test_booking_takes_the_slot_and_leaves_its_trace(agenda: AxonAgenda, database_url: str) -> None:
    slot = (await free_slots(agenda))[0]

    appointment = await agenda.create(CLINIC_A, booking_at(slot), actor=STAFF)

    assert appointment.status == "pending"
    assert appointment.starts_at == slot.starts_at
    assert appointment.ends_at - appointment.starts_at == timedelta(minutes=45)
    assert slot.starts_at not in [s.starts_at for s in await free_slots(agenda)]
    assert events_of(database_url, appointment.id) == [("appointment_booked", None)]
    assert audit_of(database_url, appointment.id) == [("appointment.create", "user", OWNER_A)]


async def test_a_taken_slot_is_refused_with_free_alternatives(agenda: AxonAgenda) -> None:
    slot = (await free_slots(agenda))[0]
    await agenda.create(CLINIC_A, booking_at(slot), actor=STAFF)

    with pytest.raises(SlotUnavailableError) as refused:
        await agenda.create(
            CLINIC_A, booking_at(slot, UUID("a6000000-0000-4000-8000-000000000003")), actor=STAFF
        )

    assert str(refused.value) == "Ese horario ya no está disponible."
    alternatives = refused.value.alternatives
    assert 1 <= len(alternatives) <= 3
    still_free = {s.starts_at for s in await free_slots(agenda)}
    assert all(alternative.starts_at in still_free for alternative in alternatives)
    assert all(alternative.starts_at > slot.starts_at for alternative in alternatives)


async def test_twenty_simultaneous_bookings_leave_exactly_one(agenda: AxonAgenda, database_url: str) -> None:
    slot = (await free_slots(agenda, days_ahead=50))[0]

    results = await asyncio.gather(
        *(agenda.create(CLINIC_A, booking_at(slot), actor=STAFF) for _ in range(20)),
        return_exceptions=True,
    )

    booked = [result for result in results if not isinstance(result, BaseException)]
    refused = [result for result in results if isinstance(result, SlotUnavailableError)]
    assert len(booked) == 1
    assert len(refused) == 19
    rows = rows_as_clinic(
        database_url,
        CLINIC_A,
        "select count(*) from public.appointments"
        " where starts_at = %s and status in ('pending', 'confirmed')",
        (slot.starts_at,),
    )
    assert rows[0][0] == 1


async def test_the_bot_never_offers_slots_within_the_lead_time(agenda: AxonAgenda) -> None:
    now = datetime.now(UTC)
    slots = await agenda.find_slots(CLINIC_A, CLEANING, now, now + timedelta(days=3), actor=BOT)
    assert all(slot.starts_at >= now + BOT_LEAD_TIME for slot in slots)


async def test_a_bot_booking_while_the_clinic_is_closed_counts_as_recovered(
    db: Database, database_url: str
) -> None:
    # Sunday at 23:00 in Mexico City: clinic A does not open on Sundays.
    sunday_night = datetime(2026, 11, 1, 23, 0, tzinfo=UTC) + timedelta(hours=6)
    agenda = AxonAgenda(db, clock=lambda: sunday_night)
    slot = (await free_slots(AxonAgenda(db), days_ahead=60))[0]

    appointment = await agenda.create(CLINIC_A, booking_at(slot), actor=BOT)

    assert events_of(database_url, appointment.id) == [("appointment_booked_after_hours", None)]
    assert audit_of(database_url, appointment.id) == [("appointment.create", "bot", None)]


async def test_moving_frees_the_old_slot_and_takes_the_new_one(agenda: AxonAgenda, database_url: str) -> None:
    first, *_, later = await free_slots(agenda, days_ahead=70)
    appointment = await agenda.create(CLINIC_A, booking_at(first), actor=STAFF)

    moved = await agenda.move(CLINIC_A, appointment.id, later.starts_at, actor=STAFF)

    assert moved.starts_at == later.starts_at
    assert moved.ends_at - moved.starts_at == timedelta(minutes=45)
    free = {s.starts_at for s in await free_slots(agenda, days_ahead=70)}
    assert first.starts_at in free
    assert later.starts_at not in free
    assert [event for event, _ in events_of(database_url, appointment.id)] == [
        "appointment_booked",
        "appointment_rescheduled",
    ]


async def test_moving_onto_a_taken_slot_is_refused(agenda: AxonAgenda) -> None:
    slots = await free_slots(agenda, days_ahead=80)
    first = slots[0]
    second = next(slot for slot in slots if slot.starts_at >= first.ends_at + timedelta(hours=1))
    one = await agenda.create(CLINIC_A, booking_at(first), actor=STAFF)
    await agenda.create(
        CLINIC_A, booking_at(second, UUID("a6000000-0000-4000-8000-000000000003")), actor=STAFF
    )

    with pytest.raises(SlotUnavailableError):
        await agenda.move(CLINIC_A, one.id, second.starts_at, actor=STAFF)


async def test_cancelling_frees_the_slot_once(agenda: AxonAgenda, database_url: str) -> None:
    slot = (await free_slots(agenda, days_ahead=90))[0]
    appointment = await agenda.create(CLINIC_A, booking_at(slot), actor=STAFF)

    cancelled = await agenda.cancel(CLINIC_A, appointment.id, actor=STAFF, reason="Pidió otro día")

    assert cancelled.status == "cancelled"
    assert slot.starts_at in {s.starts_at for s in await free_slots(agenda, days_ahead=90)}
    assert events_of(database_url, appointment.id)[-1] == ("appointment_cancelled", None)
    with pytest.raises(InvalidChangeError):
        await agenda.cancel(CLINIC_A, appointment.id, actor=STAFF)


async def test_attendance_records_the_service_price_only_once_it_happened(
    db: Database, database_url: str
) -> None:
    agenda = AxonAgenda(db)
    slot = (await free_slots(agenda, days_ahead=100))[0]
    appointment = await agenda.create(CLINIC_A, booking_at(slot), actor=STAFF)

    with pytest.raises(InvalidChangeError):
        await agenda.record_outcome(CLINIC_A, appointment.id, attended=True, actor=STAFF)

    after_it = AxonAgenda(db, clock=lambda: slot.ends_at + timedelta(hours=1))
    attended = await after_it.record_outcome(CLINIC_A, appointment.id, attended=True, actor=STAFF)

    assert attended.status == "attended"
    assert events_of(database_url, appointment.id)[-1] == ("appointment_attended", 60000)


async def test_another_clinic_s_patient_or_appointment_does_not_exist_here(agenda: AxonAgenda) -> None:
    slot = (await free_slots(agenda, days_ahead=110))[0]
    with pytest.raises(NotFoundError):
        await agenda.create(CLINIC_A, booking_at(slot, PATIENT_B), actor=STAFF)

    appointment = await agenda.create(CLINIC_A, booking_at(slot), actor=STAFF)
    with pytest.raises(NotFoundError):
        await agenda.cancel(CLINIC_B, appointment.id, actor=STAFF)
    with pytest.raises(NotFoundError):
        await agenda.find_slots(CLINIC_B, CLEANING, slot.starts_at, slot.ends_at, actor=STAFF)
