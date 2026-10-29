"""/api/conversaciones through HTTP, and the worker sending what the panel queued."""

import time
from collections.abc import Iterator
from typing import Any
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient

from app.application import create_app
from app.core.config import Settings
from app.jobs.context import WorkerContext
from tests.conftest import CLINIC_A, CLINIC_B, PHONE_NUMBER_ID_A, make_settings
from tests.integration.conftest import rows_as_clinic
from tests.support.meta_fixtures import meta_payload
from tests.support.sessions import TestKeys
from tests.support.whatsapp_world import (
    FakeMeta,
    deliver,
    make_context,
    random_phone,
    run_events,
    run_matching,
)

OWNER_A = UUID("a1000000-0000-4000-8000-000000000001")
ASSISTANT_A = UUID("a1000000-0000-4000-8000-000000000002")
CONVERSATION_B = UUID("b7000000-0000-4000-8000-000000000001")


@pytest.fixture
def phone() -> str:
    return random_phone()


@pytest.fixture
def settings(database_url: str, phone: str) -> Settings:
    return make_settings(database_url=database_url, whatsapp_allowed_test_numbers=(phone,))


@pytest.fixture
def keys() -> TestKeys:
    return TestKeys()


@pytest.fixture
def api(settings: Settings, keys: TestKeys) -> Iterator[TestClient]:
    with TestClient(create_app(settings, jwks=keys)) as client:
        yield client


@pytest.fixture
def meta() -> FakeMeta:
    return FakeMeta()


@pytest.fixture
def worker(api: TestClient, settings: Settings, meta: FakeMeta) -> WorkerContext:
    return make_context(settings, api.app.state.db, meta)  # type: ignore[attr-defined]


@pytest.fixture
def reopen_template(database_url: str) -> Iterator[None]:
    """retomar_conversacion approved by Meta for clinic A, only during the test."""
    sql = "update public.templates set status = %s where name = 'retomar_conversacion' returning 1"
    rows_as_clinic(database_url, CLINIC_A, sql, ("approved",))
    yield
    rows_as_clinic(database_url, CLINIC_A, sql, ("pending",))


def new_conversation(database_url: str, phone: str, *, window_hours: float, name: str | None = None) -> UUID:
    patient = rows_as_clinic(
        database_url,
        CLINIC_A,
        "insert into public.patients (clinic_id, name, phone) values (%s, %s, %s) returning id",
        (CLINIC_A, name, phone),
    )[0][0]
    return UUID(
        str(
            rows_as_clinic(
                database_url,
                CLINIC_A,
                """
                insert into public.conversations (clinic_id, patient_id, window_expires_at)
                values (%s, %s, now() + make_interval(secs => %s)) returning id
                """,
                (CLINIC_A, patient, window_hours * 3600),
            )[0][0]
        )
    )


def run_worker(api: TestClient, worker: WorkerContext, conversation_id: UUID) -> None:
    assert api.portal is not None
    api.portal.call(run_matching, worker, {"kind": "conversation", "conversation_id": str(conversation_id)})


def outbound(database_url: str, conversation_id: UUID) -> list[tuple[Any, ...]]:
    return rows_as_clinic(
        database_url,
        CLINIC_A,
        """
        select sender, type, content, delivery_status, billing_category, delivery_error_code
        from public.messages where conversation_id = %s and direction = 'outbound' order by created_at, id
        """,
        (conversation_id,),
    )


def audit(database_url: str, record_id: UUID) -> list[tuple[Any, ...]]:
    return rows_as_clinic(
        database_url,
        CLINIC_A,
        "select actor_type, actor_user_id, action, changes from public.audit_log where record_id = %s"
        " order by occurred_at, created_at",
        (record_id,),
    )


def say(api: TestClient, keys: TestKeys, conversation_id: UUID, text: str, **extra: Any) -> Any:
    body = {"text": text, "client_message_id": str(extra.pop("client_message_id", uuid4()))}
    return api.post(
        f"/api/conversaciones/{conversation_id}/mensajes",
        json=body,
        headers=keys.headers(extra.pop("user", OWNER_A)),
    )


# Taking and releasing ---------------------------------------------------------------------------


def test_take_and_release_leave_their_audit_trail(
    api: TestClient, keys: TestKeys, database_url: str, phone: str
) -> None:
    conversation_id = new_conversation(database_url, phone, window_hours=20)
    rows_as_clinic(
        database_url,
        CLINIC_A,
        "update public.conversations set bot_paused_until = now() + interval '1 hour' where id = %s"
        " returning 1",
        (conversation_id,),
    )

    taken = api.post(f"/api/conversaciones/{conversation_id}/tomar", headers=keys.headers(ASSISTANT_A))
    assert taken.status_code == 200, taken.text
    assert taken.json()["status"] == "human"
    released = api.post(f"/api/conversaciones/{conversation_id}/soltar", headers=keys.headers(OWNER_A))
    assert released.status_code == 200
    assert (released.json()["status"], released.json()["bot_paused_until"]) == ("bot", None)

    assert audit(database_url, conversation_id) == [
        ("user", ASSISTANT_A, "conversation.take", {"status": ["bot", "human"]}),
        ("user", OWNER_A, "conversation.release", {"status": ["human", "bot"]}),
    ]


def test_another_clinic_conversation_does_not_exist(api: TestClient, keys: TestKeys) -> None:
    for path in ("tomar", "soltar"):
        response = api.post(f"/api/conversaciones/{CONVERSATION_B}/{path}", headers=keys.headers(OWNER_A))
        assert response.status_code == 404
    assert say(api, keys, CONVERSATION_B, "Hola").status_code == 404
    status = rows_as_clinic(
        api.app.state.settings.database_url.get_secret_value(),  # type: ignore[attr-defined]
        CLINIC_B,
        "select status from public.conversations where id = %s",
        (CONVERSATION_B,),
    )
    assert status == [("bot",)]


def test_without_a_session_nothing_happens(api: TestClient, database_url: str, phone: str) -> None:
    conversation_id = new_conversation(database_url, phone, window_hours=20)
    assert api.post(f"/api/conversaciones/{conversation_id}/tomar").status_code == 401
    assert say(api, TestKeys(), conversation_id, "Hola").status_code == 401  # keys Supabase never issued


# Messages inside the window ------------------------------------------------------------------------


def test_a_message_from_the_panel_takes_the_chat_and_goes_out(
    api: TestClient, keys: TestKeys, worker: WorkerContext, meta: FakeMeta, database_url: str, phone: str
) -> None:
    conversation_id = new_conversation(database_url, phone, window_hours=20)

    response = say(api, keys, conversation_id, "Hola, te llamamos en un momento.", user=ASSISTANT_A)

    assert response.status_code == 202, response.text
    queued = response.json()
    assert (queued["status"], queued["reopen_requested"]) == ("pending", False)
    status = rows_as_clinic(
        database_url, CLINIC_A, "select status from public.conversations where id = %s", (conversation_id,)
    )
    assert status == [("human",)]
    run_worker(api, worker, conversation_id)
    assert meta.texts() == ["Hola, te llamamos en un momento."]
    assert outbound(database_url, conversation_id) == [
        ("human", "text", "Hola, te llamamos en un momento.", "sent", "service", None)
    ]
    sent = audit(database_url, UUID(queued["id"]))
    assert sent == [
        (
            "user",
            ASSISTANT_A,
            "message.send",
            {"conversation_id": str(conversation_id), "held": False, "reopen_requested": False},
        )
    ]  # who and when, never the text


def test_a_double_tap_sends_once(
    api: TestClient, keys: TestKeys, worker: WorkerContext, meta: FakeMeta, database_url: str, phone: str
) -> None:
    conversation_id = new_conversation(database_url, phone, window_hours=20)
    client_message_id = uuid4()
    first = say(api, keys, conversation_id, "Claro que sí", client_message_id=client_message_id)
    second = say(api, keys, conversation_id, "Claro que sí", client_message_id=client_message_id)
    assert first.json()["id"] == second.json()["id"]
    run_worker(api, worker, conversation_id)
    assert meta.texts() == ["Claro que sí"]


@pytest.mark.parametrize(
    ("text", "detail"),
    [
        ("", "Escribe un mensaje antes de enviar."),
        ("   ", "Escribe un mensaje antes de enviar."),
        ("a" * 4097, "Tu mensaje es muy largo: máximo 4096 caracteres."),
        ("😀" * 2049, "Tu mensaje es muy largo: máximo 4096 caracteres."),
    ],
)
def test_a_message_over_whatsapp_limits_is_refused(
    api: TestClient, keys: TestKeys, database_url: str, phone: str, text: str, detail: str
) -> None:
    conversation_id = new_conversation(database_url, phone, window_hours=20)
    response = say(api, keys, conversation_id, text)
    assert (response.status_code, response.json()["detail"]) == (422, detail)
    assert outbound(database_url, conversation_id) == []


# Messages after the window closed ---------------------------------------------------------------------


def test_without_the_approved_template_a_closed_window_cannot_be_reopened(
    api: TestClient, keys: TestKeys, database_url: str, phone: str
) -> None:
    conversation_id = new_conversation(database_url, phone, window_hours=-30)
    response = say(api, keys, conversation_id, "¿Sigues interesado en tu cita?")
    assert response.status_code == 409
    assert "Llámale" in response.json()["detail"]
    assert outbound(database_url, conversation_id) == []


def test_a_reopen_template_moved_to_marketing_is_never_sent(
    api: TestClient, keys: TestKeys, database_url: str, phone: str
) -> None:
    sql = (
        "update public.templates set status = %s, category = %s"
        " where name = 'retomar_conversacion' returning 1"
    )
    rows_as_clinic(database_url, CLINIC_A, sql, ("approved", "marketing"))
    try:
        conversation_id = new_conversation(database_url, phone, window_hours=-30)
        assert say(api, keys, conversation_id, "¿Seguimos?").status_code == 409
        assert outbound(database_url, conversation_id) == []
    finally:
        rows_as_clinic(database_url, CLINIC_A, sql, ("pending", "utility"))


@pytest.mark.usefixtures("reopen_template")
def test_a_closed_window_sends_the_template_and_holds_the_text_until_the_patient_answers(
    api: TestClient, keys: TestKeys, worker: WorkerContext, meta: FakeMeta, database_url: str, phone: str
) -> None:
    conversation_id = new_conversation(database_url, phone, window_hours=-30, name="Ana María Prueba")

    first = say(api, keys, conversation_id, "¿Te queda bien el jueves?")
    second = say(api, keys, conversation_id, "Tenemos lugar a las 4 pm.")
    assert (first.status_code, first.json()["status"], first.json()["reopen_requested"]) == (
        202,
        "held",
        True,
    )
    assert (second.json()["status"], second.json()["reopen_requested"]) == (
        "held",
        False,
    )  # one template only
    run_worker(api, worker, conversation_id)

    [template] = meta.sent
    assert template["type"] == "template"
    assert template["template"]["name"] == "retomar_conversacion"
    parameters = template["template"]["components"][0]["parameters"]
    assert [parameter["text"] for parameter in parameters] == ["Ana", "Clínica Dental Sonrisa"]
    assert outbound(database_url, conversation_id) == [
        ("human", "text", "¿Te queda bien el jueves?", "held", "service", None),
        ("system", "template", "retomar_conversacion", "sent", "utility", None),
        ("human", "text", "Tenemos lugar a las 4 pm.", "held", "service", None),
    ]

    # The patient taps "Sí, seguimos": the window opens and the held texts go out, in order.
    answer = meta_payload(
        "text",
        phone_number_id=PHONE_NUMBER_ID_A,
        user_phone=phone.removeprefix("+"),
        message_id=f"wamid.TEST.{uuid4().hex}",
        timestamp=int(time.time()),
    )
    assert api.portal is not None
    api.portal.call(_deliver_and_ingest, worker, answer)
    rows_as_clinic(  # the patient's burst is over
        database_url,
        CLINIC_A,
        "update public.messages set created_at = created_at - interval '10 seconds'"
        " where conversation_id = %s and direction = 'inbound' returning 1",
        (conversation_id,),
    )
    run_worker(api, worker, conversation_id)
    assert meta.texts() == ["¿Te queda bien el jueves?", "Tenemos lugar a las 4 pm."]


@pytest.mark.usefixtures("reopen_template")
def test_a_patient_who_never_comes_back_leaves_the_text_not_sent(
    api: TestClient, keys: TestKeys, worker: WorkerContext, meta: FakeMeta, database_url: str, phone: str
) -> None:
    conversation_id = new_conversation(database_url, phone, window_hours=-30)
    held = say(api, keys, conversation_id, "¿Seguimos con tu cita?").json()
    # 24 h later the expiry job runs.
    rows_as_clinic(
        database_url,
        CLINIC_A,
        "update public.messages set created_at = now() - interval '25 hours' where id = %s returning 1",
        (UUID(held["id"]),),
    )
    assert api.portal is not None
    api.portal.call(run_matching, worker, {"kind": "expire_hold", "message_id": held["id"]})

    state = rows_as_clinic(
        database_url,
        CLINIC_A,
        "select delivery_status, delivery_error_code from public.messages where id = %s",
        (UUID(held["id"]),),
    )
    assert state == [("failed", 131047)]


async def _deliver_and_ingest(worker: WorkerContext, payload: dict[str, Any]) -> None:
    await run_events(worker, await deliver(worker.db, payload))


def test_clinic_b_cannot_be_reached_through_a_header(api: TestClient, keys: TestKeys) -> None:
    response = api.post(
        f"/api/conversaciones/{CONVERSATION_B}/tomar",
        headers={**keys.headers(OWNER_A), "X-Clinic-Id": str(CLINIC_B)},
    )
    assert response.status_code == 403
