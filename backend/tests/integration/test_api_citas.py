"""/api/citas through HTTP: sessions, clinic separation, refusals and the rate limit."""

import functools
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

import jwt
import pytest
from fastapi.testclient import TestClient

from app.api.auth import RateLimiter
from app.application import create_app
from app.scheduling.availability import Slot
from app.scheduling.connector import Actor
from tests.conftest import CLINIC_A, make_settings
from tests.integration.conftest import rows_as_clinic
from tests.support.sessions import TestKeys

CLEANING = UUID("a3000000-0000-4000-8000-000000000001")
PATIENT_A = UUID("a6000000-0000-4000-8000-000000000002")
PATIENT_B = UUID("b6000000-0000-4000-8000-000000000001")
OWNER_A = UUID("a1000000-0000-4000-8000-000000000001")
ASSISTANT_A = UUID("a1000000-0000-4000-8000-000000000002")
OWNER_B = UUID("b1000000-0000-4000-8000-000000000001")


@pytest.fixture
def keys() -> TestKeys:
    return TestKeys()


@pytest.fixture
def api(database_url: str, keys: TestKeys) -> Iterator[TestClient]:
    app = create_app(make_settings(database_url=database_url), jwks=keys)
    with TestClient(app) as client:
        yield client


def free_slots(api: TestClient, days_ahead: int) -> list[Slot]:
    start = datetime.now(UTC) + timedelta(days=days_ahead)
    search = functools.partial(
        api.app.state.agenda.find_slots,  # type: ignore[attr-defined]
        CLINIC_A,
        CLEANING,
        start,
        start + timedelta(days=7),
        actor=Actor("user", OWNER_A),
    )
    assert api.portal is not None
    return api.portal.call(search)


def new_appointment(slot: Slot, patient_id: UUID = PATIENT_A) -> dict[str, Any]:
    return {
        "patient_id": str(patient_id),
        "service_id": str(CLEANING),
        "starts_at": slot.starts_at.isoformat(),
    }


def book(api: TestClient, keys: TestKeys, slot: Slot) -> dict[str, Any]:
    response = api.post("/api/citas", json=new_appointment(slot), headers=keys.headers(OWNER_A))
    assert response.status_code == 201, response.text
    booked: dict[str, Any] = response.json()
    return booked


@pytest.mark.parametrize(
    "authorization",
    [None, "Bearer", "Bearer no-es-un-jwt", "Basic abc"],
    ids=["missing", "empty", "malformed", "other-scheme"],
)
def test_without_a_valid_session_nothing_happens(api: TestClient, authorization: str | None) -> None:
    headers = {"Authorization": authorization} if authorization else {}
    response = api.post("/api/citas", json={}, headers=headers)
    assert response.status_code == 401
    assert response.json() == {"detail": "Tu sesión no es válida; vuelve a entrar."}


@pytest.mark.parametrize(
    "overrides",
    [
        {"exp": 1_000_000},  # expired long ago
        {"aud": "anon"},
        {"iss": "https://otro-proyecto.supabase.co/auth/v1"},
        {"role": "service_role"},
        {"_kid": "otra-llave"},
        {"sub": "no-es-un-uuid"},
    ],
    ids=["expired", "audience", "issuer", "role", "unknown-key", "subject"],
)
def test_tokens_that_are_not_valid_sessions_are_refused(
    api: TestClient, keys: TestKeys, overrides: dict[str, Any]
) -> None:
    response = api.post("/api/citas", json={}, headers=keys.headers(OWNER_A, **overrides))
    assert response.status_code == 401


def test_a_shared_secret_token_is_refused(api: TestClient) -> None:
    # HS256 with Supabase's well-known local secret: anyone could mint it.
    forged = jwt.encode(
        {"sub": str(OWNER_A), "aud": "authenticated", "role": "authenticated", "exp": 9_999_999_999},
        "super-secret-jwt-token-with-at-least-32-characters-long",
        algorithm="HS256",
        headers={"kid": "test-key"},
    )
    response = api.post("/api/citas", json={}, headers={"Authorization": f"Bearer {forged}"})
    assert response.status_code == 401


def test_a_user_without_a_clinic_is_forbidden(api: TestClient, keys: TestKeys) -> None:
    response = api.post("/api/citas", json={}, headers=keys.headers(UUID(int=7)))
    assert response.status_code == 403


def test_the_owner_books_and_the_assistant_cancels(
    api: TestClient, keys: TestKeys, database_url: str
) -> None:
    slot = free_slots(api, days_ahead=120)[0]

    booked = book(api, keys, slot)
    assert booked["status"] == "pending"
    assert booked["starts_at"].startswith(slot.starts_at.strftime("%Y-%m-%dT%H:%M"))

    response = api.patch(
        f"/api/citas/{booked['id']}",
        json={"action": "cancel", "reason": "Llamó para cancelar"},
        headers=keys.headers(ASSISTANT_A),
    )
    assert response.status_code == 200
    assert response.json()["status"] == "cancelled"
    actors = rows_as_clinic(
        database_url,
        CLINIC_A,
        "select action, actor_user_id from public.audit_log where record_id = %s order by occurred_at",
        (UUID(booked["id"]),),
    )
    assert actors == [("appointment.create", OWNER_A), ("appointment.cancel", ASSISTANT_A)]


def test_the_clinic_comes_from_the_session_never_from_the_body(api: TestClient, keys: TestKeys) -> None:
    slot = free_slots(api, days_ahead=130)[0]
    body = new_appointment(slot) | {"clinic_id": "b0000000-0000-4000-8000-000000000001"}
    assert api.post("/api/citas", json=body, headers=keys.headers(OWNER_A)).status_code == 422


def test_another_clinic_s_patients_and_appointments_do_not_exist(api: TestClient, keys: TestKeys) -> None:
    slot = free_slots(api, days_ahead=140)[0]
    response = api.post("/api/citas", json=new_appointment(slot, PATIENT_B), headers=keys.headers(OWNER_A))
    assert response.status_code == 404

    booked = book(api, keys, slot)
    for change in ({"action": "cancel"}, {"action": "reschedule", "starts_at": slot.ends_at.isoformat()}):
        response = api.patch(f"/api/citas/{booked['id']}", json=change, headers=keys.headers(OWNER_B))
        assert response.status_code == 404


def test_a_taken_slot_answers_409_with_alternatives(api: TestClient, keys: TestKeys) -> None:
    slot = free_slots(api, days_ahead=150)[0]
    book(api, keys, slot)

    other_patient = UUID("a6000000-0000-4000-8000-000000000003")
    response = api.post(
        "/api/citas", json=new_appointment(slot, other_patient), headers=keys.headers(ASSISTANT_A)
    )

    assert response.status_code == 409
    body = response.json()
    assert body["detail"] == "Ese horario ya no está disponible."
    assert 1 <= len(body["alternatives"]) <= 3
    assert all(len(alternative["label"]) <= 20 for alternative in body["alternatives"])


def test_moving_and_marking_follow_the_agenda_rules(api: TestClient, keys: TestKeys) -> None:
    slots = free_slots(api, days_ahead=160)
    booked = book(api, keys, slots[0])
    later = next(slot for slot in slots if slot.starts_at >= slots[0].ends_at + timedelta(hours=1))

    moved = api.patch(
        f"/api/citas/{booked['id']}",
        json={"action": "reschedule", "starts_at": later.starts_at.isoformat()},
        headers=keys.headers(OWNER_A),
    )
    assert moved.status_code == 200
    assert moved.json()["starts_at"].startswith(later.starts_at.strftime("%Y-%m-%dT%H:%M"))

    too_soon = api.patch(
        f"/api/citas/{booked['id']}", json={"action": "attended"}, headers=keys.headers(OWNER_A)
    )
    assert too_soon.status_code == 422
    assert too_soon.json() == {"detail": "Todavía no es la hora de esa cita."}

    naive = api.patch(
        f"/api/citas/{booked['id']}",
        json={"action": "reschedule", "starts_at": "2030-01-07T10:00:00"},
        headers=keys.headers(OWNER_A),
    )
    assert naive.status_code == 422  # a time without zone is ambiguous


def test_too_many_requests_get_429(api: TestClient, keys: TestKeys) -> None:
    api.app.state.rate_limiters["user"] = RateLimiter(limit=2)  # type: ignore[attr-defined]
    headers = keys.headers(OWNER_A)
    codes = [api.post("/api/citas", json={}, headers=headers).status_code for _ in range(3)]
    assert codes == [422, 422, 429]


def test_a_body_over_the_cap_is_refused_before_anything_reads_it(api: TestClient, keys: TestKeys) -> None:
    big = b'{"patient_id": "' + b"x" * 20_000 + b'"}'
    declared = api.post("/api/citas", content=big, headers={"Content-Type": "application/json"})
    assert declared.status_code == 413

    def chunks() -> Iterator[bytes]:  # no Content-Length: counted while it arrives
        yield b'{"patient_id": "'
        for _ in range(20):
            yield b"x" * 1_000
        yield b'"}'

    chunked = api.post("/api/citas", content=chunks(), headers={"Content-Type": "application/json"})
    assert chunked.status_code == 413


def test_only_rejected_attempts_spend_the_address_budget(api: TestClient, keys: TestKeys) -> None:
    # Behind the hosting's proxy every clinic may share one address.
    api.app.state.rate_limiters["ip"] = RateLimiter(limit=2)  # type: ignore[attr-defined]
    valid = keys.headers(OWNER_A)
    assert [api.post("/api/citas", json={}, headers=valid).status_code for _ in range(4)] == [422] * 4

    forged = {"Authorization": "Bearer no-es-un-jwt"}
    assert [api.post("/api/citas", json={}, headers=forged).status_code for _ in range(3)] == [401, 401, 429]
    assert api.post("/api/citas", json={}, headers=valid).status_code == 422
