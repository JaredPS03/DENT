"""The WhatsApp round trip against the local database, with Meta's official example payloads:
webhook item -> queue -> worker -> patient, conversation and message -> turn -> echo -> Graph API.

Covers duplicates, out-of-order webhooks, a burst of five messages, a voice note, a failed
status, a coexistence echo, the 24 h window, the turn lease, retries and the dead-letter queue.
"""

import asyncio
import time
from collections.abc import AsyncIterator
from typing import Any
from uuid import UUID, uuid4

import httpx
import pytest
from psycopg.types.json import Jsonb
from psycopg_pool import AsyncConnectionPool

from app.bot.transcription import FakeTranscription, TranscriptionUnavailableError
from app.core.config import Settings
from app.db.connection import Database
from app.jobs import queue, worker
from app.jobs.context import WorkerContext
from app.jobs.queue import ConversationJob, QueuedJob
from app.jobs.timing import MAX_ATTEMPTS
from app.jobs.worker import run_job
from tests.conftest import CLINIC_A, PHONE_NUMBER_ID_A, make_settings
from tests.support.meta_fixtures import meta_payload
from tests.support.whatsapp_world import (
    VOICE_NOTE,
    FakeMeta,
    MemoryStore,
    deliver,
    make_context,
    queued_count,
    random_phone,
    run_events,
    run_matching,
)

pytestmark = pytest.mark.anyio


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
def phone() -> str:
    return random_phone()


@pytest.fixture
def settings(phone: str) -> Settings:
    return make_settings(echo_bot_enabled=True, whatsapp_allowed_test_numbers=(phone,))


@pytest.fixture
def meta() -> FakeMeta:
    return FakeMeta()


@pytest.fixture
def ctx(settings: Settings, db: Database, meta: FakeMeta) -> WorkerContext:
    return make_context(settings, db, meta)


def meta_now(name: str, **overrides: Any) -> dict[str, Any]:
    """One of Meta's examples, happening now (the examples' own dates are long past)."""
    overrides.setdefault("timestamp", int(time.time()))
    return meta_payload(name, **overrides)


def wa_id(phone: str) -> str:
    """How Meta's wa_id writes a Mexican mobile: with the old "1" after the country code."""
    return "521" + phone.removeprefix("+52")


def wamid() -> str:
    return f"wamid.TEST.{uuid4().hex}"


def text_from(phone: str, *, message_id: str | None = None, timestamp: int | None = None) -> dict[str, Any]:
    """Meta's text example from `phone`, sent now (the example's own date is long past)."""
    return meta_now(
        "text",
        phone_number_id=PHONE_NUMBER_ID_A,
        user_phone=wa_id(phone),
        message_id=message_id or wamid(),
        timestamp=timestamp or int(time.time()),
    )


def audio_from(phone: str, media_id: str) -> dict[str, Any]:
    payload = meta_now(
        "audio",
        phone_number_id=PHONE_NUMBER_ID_A,
        user_phone=wa_id(phone),
        message_id=wamid(),
        timestamp=int(time.time()),
    )
    payload["entry"][0]["changes"][0]["value"]["messages"][0]["audio"]["id"] = media_id
    return payload


async def one(db: Database, sql: str, params: tuple[Any, ...] = ()) -> Any:
    found = await rows(db, sql, params)
    assert len(found) == 1, found
    return found[0]


async def rows(db: Database, sql: str, params: tuple[Any, ...] = ()) -> list[tuple[Any, ...]]:
    async with db.clinic_transaction(CLINIC_A) as conn:
        cursor = await conn.execute(sql, params)
        return await cursor.fetchall()


async def conversation_of(db: Database, phone: str) -> UUID:
    row = await one(
        db,
        "select c.id from public.conversations c join public.patients p on p.id = c.patient_id"
        " where p.phone = %s",
        (phone,),
    )
    return UUID(str(row[0]))


async def patient_pauses(db: Database, conversation_id: UUID) -> None:
    """Moves the patient's pending messages 10 s into the past: their burst is over."""
    async with db.clinic_transaction(CLINIC_A) as conn:
        await conn.execute(
            """
            update public.messages set created_at = created_at - interval '10 seconds'
            where conversation_id = %s and handled_at is null
            """,
            (conversation_id,),
        )


async def take_turns(ctx: WorkerContext, conversation_id: UUID) -> None:
    await patient_pauses(ctx.db, conversation_id)
    await run_matching(ctx, {"kind": "conversation", "conversation_id": str(conversation_id)})


async def outbound_of(db: Database, conversation_id: UUID) -> list[tuple[Any, ...]]:
    return await rows(
        db,
        """
        select sender, type, content, delivery_status, meta_message_id, billing_category, delivery_error_code
        from public.messages where conversation_id = %s and direction = 'outbound' order by created_at
        """,
        (conversation_id,),
    )


# The happy path -------------------------------------------------------------------------------


async def test_a_text_message_goes_round_trip(
    ctx: WorkerContext, db: Database, meta: FakeMeta, phone: str
) -> None:
    await run_events(ctx, await deliver(db, text_from(phone)))

    patient = await one(db, "select phone, bsuid, source from public.patients where phone = %s", (phone,))
    assert patient == (phone, None, "bot")  # +52 and 10 digits, from Meta's 521… wa_id
    conversation_id = await conversation_of(db, phone)
    status, window = await one(
        db,
        "select status, window_expires_at > now() from public.conversations where id = %s",
        (conversation_id,),
    )
    assert (status, window) == ("bot", True)
    assert await queued_count(db, {"conversation_id": str(conversation_id)}) == 1

    await take_turns(ctx, conversation_id)

    assert meta.texts() == ["Eco: Does it come in another color?"]
    sent = meta.sent[0]
    assert sent["to"] == phone.removeprefix("+")
    assert sent["text"]["preview_url"] is False
    [reply] = await outbound_of(db, conversation_id)
    assert reply[:4] == ("bot", "text", "Eco: Does it come in another color?", "sent")
    assert reply[4].startswith("wamid.OUT.")
    assert reply[5] == "service"
    # The callback data is the reply's own row, so statuses find it even without Meta's id.
    reply_id = (await one(db, "select id from public.messages where meta_message_id = %s", (reply[4],)))[0]
    assert sent["biz_opaque_callback_data"] == str(reply_id)
    handled = await rows(
        db,
        "select handled_at is not null from public.messages"
        " where conversation_id = %s and direction = 'inbound'",
        (conversation_id,),
    )
    assert handled == [(True,)]


async def test_a_patient_known_only_by_bsuid(ctx: WorkerContext, db: Database, meta: FakeMeta) -> None:
    bsuid = f"MX.{uuid4().int}"[:30]
    payload = meta_now(
        "text_bsuid_only", phone_number_id=PHONE_NUMBER_ID_A, user_bsuid=bsuid, message_id=wamid()
    )
    await run_events(ctx, await deliver(db, payload))
    patient_id, patient_phone = await one(
        db, "select id, phone from public.patients where bsuid = %s", (bsuid,)
    )
    assert patient_phone is None
    conversation_id = (
        await one(db, "select id from public.conversations where patient_id = %s", (patient_id,))
    )[0]

    await take_turns(ctx, conversation_id)

    # Outside production a BSUID cannot be checked against the test numbers: nothing leaves.
    assert meta.sent == []
    [reply] = await outbound_of(db, conversation_id)
    assert (reply[3], reply[6]) == ("failed", 131030)


# Duplicates and disorder ------------------------------------------------------------------------


async def test_a_message_delivered_twice_is_stored_and_answered_once(
    ctx: WorkerContext, db: Database, meta: FakeMeta, phone: str
) -> None:
    message_id = wamid()
    first = await deliver(db, text_from(phone, message_id=message_id))
    again = await deliver(db, text_from(phone, message_id=message_id))
    assert first == again  # the webhook kept one item
    await run_events(ctx, first)
    # The same job run twice (a worker died after finishing it) changes nothing.
    async with db.clinic_transaction(CLINIC_A) as conn:
        await conn.execute("update public.webhook_events set processed_at = null where id = %s", (first[0],))
        await queue.enqueue(conn, queue.EventJob(clinic_id=CLINIC_A, event_id=first[0]))
    await run_events(ctx, first)

    conversation_id = await conversation_of(db, phone)
    count = await one(
        db,
        "select count(*) from public.messages where conversation_id = %s and direction = 'inbound'",
        (conversation_id,),
    )
    assert count == (1,)
    await take_turns(ctx, conversation_id)
    await take_turns(ctx, conversation_id)
    assert len(meta.sent) == 1


async def test_messages_arriving_out_of_order_are_answered_in_the_order_they_were_sent(
    ctx: WorkerContext, db: Database, meta: FakeMeta, phone: str
) -> None:
    now = int(time.time())
    second = text_from(phone, timestamp=now - 5)
    second["entry"][0]["changes"][0]["value"]["messages"][0]["text"]["body"] = "segundo"
    first = text_from(phone, timestamp=now - 10)
    first["entry"][0]["changes"][0]["value"]["messages"][0]["text"]["body"] = "primero"
    await run_events(ctx, await deliver(db, second))
    await run_events(ctx, await deliver(db, first))

    await take_turns(ctx, await conversation_of(db, phone))

    assert meta.texts() == ["Eco (2 mensajes): primero / segundo"]


async def test_delivery_statuses_never_go_back(
    ctx: WorkerContext, db: Database, meta: FakeMeta, phone: str
) -> None:
    await run_events(ctx, await deliver(db, text_from(phone)))
    conversation_id = await conversation_of(db, phone)
    await take_turns(ctx, conversation_id)
    out_id = (await outbound_of(db, conversation_id))[0][4]

    for status in ("read", "delivered", "sent"):  # Meta delivered them backwards
        payload = meta_now(
            "status_sent_pricing",
            phone_number_id=PHONE_NUMBER_ID_A,
            user_phone=wa_id(phone),
            message_id=out_id,
        )
        payload["entry"][0]["changes"][0]["value"]["statuses"][0]["status"] = status
        await run_events(ctx, await deliver(db, payload))

    status, billable, category = await one(
        db,
        "select delivery_status, billable, billing_category from public.messages where meta_message_id = %s",
        (out_id,),
    )
    # The read stays, and Meta's pricing block is the truth about what was charged.
    assert (status, billable, category) == ("read", True, "marketing")


async def test_a_status_faster_than_the_worker_finds_its_message_by_callback_data(
    ctx: WorkerContext, db: Database, meta: FakeMeta, phone: str
) -> None:
    await run_events(ctx, await deliver(db, text_from(phone)))
    conversation_id = await conversation_of(db, phone)
    # Meta answers the send with a lost connection: the outcome is unknown and it is never resent.
    meta.script.append(httpx.ReadTimeout("no answer"))
    await take_turns(ctx, conversation_id)
    [(message_id,)] = await rows(
        db,
        "select id from public.messages where conversation_id = %s and direction = 'outbound'",
        (conversation_id,),
    )
    await take_turns(ctx, conversation_id)
    assert meta.sent == []

    # Its status webhook arrives with the callback data Axon sent along.
    payload = meta_now(
        "status_sent_pricing",
        phone_number_id=PHONE_NUMBER_ID_A,
        user_phone=wa_id(phone),
        message_id="wamid.LATE." + uuid4().hex,
        extra={"biz_opaque_callback_data": str(message_id)},
    )
    await run_events(ctx, await deliver(db, payload))
    status, meta_id = await one(
        db, "select delivery_status, meta_message_id from public.messages where id = %s", (message_id,)
    )
    assert status == "sent"
    assert meta_id.startswith("wamid.LATE.")


async def test_a_failed_status_keeps_meta_error_code(
    ctx: WorkerContext, db: Database, meta: FakeMeta, phone: str
) -> None:
    await run_events(ctx, await deliver(db, text_from(phone)))
    conversation_id = await conversation_of(db, phone)
    await take_turns(ctx, conversation_id)
    out_id = (await outbound_of(db, conversation_id))[0][4]

    payload = meta_now(
        "status_failed", phone_number_id=PHONE_NUMBER_ID_A, user_phone=wa_id(phone), message_id=out_id
    )
    await run_events(ctx, await deliver(db, payload))

    assert (await outbound_of(db, conversation_id))[0][3:7:3] == ("failed", 131049)


# Bursts and the lease -------------------------------------------------------------------------------


async def test_a_burst_of_five_messages_gets_one_answer(
    ctx: WorkerContext, db: Database, meta: FakeMeta, phone: str
) -> None:
    for number in range(1, 6):  # five messages within two seconds
        payload = text_from(phone, timestamp=int(time.time()) - 2 + number // 3)
        payload["entry"][0]["changes"][0]["value"]["messages"][0]["text"]["body"] = f"mensaje {number}"
        await run_events(ctx, await deliver(db, payload))
    conversation_id = await conversation_of(db, phone)

    # While the patient is still typing nobody answers; the turn comes back when they pause.
    match = {"kind": "conversation", "conversation_id": str(conversation_id)}
    assert await run_matching(ctx, match, passes=1) == 5
    assert meta.sent == []
    assert await queued_count(db, match) >= 1

    await take_turns(ctx, conversation_id)
    assert meta.texts() == ["Eco (5 mensajes): mensaje 1 / mensaje 2 / mensaje 3 / mensaje 4 / mensaje 5"]


async def test_two_workers_on_one_conversation_answer_once(
    settings: Settings, db: Database, meta: FakeMeta, phone: str
) -> None:
    workers = [make_context(settings, db, meta) for _ in range(5)]
    await run_events(workers[0], await deliver(db, text_from(phone)))
    conversation_id = await conversation_of(db, phone)
    await patient_pauses(db, conversation_id)
    jobs = [
        QueuedJob(
            msg_id=-index,
            attempt=1,
            body=ConversationJob(clinic_id=CLINIC_A, conversation_id=conversation_id).model_dump(mode="json"),
        )
        for index in range(1, 6)
    ]
    # msg_ids that do not exist: settling them in the queue is a no-op.
    await asyncio.gather(*(run_job(worker, job) for worker, job in zip(workers, jobs, strict=True)))
    await run_matching(workers[0], {"kind": "conversation", "conversation_id": str(conversation_id)})

    assert len(meta.sent) == 1
    lease = await one(
        db, "select turn_lease_token from public.conversations where id = %s", (conversation_id,)
    )
    assert lease == (None,)


# Retries -------------------------------------------------------------------------------------------


async def test_meta_down_retries_and_sends_once(
    ctx: WorkerContext, db: Database, meta: FakeMeta, phone: str
) -> None:
    await run_events(ctx, await deliver(db, text_from(phone)))
    conversation_id = await conversation_of(db, phone)
    meta.script.extend([httpx.Response(503), httpx.ConnectError("refused")])

    await take_turns(ctx, conversation_id)

    assert len(meta.sent) == 1
    assert (await outbound_of(db, conversation_id))[0][3] == "sent"


async def test_a_data_error_is_not_retried(
    ctx: WorkerContext, db: Database, meta: FakeMeta, phone: str
) -> None:
    await run_events(ctx, await deliver(db, text_from(phone)))
    conversation_id = await conversation_of(db, phone)
    meta.script.append(
        httpx.Response(400, json={"error": {"code": 131026, "message": "Message undeliverable"}})
    )

    await take_turns(ctx, conversation_id)

    assert meta.sent == []
    assert (await outbound_of(db, conversation_id))[0][3:7:3] == ("failed", 131026)


async def _queued(db: Database, body: dict[str, Any]) -> int:
    async with db.unscoped_transaction() as conn:
        cursor = await conn.execute("select pgmq.send(%s::text, %s::jsonb)", (queue.QUEUE, Jsonb(body)))
        row = await cursor.fetchone()
    assert row is not None
    return int(row[0])


async def test_a_failing_job_waits_longer_each_time_then_becomes_a_dead_letter(
    ctx: WorkerContext, db: Database, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def explode(*args: object, **kwargs: object) -> None:
        raise RuntimeError("boom")

    monkeypatch.setattr(worker, "_dispatch", explode)
    message_id = str(uuid4())
    body = {"kind": "expire_hold", "clinic_id": str(CLINIC_A), "message_id": message_id}
    msg_id = await _queued(db, body)

    await run_job(ctx, QueuedJob(msg_id, 1, body))
    hidden_for = await one(
        db,
        "select extract(epoch from vt - clock_timestamp()) from pgmq.q_whatsapp_inbound where msg_id = %s",
        (msg_id,),
    )
    assert 3 < hidden_for[0] <= 6  # first retry in about 5 s

    await run_job(ctx, QueuedJob(msg_id, MAX_ATTEMPTS, body))
    assert await queued_count(db, {"message_id": message_id}) == 0
    assert (
        await queued_count(
            db, {"message_id": message_id, "failure": {"error": "RuntimeError"}}, queue.DEAD_LETTERS
        )
        == 1
    )


async def test_an_unreadable_job_goes_straight_to_the_dead_letter_queue(
    ctx: WorkerContext, db: Database
) -> None:
    marker = uuid4().hex
    body = {"kind": "teleport", "marker": marker}
    await run_job(ctx, QueuedJob(await _queued(db, body), 1, body))
    assert await queued_count(db, {"marker": marker}) == 0
    assert await queued_count(db, {"marker": marker}, queue.DEAD_LETTERS) == 1


# Voice notes ----------------------------------------------------------------------------------------


async def test_a_voice_note_is_stored_privately_transcribed_and_answered(
    settings: Settings, db: Database, meta: FakeMeta, phone: str
) -> None:
    store = MemoryStore()
    transcriber = FakeTranscription("Hola, quiero una cita para limpieza")
    ctx = make_context(settings, db, meta, store=store, transcriber=transcriber)
    media_id = str(uuid4().int)[:16]
    meta.media[media_id] = (VOICE_NOTE, "audio/ogg; codecs=opus")
    payload = meta_now(
        "audio", phone_number_id=PHONE_NUMBER_ID_A, user_phone=wa_id(phone), message_id=wamid()
    )
    payload["entry"][0]["changes"][0]["value"]["messages"][0]["audio"]["id"] = media_id
    await run_events(ctx, await deliver(db, payload))
    conversation_id = await conversation_of(db, phone)

    await take_turns(ctx, conversation_id)

    message_id, media_path, meta_media_id, transcription = await one(
        db,
        "select id, media_path, meta_media_id, transcription from public.messages"
        " where conversation_id = %s and direction = 'inbound'",
        (conversation_id,),
    )
    assert media_path == f"{CLINIC_A}/{conversation_id}/{message_id}.ogg"  # no patient data in the path
    assert store.files[media_path] == (VOICE_NOTE, "audio/ogg")
    assert meta_media_id is None
    assert transcription == "Hola, quiero una cita para limpieza"
    assert transcriber.calls == [(len(VOICE_NOTE), "audio/ogg")]
    assert meta.texts() == ["Eco: (nota de voz) Hola, quiero una cita para limpieza"]


async def test_a_transcription_outage_retries_and_the_last_attempt_still_answers(
    settings: Settings, db: Database, meta: FakeMeta, phone: str
) -> None:
    transcriber = FakeTranscription()
    transcriber.failure = TranscriptionUnavailableError("http 503")
    ctx = make_context(settings, db, meta, transcriber=transcriber)
    media_id = str(uuid4().int)[:16]
    meta.media[media_id] = (VOICE_NOTE, "audio/ogg; codecs=opus")
    payload = meta_now(
        "audio", phone_number_id=PHONE_NUMBER_ID_A, user_phone=wa_id(phone), message_id=wamid()
    )
    payload["entry"][0]["changes"][0]["value"]["messages"][0]["audio"]["id"] = media_id
    await run_events(ctx, await deliver(db, payload))
    conversation_id = await conversation_of(db, phone)
    await patient_pauses(db, conversation_id)

    job_match = {"kind": "conversation", "conversation_id": str(conversation_id)}
    # Every attempt fails until the last one, which answers without the transcription.
    async with db.unscoped_transaction() as conn:
        await conn.execute(
            "update pgmq.q_whatsapp_inbound set read_ct = %s where message @> %s",
            (MAX_ATTEMPTS - 1, Jsonb(job_match)),
        )
    await run_matching(ctx, job_match)

    assert meta.texts() == ["Eco: (nota de voz sin transcribir)"]


async def test_media_from_a_host_that_is_not_meta_is_never_fetched(
    ctx: WorkerContext, db: Database, meta: FakeMeta, phone: str
) -> None:
    media_id = str(uuid4().int)[:16]
    meta.media[media_id] = (VOICE_NOTE, "audio/ogg")
    meta.media_url_override[media_id] = "https://attacker.example/steal"
    payload = meta_now(
        "audio", phone_number_id=PHONE_NUMBER_ID_A, user_phone=wa_id(phone), message_id=wamid()
    )
    payload["entry"][0]["changes"][0]["value"]["messages"][0]["audio"]["id"] = media_id
    await run_events(ctx, await deliver(db, payload))
    conversation_id = await conversation_of(db, phone)

    await take_turns(ctx, conversation_id)

    media_path, meta_media_id = await one(
        db,
        "select media_path, meta_media_id from public.messages"
        " where conversation_id = %s and direction = 'inbound'",
        (conversation_id,),
    )
    assert (media_path, meta_media_id) == (None, None)  # given up, never retried
    assert meta.texts() == ["Eco: (nota de voz sin transcribir)"]


# Coexistence and the window ---------------------------------------------------------------------------


async def test_a_reply_from_the_clinic_app_is_kept_as_human_and_pauses_the_bot(
    ctx: WorkerContext, db: Database, meta: FakeMeta, phone: str
) -> None:
    await run_events(ctx, await deliver(db, text_from(phone)))
    conversation_id = await conversation_of(db, phone)
    await take_turns(ctx, conversation_id)
    window_before = (
        await one(db, "select window_expires_at from public.conversations where id = %s", (conversation_id,))
    )[0]

    echo = meta_now(
        "echo_text", phone_number_id=PHONE_NUMBER_ID_A, user_phone=wa_id(phone), message_id=wamid()
    )
    echo["entry"][0]["changes"][0]["value"]["message_echoes"][0]["timestamp"] = int(
        (await one(db, "select extract(epoch from now())::bigint"))[0]
    )
    await run_events(ctx, await deliver(db, echo))

    sender, kind = (await outbound_of(db, conversation_id))[-1][:2]
    assert (sender, kind) == ("human", "text")
    paused, window_after = await one(
        db,
        "select bot_paused_until > now() + interval '119 minutes', window_expires_at"
        " from public.conversations where id = %s",
        (conversation_id,),
    )
    assert paused is True  # the clinic's 120 minutes
    assert window_after == window_before  # what the clinic sends never opens the window

    # The patient writes again: the message reaches the inbox, the bot stays quiet.
    await run_events(ctx, await deliver(db, text_from(phone)))
    await take_turns(ctx, conversation_id)
    assert len(meta.sent) == 1
    unhandled = await one(
        db,
        "select count(*) from public.messages"
        " where conversation_id = %s and direction = 'inbound' and handled_at is null",
        (conversation_id,),
    )
    assert unhandled == (0,)


async def test_a_free_message_is_not_sent_once_the_window_closed(
    ctx: WorkerContext, db: Database, meta: FakeMeta, phone: str
) -> None:
    await run_events(ctx, await deliver(db, text_from(phone)))
    conversation_id = await conversation_of(db, phone)
    await patient_pauses(db, conversation_id)
    async with db.clinic_transaction(CLINIC_A) as conn:
        await conn.execute(
            "update public.conversations set window_expires_at = now() - interval '1 minute' where id = %s",
            (conversation_id,),
        )
    await run_matching(ctx, {"kind": "conversation", "conversation_id": str(conversation_id)})

    assert meta.sent == []
    assert (await outbound_of(db, conversation_id))[0][3:7:3] == ("failed", 131047)


async def test_a_group_message_is_not_a_patient_chat(ctx: WorkerContext, db: Database, phone: str) -> None:
    payload = text_from(phone)
    payload["entry"][0]["changes"][0]["value"]["messages"][0]["group_id"] = "120363000000000000"
    await run_events(ctx, await deliver(db, payload))
    assert await rows(db, "select id from public.patients where phone = %s", (phone,)) == []


async def test_dead_letters_can_be_replayed_once_the_cause_is_fixed(db: Database) -> None:
    marker = uuid4().hex
    async with db.unscoped_transaction() as conn:
        await conn.execute(
            "select pgmq.send(%s::text, %s::jsonb)",
            (queue.DEAD_LETTERS, Jsonb({"kind": "event", "marker": marker, "failure": {"error": "X"}})),
        )
    assert await queue.replay_dead_letters(db, limit=1000) >= 1
    assert await queued_count(db, {"marker": marker}, queue.DEAD_LETTERS) == 0
    # Back in the queue as a new job, without the failure note.
    assert await queued_count(db, {"marker": marker}) == 1
    assert await queued_count(db, {"marker": marker, "failure": {}}) == 0
