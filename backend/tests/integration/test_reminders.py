"""Milestone 1D against the local database, with a simulated clock: the 24 h reminder, its buttons,
the scheduler's claims, the second alert of an urgency, the alerts to Jared, Meta's notices about
the account and the daily cleanup."""

import asyncio
import time
from collections.abc import AsyncIterator, Iterator
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4
from zoneinfo import ZoneInfo

import psycopg
import pytest
from psycopg.types.json import Jsonb
from psycopg_pool import AsyncConnectionPool

from app.bot.transcription import FakeTranscription
from app.core.config import Settings
from app.db.connection import Database
from app.jobs.context import WorkerContext
from app.jobs.reminders import queue_reminders
from app.jobs.scheduled import clean_up, watch
from app.jobs.scheduler import run_scheduler, tick
from app.scheduling.axon import AxonAgenda
from app.scheduling.connector import Actor
from app.webhooks.whatsapp import _route_account_changes
from app.whatsapp.payloads import split_payload
from app.whatsapp.templates import UNKNOWN_NAME_GREETING
from tests.conftest import (
    CLINIC_A,
    CLINIC_B,
    LOCAL_ADMIN_DATABASE_URL,
    PHONE_NUMBER_ID_A,
    make_settings,
    webhook_payload,
)
from tests.integration.test_bot_turns import body_text, says, taps, to_patient, turn
from tests.integration.test_whatsapp_round_trip import (
    audio_from,
    conversation_of,
    one,
    rows,
    take_turns,
    wa_id,
    wamid,
)
from tests.support.fake_model import ScriptedModel
from tests.support.meta_fixtures import first_item, meta_payload
from tests.support.whatsapp_world import (
    VOICE_NOTE,
    FakeMeta,
    MemoryStore,
    consenting_patient,
    make_context,
    queued_count,
    random_phone,
    run_matching,
)

pytestmark = pytest.mark.anyio

AXON_NUMBER = "300000000000003"
OPS_PHONE = "+529510000099"
TEAM_PHONES = ("+529510000011", "+529510000012")  # clinic A's owner and assistant, in the seed
CLINIC_TZ = ZoneInfo("America/Mexico_City")
WABA_A = "900000000000001"
LIMPIEZA = UUID("a3000000-0000-4000-8000-000000000001")
PROVIDER_A = UUID("a2000000-0000-4000-8000-000000000001")
BOT = Actor(type="bot")


@pytest.fixture
async def db(database_url: str) -> AsyncIterator[Database]:
    pool = AsyncConnectionPool(
        database_url,
        min_size=1,
        max_size=10,
        open=False,
        kwargs={"autocommit": True, "prepare_threshold": None},
    )
    await pool.open(wait=True)
    try:
        yield Database(pool)
    finally:
        await pool.close()


@pytest.fixture(autouse=True)
def clean_slate() -> Iterator[None]:
    """Claims and the jobs these tests queue outlive a test: every test starts without them."""
    _admin("delete from private.claims")
    yield
    _admin("delete from pgmq.q_whatsapp_inbound where message->>'kind' in ('scheduled', 'ops_alert')")


@pytest.fixture
def phone() -> str:
    return random_phone()


@pytest.fixture
def settings(phone: str) -> Settings:
    return make_settings(
        whatsapp_allowed_test_numbers=(phone, *TEAM_PHONES, OPS_PHONE),
        axon_phone_number_id=AXON_NUMBER,
        ops_alert_phone=OPS_PHONE,
    )


@pytest.fixture
def meta() -> FakeMeta:
    return FakeMeta()


@pytest.fixture
def model() -> ScriptedModel:
    return ScriptedModel()


@pytest.fixture
def ctx(settings: Settings, db: Database, meta: FakeMeta, model: ScriptedModel) -> WorkerContext:
    return make_context(settings, db, meta, model=model)


def _admin(sql: str, params: tuple[Any, ...] = ()) -> None:
    with psycopg.connect(LOCAL_ADMIN_DATABASE_URL, autocommit=True) as conn:
        conn.execute(sql, params)


def _far_ten_am() -> datetime:
    """A weekday at 10:00 in the clinic, months or years ahead, that no other test uses."""
    day = datetime.now(CLINIC_TZ).date() + timedelta(days=60 + uuid4().int % 5000)
    while day.weekday() >= 5:
        day += timedelta(days=1)
    minutes = 5 * (uuid4().int % 12)
    return datetime(day.year, day.month, day.day, 10, minutes, tzinfo=CLINIC_TZ).astimezone(UTC)


async def _book(db: Database, patient_id: UUID, starts_at: datetime | None = None) -> tuple[UUID, datetime]:
    """An appointment of the patient, booked well ahead (its time set now)."""
    for attempt in range(10):
        # Taken by an appointment of another test: the next free time.
        when = starts_at + timedelta(minutes=50 * attempt) if starts_at else _far_ten_am()
        try:
            async with db.clinic_transaction(CLINIC_A) as conn:
                cursor = await conn.execute(
                    """
                    insert into public.appointments
                      (clinic_id, patient_id, service_id, provider_id, starts_at, ends_at, status, created_by)
                    values (%s, %s, %s, %s, %s, %s, 'pending', 'bot') returning id
                    """,
                    (CLINIC_A, patient_id, LIMPIEZA, PROVIDER_A, when, when + timedelta(minutes=45)),
                )
                row = await cursor.fetchone()
        except psycopg.errors.ExclusionViolation:
            continue
        assert row is not None
        return row[0], when
    raise AssertionError("no free time for the test appointment")


def _at(moment: datetime) -> Any:
    return lambda: moment


async def _patient_with_appointment(db: Database, phone: str) -> tuple[UUID, UUID, datetime]:
    patient_id = await consenting_patient(db, phone)
    appointment_id, starts_at = await _book(db, patient_id)
    return patient_id, appointment_id, starts_at


async def _reminders_of(db: Database, appointment_id: UUID) -> list[tuple[Any, ...]]:
    return await rows(
        db,
        "select status, skip_reason, message_id from public.appointment_reminders where appointment_id = %s",
        (appointment_id,),
    )


async def _sent_reminder(
    ctx: WorkerContext, db: Database, phone: str, appointment_id: UUID, starts_at: datetime
) -> tuple[UUID, str]:
    """Queues the reminder the day before (simulated clock) and sends it. Returns the conversation
    and Meta's id of the template."""
    ctx.clock = _at(starts_at - timedelta(hours=24) + timedelta(minutes=1))
    assert await queue_reminders(ctx, CLINIC_A, ctx.clock()) == 1
    ctx.clock = lambda: datetime.now(UTC)
    conversation_id = await conversation_of(db, phone)
    await take_turns(ctx, conversation_id)
    [(_, _, message_id)] = await _reminders_of(db, appointment_id)
    meta_id = (await one(db, "select meta_message_id from public.messages where id = %s", (message_id,)))[0]
    assert meta_id
    return conversation_id, meta_id


def _tap(phone: str, label: str, context_id: str | None) -> dict[str, Any]:
    """A tap on a quick reply of a template, as Meta documents it: the label, and the message it
    answers in context."""
    payload = meta_payload(
        "template_button",
        phone_number_id=PHONE_NUMBER_ID_A,
        user_phone=wa_id(phone),
        message_id=wamid(),
        timestamp=int(time.time()),
    )
    message = first_item(payload, "messages")
    message["button"] = {"payload": label, "text": label}
    if context_id is None:
        message.pop("context")
    else:
        message["context"]["id"] = context_id
    return payload


async def _appointment(db: Database, appointment_id: UUID) -> tuple[str, datetime]:
    status, starts_at = await one(
        db, "select status, starts_at from public.appointments where id = %s", (appointment_id,)
    )
    return status, starts_at


async def _events(db: Database, appointment_id: UUID) -> list[tuple[Any, ...]]:
    return await rows(
        db,
        "select type, metadata from public.events where appointment_id = %s order by occurred_at, created_at",
        (appointment_id,),
    )


# The reminder ------------------------------------------------------------------------------------


async def test_a_reminder_goes_out_24_hours_before_and_only_once(
    ctx: WorkerContext, db: Database, meta: FakeMeta, phone: str
) -> None:
    _, appointment_id, starts_at = await _patient_with_appointment(db, phone)
    day_before = starts_at - timedelta(hours=24)

    assert await queue_reminders(ctx, CLINIC_A, day_before - timedelta(minutes=15)) == 0  # not yet
    assert await queue_reminders(ctx, CLINIC_A, day_before + timedelta(minutes=1)) == 1
    assert await queue_reminders(ctx, CLINIC_A, day_before + timedelta(minutes=16)) == 0  # the next run

    await take_turns(ctx, await conversation_of(db, phone))
    [template] = [m["template"] for m in to_patient(meta, phone)]
    local = starts_at.astimezone(CLINIC_TZ)
    assert template["name"] == "recordatorio_cita"
    parameters = [p["text"] for p in template["components"][0]["parameters"]]
    assert parameters[0] == UNKNOWN_NAME_GREETING  # the patient never told their name
    assert parameters[1:] == [
        "Limpieza",
        parameters[2],
        f"10:{local.minute:02d} am",
        "Clínica Dental Sonrisa",
    ]
    assert parameters[2].endswith(f" {local.day} de " + parameters[2].split(" de ")[-1])
    [(status, _, message_id)] = await _reminders_of(db, appointment_id)
    assert status == "queued"
    content, kind, category, sender = await one(
        db, "select content, type, billing_category, sender from public.messages where id = %s", (message_id,)
    )
    assert (kind, category, sender) == ("template", "utility", "system")
    assert content.startswith("Hola de nuevo, este es el recordatorio de la cita de Limpieza mañana ")


async def test_two_workers_at_once_queue_a_single_reminder(
    ctx: WorkerContext, db: Database, phone: str
) -> None:
    _, appointment_id, starts_at = await _patient_with_appointment(db, phone)
    now = starts_at - timedelta(hours=24) + timedelta(minutes=1)

    queued = await asyncio.gather(queue_reminders(ctx, CLINIC_A, now), queue_reminders(ctx, CLINIC_A, now))

    assert sorted(queued) == [0, 1]
    assert len(await _reminders_of(db, appointment_id)) == 1


async def test_an_appointment_booked_with_less_than_24_hours_left_gets_no_reminder(
    ctx: WorkerContext, db: Database, meta: FakeMeta, phone: str
) -> None:
    patient_id = await consenting_patient(db, phone)
    starts_at = datetime.now(UTC) + timedelta(hours=20, minutes=uuid4().int % 600)
    appointment_id, _ = await _book(db, patient_id, starts_at.replace(second=0, microsecond=0))

    assert await queue_reminders(ctx, CLINIC_A, datetime.now(UTC) + timedelta(minutes=1)) == 0
    assert await _reminders_of(db, appointment_id) == []


async def test_an_appointment_cancelled_before_the_send_never_gets_its_reminder(
    ctx: WorkerContext, db: Database, meta: FakeMeta, phone: str
) -> None:
    _, appointment_id, starts_at = await _patient_with_appointment(db, phone)
    ctx.clock = _at(starts_at - timedelta(hours=24) + timedelta(minutes=1))
    assert await queue_reminders(ctx, CLINIC_A, ctx.clock()) == 1

    await AxonAgenda(db, ctx.clock).cancel(CLINIC_A, appointment_id, actor=BOT, reason="prueba")
    await take_turns(ctx, await conversation_of(db, phone))

    assert to_patient(meta, phone) == []
    [(status, _, message_id)] = await _reminders_of(db, appointment_id)
    assert status == "voided"
    assert (await one(db, "select delivery_status from public.messages where id = %s", (message_id,)))[
        0
    ] == "failed"


async def test_a_reminder_whose_appointment_changed_right_before_the_send_is_voided_then(
    ctx: WorkerContext, db: Database, meta: FakeMeta, phone: str
) -> None:
    """Changed without the agenda (a direct edit): the check right before Meta still catches it."""
    _, appointment_id, starts_at = await _patient_with_appointment(db, phone)
    assert await queue_reminders(ctx, CLINIC_A, starts_at - timedelta(hours=23)) == 1
    async with db.clinic_transaction(CLINIC_A) as conn:
        await conn.execute(
            "update public.appointments set starts_at = starts_at + interval '1 day',"
            " ends_at = ends_at + interval '1 day' where id = %s",
            (appointment_id,),
        )

    await take_turns(ctx, await conversation_of(db, phone))

    assert to_patient(meta, phone) == []
    assert [r[0] for r in await _reminders_of(db, appointment_id)] == ["voided"]


async def test_a_worker_restarted_mid_send_never_sends_the_reminder_twice(
    ctx: WorkerContext, db: Database, meta: FakeMeta, phone: str
) -> None:
    _, appointment_id, starts_at = await _patient_with_appointment(db, phone)
    assert await queue_reminders(ctx, CLINIC_A, starts_at - timedelta(hours=23)) == 1
    [(_, _, message_id)] = await _reminders_of(db, appointment_id)
    # A worker claimed it and died before Meta answered: it may have gone out.
    async with db.clinic_transaction(CLINIC_A) as conn:
        await conn.execute(
            "update public.messages set send_started_at = now() - interval '6 minutes' where id = %s",
            (message_id,),
        )

    await take_turns(ctx, await conversation_of(db, phone))
    assert await queue_reminders(ctx, CLINIC_A, starts_at - timedelta(hours=23)) == 0

    assert to_patient(meta, phone) == []
    # ...and Jared hears of a send with an unknown outcome.
    await watch(ctx, CLINIC_A, datetime.now(UTC))
    assert (
        await queued_count(db, {"kind": "ops_alert", "alert": "uncertain_sends", "clinic_id": str(CLINIC_A)})
        == 1
    )


async def test_a_moved_appointment_gets_a_reminder_for_its_new_time(
    ctx: WorkerContext, db: Database, phone: str
) -> None:
    _, appointment_id, starts_at = await _patient_with_appointment(db, phone)
    assert await queue_reminders(ctx, CLINIC_A, starts_at - timedelta(hours=23)) == 1
    moved_to = starts_at + timedelta(days=7)
    async with db.clinic_transaction(CLINIC_A) as conn:
        await conn.execute(
            "update public.appointments set starts_at = %s, ends_at = %s where id = %s",
            (moved_to, moved_to + timedelta(minutes=45), appointment_id),
        )

    assert await queue_reminders(ctx, CLINIC_A, moved_to - timedelta(hours=23)) == 1
    assert sorted(r[0] for r in await _reminders_of(db, appointment_id)) == ["queued", "queued"]


async def test_a_reminder_too_late_for_tomorrow_is_skipped_and_jared_hears(
    ctx: WorkerContext, db: Database, phone: str
) -> None:
    """The worker was down: the template says "mañana", and the appointment is today."""
    _, appointment_id, starts_at = await _patient_with_appointment(db, phone)

    assert await queue_reminders(ctx, CLINIC_A, starts_at - timedelta(hours=2)) == 0

    assert await _reminders_of(db, appointment_id) == [("skipped", "too_late", None)]
    assert (
        await queued_count(db, {"kind": "ops_alert", "alert": "reminder_failed", "clinic_id": str(CLINIC_A)})
        == 1
    )


async def test_without_the_template_approved_as_utility_no_reminder_goes(
    ctx: WorkerContext, db: Database, phone: str
) -> None:
    _, appointment_id, starts_at = await _patient_with_appointment(db, phone)
    _admin(
        "update public.templates set category = 'marketing'"
        " where clinic_id = %s and name = 'recordatorio_cita'",
        (CLINIC_A,),
    )
    try:
        assert await queue_reminders(ctx, CLINIC_A, starts_at - timedelta(hours=23)) == 0
    finally:
        _admin(
            "update public.templates set category = 'utility'"
            " where clinic_id = %s and name = 'recordatorio_cita'",
            (CLINIC_A,),
        )

    assert await _reminders_of(db, appointment_id) == []  # tried again on the next run
    assert (
        await queued_count(db, {"kind": "ops_alert", "alert": "reminder_failed", "clinic_id": str(CLINIC_A)})
        == 1
    )


# Its buttons -------------------------------------------------------------------------------------


async def test_confirmo_confirms_records_where_it_came_from_and_answers(
    ctx: WorkerContext, db: Database, meta: FakeMeta, model: ScriptedModel, phone: str
) -> None:
    _, appointment_id, starts_at = await _patient_with_appointment(db, phone)
    _, template_id = await _sent_reminder(ctx, db, phone, appointment_id, starts_at)

    await turn(ctx, db, phone, _tap(phone, "Confirmo", template_id))

    assert (await _appointment(db, appointment_id))[0] == "confirmed"
    assert await _events(db, appointment_id) == [("appointment_confirmed", {"source": "reminder"})]
    reply = body_text(to_patient(meta, phone)[-1])
    assert reply.startswith("¡Gracias! Tu cita de Limpieza queda confirmada para el ")
    assert model.calls == []


async def test_a_button_tapped_twice_changes_the_appointment_once(
    ctx: WorkerContext, db: Database, meta: FakeMeta, phone: str
) -> None:
    _, appointment_id, starts_at = await _patient_with_appointment(db, phone)
    _, template_id = await _sent_reminder(ctx, db, phone, appointment_id, starts_at)

    await turn(ctx, db, phone, _tap(phone, "Confirmo", template_id))
    await turn(ctx, db, phone, _tap(phone, "Confirmo", template_id))

    assert (await _appointment(db, appointment_id))[0] == "confirmed"
    assert [e[0] for e in await _events(db, appointment_id)] == ["appointment_confirmed"]
    replies = [body_text(m) for m in to_patient(meta, phone)[1:]]
    assert len(replies) == 2
    assert all("queda confirmada" in r for r in replies)


async def test_cancelar_cancels_and_offers_another_date(
    ctx: WorkerContext, db: Database, meta: FakeMeta, phone: str
) -> None:
    _, appointment_id, starts_at = await _patient_with_appointment(db, phone)
    _, template_id = await _sent_reminder(ctx, db, phone, appointment_id, starts_at)

    await turn(ctx, db, phone, _tap(phone, "Cancelar", template_id))

    assert (await _appointment(db, appointment_id))[0] == "cancelled"
    assert await _events(db, appointment_id) == [("appointment_cancelled", {"source": "reminder"})]
    reply = body_text(to_patient(meta, phone)[-1])
    assert reply.startswith("Listo, cancelé tu cita de Limpieza del ")
    assert "Si quieres otra fecha, escríbeme" in reply

    # Tapped again later: it says so, and nothing changes.
    await turn(ctx, db, phone, _tap(phone, "Cancelar", template_id))
    assert body_text(to_patient(meta, phone)[-1]).startswith("Esa cita ya está cancelada.")
    assert len(await _events(db, appointment_id)) == 1


async def test_cambiar_offers_free_slots_and_the_one_chosen_moves_the_appointment(
    ctx: WorkerContext, db: Database, meta: FakeMeta, model: ScriptedModel, phone: str
) -> None:
    _, appointment_id, starts_at = await _patient_with_appointment(db, phone)
    _, template_id = await _sent_reminder(ctx, db, phone, appointment_id, starts_at)

    await turn(ctx, db, phone, _tap(phone, "Cambiar", template_id))

    offer = to_patient(meta, phone)[-1]
    assert body_text(offer).startswith("Claro. Estos horarios están libres para tu Limpieza.")
    buttons = offer["interactive"]["action"]["buttons"]
    assert 1 <= len(buttons) <= 3
    assert (await _appointment(db, appointment_id)) == ("pending", starts_at)  # untouched until chosen
    chosen = buttons[0]["reply"]

    await turn(ctx, db, phone, taps(phone, chosen["id"], chosen["title"]))

    status, moved_to = await _appointment(db, appointment_id)
    assert status == "pending"
    assert moved_to != starts_at
    assert await _events(db, appointment_id) == [("appointment_rescheduled", {"source": "reminder"})]
    assert body_text(to_patient(meta, phone)[-1]).startswith("Listo, tu cita de Limpieza quedó para ")
    assert model.calls == []


async def test_a_tap_on_an_old_reminder_says_what_changed(
    ctx: WorkerContext, db: Database, meta: FakeMeta, phone: str
) -> None:
    _, appointment_id, starts_at = await _patient_with_appointment(db, phone)
    _, template_id = await _sent_reminder(ctx, db, phone, appointment_id, starts_at)
    moved_to = starts_at + timedelta(days=7)
    async with db.clinic_transaction(CLINIC_A) as conn:  # the team moved it from the panel
        await conn.execute(
            "update public.appointments set starts_at = %s, ends_at = %s where id = %s",
            (moved_to, moved_to + timedelta(minutes=45), appointment_id),
        )

    await turn(ctx, db, phone, _tap(phone, "Confirmo", template_id))

    assert (await _appointment(db, appointment_id)) == ("pending", moved_to)
    assert body_text(to_patient(meta, phone)[-1]).startswith("Tu cita cambió: ahora es el ")


async def test_while_the_team_has_the_chat_a_tap_changes_the_appointment_without_a_reply(
    ctx: WorkerContext, db: Database, meta: FakeMeta, phone: str
) -> None:
    _, appointment_id, starts_at = await _patient_with_appointment(db, phone)
    conversation_id, template_id = await _sent_reminder(ctx, db, phone, appointment_id, starts_at)
    async with db.clinic_transaction(CLINIC_A) as conn:
        await conn.execute(
            "update public.conversations set status = 'human' where id = %s", (conversation_id,)
        )

    await turn(ctx, db, phone, _tap(phone, "Confirmo", template_id))

    assert (await _appointment(db, appointment_id))[0] == "confirmed"
    assert len(to_patient(meta, phone)) == 1  # only the reminder: the bot does not write


async def test_a_tap_whose_template_id_is_unknown_finds_the_latest_reminder(
    ctx: WorkerContext, db: Database, phone: str
) -> None:
    """The template's send outcome was lost, so its Meta id is unknown here."""
    _, appointment_id, starts_at = await _patient_with_appointment(db, phone)
    await _sent_reminder(ctx, db, phone, appointment_id, starts_at)

    await turn(ctx, db, phone, _tap(phone, "confirmo", "wamid.NO.SE.CONOCE"))

    assert (await _appointment(db, appointment_id))[0] == "confirmed"


async def test_a_tap_never_reaches_the_appointment_of_another_patient(
    ctx: WorkerContext, db: Database, settings: Settings, meta: FakeMeta, phone: str
) -> None:
    _, appointment_id, starts_at = await _patient_with_appointment(db, phone)
    _, template_id = await _sent_reminder(ctx, db, phone, appointment_id, starts_at)
    stranger = random_phone()
    ctx.settings = settings.model_copy(
        update={"whatsapp_allowed_test_numbers": (*settings.whatsapp_allowed_test_numbers, stranger)}
    )

    await turn(ctx, db, stranger, _tap(stranger, "Cancelar", template_id))

    assert (await _appointment(db, appointment_id))[0] == "pending"
    assert await _events(db, appointment_id) == []


# The watchdog ------------------------------------------------------------------------------------


async def _urgency(ctx: WorkerContext, db: Database, phone: str, minutes_ago: int) -> tuple[UUID, UUID]:
    conversation_id = await turn(ctx, db, phone, says(phone, "me duele muchisimo la muela"))
    escalation_id = (
        await one(db, "select id from public.escalations where conversation_id = %s", (conversation_id,))
    )[0]
    await run_matching(ctx, {"kind": "team_alert", "escalation_id": str(escalation_id)})
    _admin(
        "update public.escalations set created_at = now() - make_interval(mins => %s) where id = %s",
        (minutes_ago, escalation_id),
    )
    return conversation_id, escalation_id


async def test_an_urgency_nobody_attends_is_alerted_again_once(
    ctx: WorkerContext, db: Database, meta: FakeMeta, phone: str
) -> None:
    _, escalation_id = await _urgency(ctx, db, phone, minutes_ago=9)
    await watch(ctx, CLINIC_A, datetime.now(UTC))
    assert await queued_count(db, {"kind": "team_alert", "escalation_id": str(escalation_id)}) == 0  # not yet

    _admin(
        "update public.escalations set created_at = created_at - interval '2 minutes' where id = %s",
        (escalation_id,),
    )
    await watch(ctx, CLINIC_A, datetime.now(UTC))
    await watch(ctx, CLINIC_A, datetime.now(UTC))
    await run_matching(ctx, {"kind": "team_alert", "escalation_id": str(escalation_id)})

    alerts = [m for m in meta.sent if m.get("to") in {p.removeprefix("+") for p in TEAM_PHONES}]
    assert len(alerts) == 4  # the first alert and the second one, to each of the two
    assert await rows(
        db,
        "select round, count(*) from public.team_alerts"
        " where escalation_id = %s group by round order by round",
        (escalation_id,),
    ) == [(1, 2), (2, 2)]


async def test_an_urgency_the_team_already_answered_is_not_alerted_again(
    ctx: WorkerContext, db: Database, phone: str
) -> None:
    conversation_id, escalation_id = await _urgency(ctx, db, phone, minutes_ago=30)
    async with db.clinic_transaction(CLINIC_A) as conn:
        await conn.execute(
            """
            insert into public.messages (clinic_id, conversation_id, direction, sender, type, content,
                                         delivery_status, meta_message_id)
            values (%s, %s, 'outbound', 'human', 'text', 'Ya te llamamos', 'sent', %s)
            """,
            (CLINIC_A, conversation_id, wamid()),
        )

    await watch(ctx, CLINIC_A, datetime.now(UTC))

    assert (await one(db, "select realerted_at from public.escalations where id = %s", (escalation_id,)))[
        0
    ] is None


async def test_a_message_without_an_answer_after_5_minutes_reaches_jared_once_an_hour(
    ctx: WorkerContext, db: Database, meta: FakeMeta, phone: str
) -> None:
    patient_id = await consenting_patient(db, phone)
    async with db.clinic_transaction(CLINIC_A) as conn:
        cursor = await conn.execute(
            "insert into public.conversations (clinic_id, patient_id) values (%s, %s) returning id",
            (CLINIC_A, patient_id),
        )
        row = await cursor.fetchone()
        assert row is not None
        await conn.execute(
            """
            insert into public.messages (clinic_id, conversation_id, direction, sender, type, content,
                                         meta_message_id, created_at)
            values (%s, %s, 'inbound', 'patient', 'text', 'hola', %s, now() - interval '6 minutes')
            """,
            (CLINIC_A, row[0], wamid()),
        )
    try:
        await watch(ctx, CLINIC_A, datetime.now(UTC))
        await watch(ctx, CLINIC_A, datetime.now(UTC))
        match = {"kind": "ops_alert", "alert": "unanswered", "clinic_id": str(CLINIC_A)}
        assert await queued_count(db, match) == 1
        await run_matching(ctx, match)
    finally:
        _admin("update public.messages set handled_at = now() where conversation_id = %s", (row[0],))

    [alert] = [m for m in meta.sent if m.get("to") == OPS_PHONE.removeprefix("+")]
    assert alert["template"]["name"] == "alerta_axon"
    parameters = [p["text"] for p in alert["template"]["components"][0]["parameters"]]
    assert parameters[:2] == ["mensajes de pacientes sin respuesta", "Clínica Dental Sonrisa"]
    assert parameters[2].isdigit()  # how many: never which patient


# The scheduler -----------------------------------------------------------------------------------


async def test_each_run_is_claimed_once_even_with_two_workers(ctx: WorkerContext, db: Database) -> None:
    now = datetime(2031, 3, 4, 9, 30, 5, tzinfo=UTC)

    first, second = await asyncio.gather(tick(ctx, now), tick(ctx, now))
    assert sorted(first + second) == ["cleanup", "reminders", "watchdog"]
    assert await tick(ctx, now + timedelta(seconds=20)) == []
    assert await tick(ctx, now + timedelta(minutes=1)) == ["watchdog"]

    slot = "2031-03-04T09:30:00Z"
    for clinic_id in (CLINIC_A, CLINIC_B):
        for task in ("reminders", "watchdog", "cleanup"):
            match = {"kind": "scheduled", "task": task, "clinic_id": str(clinic_id)}
            assert await queued_count(db, match) >= 1
    assert await queued_count(db, {"kind": "scheduled", "task": "reminders", "slot": slot}) >= 2


async def test_the_scheduler_checks_in_with_sentry_every_minute(ctx: WorkerContext) -> None:
    beats: list[bool] = []
    stop = asyncio.Event()
    ctx.clock = _at(datetime(2031, 3, 5, 9, 0, tzinfo=UTC))

    def heartbeat(healthy: bool) -> None:
        beats.append(healthy)
        stop.set()

    await asyncio.wait_for(run_scheduler(ctx, stop, heartbeat), timeout=10)

    assert beats == [True]


async def test_jobs_that_exhausted_their_retries_reach_jared(ctx: WorkerContext, db: Database) -> None:
    _admin("select pgmq.send('whatsapp_inbound_dlq', %s)", (Jsonb({"kind": "prueba", "failure": {}}),))
    try:
        assert "watchdog" in await tick(ctx, datetime.now(UTC))
        assert await queued_count(db, {"kind": "ops_alert", "alert": "dead_letters"}) == 1
    finally:
        _admin("delete from pgmq.q_whatsapp_inbound_dlq where message->>'kind' = 'prueba'")


# Meta's notices about the account ----------------------------------------------------------------


def _account_notice(field: str, value: dict[str, Any], waba_id: str = WABA_A) -> dict[str, Any]:
    """Each run's notice is new to the database (stored notices are deduplicated by content)."""
    return webhook_payload({"field": field, "value": {**value, "test_run": str(uuid4())}}, waba_id=waba_id)


async def _deliver_account(ctx: WorkerContext, db: Database, payload: dict[str, Any]) -> None:
    """Routes the notice as the webhook does and processes it (and nothing other tests left)."""
    _, changes = split_payload(payload)
    await _route_account_changes(db, ctx.settings, changes)
    stored = await rows(
        db,
        "select id from public.webhook_events where clinic_id = %s and dedup_key = any(%s)",
        (CLINIC_A, [change.dedup_key for change in changes]),
    )
    assert len(stored) == len(changes)
    for (event_id,) in stored:
        await run_matching(ctx, {"kind": "event", "event_id": str(event_id)})


async def test_a_paused_template_stops_its_reminders_and_jared_hears(
    ctx: WorkerContext, db: Database, phone: str
) -> None:
    template = ("recordatorio_cita", CLINIC_A)
    template_id = 1_000_000 + uuid4().int % 10**9
    try:
        await _deliver_account(
            ctx,
            db,
            _account_notice(
                "message_template_status_update",
                {
                    "event": "PAUSED",
                    "message_template_id": template_id,
                    "message_template_name": "recordatorio_cita",
                    "message_template_language": "es_MX",
                    "reason": None,
                    "other_info": {"title": "FIRST_PAUSE", "description": "Calidad baja"},
                },
            ),
        )
        status, meta_id = await one(
            db,
            "select status, meta_template_id from public.templates where name = %s and clinic_id = %s",
            template,
        )
        assert (status, meta_id) == ("paused", str(template_id))
        assert (
            await queued_count(db, {"kind": "ops_alert", "alert": "template", "clinic_id": str(CLINIC_A)})
            == 1
        )
        _, _, starts_at = await _patient_with_appointment(db, phone)
        assert await queue_reminders(ctx, CLINIC_A, starts_at - timedelta(hours=23)) == 0
    finally:
        _admin(
            "update public.templates set status = 'approved', status_reason = null"
            " where name = %s and clinic_id = %s",
            template,
        )


async def test_a_template_moved_to_marketing_is_kept_as_marketing(ctx: WorkerContext, db: Database) -> None:
    template = ("cita_confirmada", CLINIC_A)
    try:
        await _deliver_account(
            ctx,
            db,
            _account_notice(
                "template_category_update",
                {
                    "message_template_id": 2689556908129832,
                    "message_template_name": "cita_confirmada",
                    "message_template_language": "es_MX",
                    "previous_category": "UTILITY",
                    "new_category": "MARKETING",
                },
            ),
        )
        assert (
            await one(
                db, "select category from public.templates where name = %s and clinic_id = %s", template
            )
        )[0] == "marketing"
    finally:
        _admin(
            "update public.templates set category = 'utility' where name = %s and clinic_id = %s", template
        )


async def test_a_quality_drop_of_the_clinic_number_reaches_jared(ctx: WorkerContext, db: Database) -> None:
    await _deliver_account(
        ctx,
        db,
        _account_notice(
            "phone_number_quality_update",
            {"display_phone_number": "529510000001", "event": "FLAGGED", "current_limit": "TIER_250"},
        ),
    )

    assert await queued_count(db, {"kind": "ops_alert", "alert": "quality", "clinic_id": str(CLINIC_A)}) == 1


async def test_a_notice_about_axon_own_account_reaches_jared_directly(
    ctx: WorkerContext, db: Database, settings: Settings
) -> None:
    ctx.settings = settings.model_copy(update={"axon_waba_id": "900000000000099"})
    _, changes = split_payload(
        _account_notice(
            "message_template_status_update",
            {
                "event": "REJECTED",
                "message_template_id": 1,
                "message_template_name": "alerta_axon",
                "message_template_language": "es_MX",
                "reason": "INCORRECT_CATEGORY",
            },
            waba_id="900000000000099",
        )
    )

    await _route_account_changes(db, ctx.settings, changes)

    assert await queued_count(db, {"kind": "ops_alert", "alert": "template", "clinic_id": None}) == 1


# The daily cleanup -------------------------------------------------------------------------------


async def test_the_cleanup_runs_at_3_in_the_clinic_and_removes_only_what_expired(
    settings: Settings, db: Database, meta: FakeMeta, phone: str
) -> None:
    store = MemoryStore()
    ctx = make_context(settings, db, meta, store=store)
    patient_id = await consenting_patient(db, phone)
    old_path, new_path, orphan, young_orphan = (f"{CLINIC_A}/{uuid4()}/{uuid4()}.ogg" for _ in range(4))
    b_path = f"{CLINIC_B}/{uuid4()}/{uuid4()}.ogg"
    for path in (old_path, new_path, orphan, young_orphan):
        await store.put(CLINIC_A, path, b"audio", "audio/ogg")
    await store.put(CLINIC_B, b_path, b"audio", "audio/ogg")
    for path in (old_path, new_path, orphan, b_path):
        store.stored_at[path] -= timedelta(days=2)
    async with db.clinic_transaction(CLINIC_A) as conn:
        cursor = await conn.execute(
            "insert into public.conversations (clinic_id, patient_id) values (%s, %s) returning id",
            (CLINIC_A, patient_id),
        )
        row = await cursor.fetchone()
        assert row is not None
        conversation_id = row[0]
        for path, age, transcription in ((old_path, "100 days", "ya no me duele"), (new_path, "1 day", None)):
            await conn.execute(
                """
                insert into public.messages (clinic_id, conversation_id, direction, sender, type, media_path,
                                             transcription, meta_message_id, handled_at, created_at)
                values (%s, %s, 'inbound', 'patient', 'audio', %s, %s, %s, now(), now() - %s::interval)
                """,
                (CLINIC_A, conversation_id, path, transcription, wamid(), age),
            )
        cursor = await conn.execute(
            """
            insert into public.webhook_events (clinic_id, field, kind, dedup_key, payload, processed_at)
            values (%s, 'messages', 'message', %s, '{"x": 1}', now() - interval '8 days'),
                   (%s, 'messages', 'message', %s, '{"x": 1}', now() - interval '1 day')
            returning id
            """,
            (CLINIC_A, f"test:{uuid4()}", CLINIC_A, f"test:{uuid4()}"),
        )
        old_event, new_event = [r[0] for r in await cursor.fetchall()]
    local_today = datetime.now(CLINIC_TZ).date()
    at_two = datetime(local_today.year, local_today.month, local_today.day, 2, 30, tzinfo=CLINIC_TZ)

    await clean_up(ctx, CLINIC_A, at_two.astimezone(UTC))  # not its hour: nothing happens
    assert old_path in store.files
    await clean_up(ctx, CLINIC_A, (at_two + timedelta(hours=1)).astimezone(UTC))

    assert set(store.files) == {new_path, young_orphan, b_path}
    assert await rows(
        db,
        "select media_path, transcription from public.messages"
        " where conversation_id = %s order by created_at",
        (conversation_id,),
    ) == [(None, "ya no me duele"), (new_path, None)]
    payloads = dict(
        await rows(
            db,
            "select id, payload is not null from public.webhook_events where id = any(%s)",
            ([old_event, new_event],),
        )
    )
    assert payloads == {old_event: False, new_event: True}


# The daily cap of transcriptions -----------------------------------------------------------------


async def test_past_the_clinic_daily_cap_a_voice_note_goes_to_the_team_unheard(
    settings: Settings, db: Database, meta: FakeMeta, phone: str
) -> None:
    transcriber = FakeTranscription("me duele")
    ctx = make_context(
        settings.model_copy(update={"transcription_daily_cap_per_clinic": 0}),
        db,
        meta,
        transcriber=transcriber,
        model=ScriptedModel(),
    )
    media_id = str(uuid4().int)[:15]
    meta.media[media_id] = (VOICE_NOTE, "audio/ogg")

    conversation_id = await turn(ctx, db, phone, audio_from(phone, media_id))

    assert transcriber.calls == []
    assert await rows(
        db, "select reason from public.escalations where conversation_id = %s", (conversation_id,)
    ) == [("nota de voz sin transcribir",)]
