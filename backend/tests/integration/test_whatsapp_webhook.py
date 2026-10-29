import asyncio
import time
from collections.abc import AsyncIterator, Iterator
from typing import Any
from uuid import UUID, uuid4

import httpx
import pytest
from fastapi.testclient import TestClient

from app.application import create_app
from app.webhooks import whatsapp as whatsapp_webhook
from app.whatsapp.payloads import split_payload
from tests.conftest import (
    CLINIC_A,
    CLINIC_B,
    PHONE_NUMBER_ID_A,
    PHONE_NUMBER_ID_B,
    TEST_VERIFY_TOKEN,
    make_settings,
    message_change,
    sign,
    status_change,
    to_body,
    webhook_payload,
)
from tests.integration.conftest import FreezableProxy, rows_as_clinic

URL = "/webhooks/whatsapp"
MAX_BODY_BYTES = whatsapp_webhook.MAX_BODY_BYTES


def _events(database_url: str, clinic_id: UUID, meta_id: str) -> list[tuple[Any, ...]]:
    return rows_as_clinic(
        database_url,
        clinic_id,
        "select clinic_id, field, kind, dedup_key, payload from public.webhook_events"
        " where meta_id = %s order by dedup_key",
        (meta_id,),
    )


def _post(client: TestClient, payload: dict[str, Any], signature: str | None = "valid") -> int:
    body = to_body(payload)
    headers = {"content-type": "application/json"}
    if signature == "valid":
        headers["x-hub-signature-256"] = sign(body)
    elif signature is not None:
        headers["x-hub-signature-256"] = signature
    response = client.post(URL, content=body, headers=headers)
    return int(response.status_code)


@pytest.fixture
def wamid() -> str:
    return f"wamid.TEST.{uuid4().hex}"


# Verification ------------------------------------------------------------------------------


def test_verification_returns_the_challenge(client: TestClient) -> None:
    response = client.get(
        URL,
        params={
            "hub.mode": "subscribe",
            "hub.verify_token": TEST_VERIFY_TOKEN,
            "hub.challenge": "1158201444",
        },
    )
    assert response.status_code == 200
    assert response.text == "1158201444"


@pytest.mark.parametrize(
    "params",
    [
        {"hub.mode": "subscribe", "hub.verify_token": "otro", "hub.challenge": "1"},
        {"hub.mode": "unsubscribe", "hub.verify_token": TEST_VERIFY_TOKEN, "hub.challenge": "1"},
        {"hub.mode": "subscribe", "hub.challenge": "1"},
        {"hub.mode": "subscribe", "hub.verify_token": TEST_VERIFY_TOKEN, "hub.challenge": "<script>"},
    ],
)
def test_verification_rejects_anything_else(client: TestClient, params: dict[str, str]) -> None:
    assert client.get(URL, params=params).status_code == 403


# Signature -----------------------------------------------------------------------------------


def test_valid_signature_stores_the_message_for_its_clinic(
    client: TestClient, database_url: str, wamid: str
) -> None:
    assert _post(client, webhook_payload(message_change(PHONE_NUMBER_ID_A, wamid))) == 200

    rows = _events(database_url, CLINIC_A, wamid)
    assert len(rows) == 1
    clinic_id, field, kind, dedup_key, payload = rows[0]
    assert (clinic_id, field, kind, dedup_key) == (CLINIC_A, "messages", "message", f"msg:{wamid}")
    assert payload["messages"][0]["id"] == wamid
    assert _events(database_url, CLINIC_B, wamid) == []


@pytest.mark.parametrize("signature", ["sha256=" + "0" * 64, "sha256=", "", "not-a-signature"])
def test_invalid_signature_is_rejected_and_nothing_is_stored(
    client: TestClient, database_url: str, wamid: str, signature: str
) -> None:
    assert (
        _post(client, webhook_payload(message_change(PHONE_NUMBER_ID_A, wamid)), signature=signature) == 401
    )
    assert _events(database_url, CLINIC_A, wamid) == []


def test_missing_signature_is_rejected_and_nothing_is_stored(
    client: TestClient, database_url: str, wamid: str
) -> None:
    assert _post(client, webhook_payload(message_change(PHONE_NUMBER_ID_A, wamid)), signature=None) == 401
    assert _events(database_url, CLINIC_A, wamid) == []


def test_signature_from_another_body_is_rejected(client: TestClient, database_url: str, wamid: str) -> None:
    signed = to_body(webhook_payload(message_change(PHONE_NUMBER_ID_A, "wamid.otro")))
    body = to_body(webhook_payload(message_change(PHONE_NUMBER_ID_A, wamid)))
    response = client.post(URL, content=body, headers={"x-hub-signature-256": sign(signed)})
    assert response.status_code == 401
    assert _events(database_url, CLINIC_A, wamid) == []


def test_signature_is_checked_before_parsing(client: TestClient) -> None:
    assert (
        client.post(
            URL, content=b"{not json", headers={"x-hub-signature-256": "sha256=" + "0" * 64}
        ).status_code
        == 401
    )


# Size --------------------------------------------------------------------------------------------


def test_body_over_three_megabytes_is_rejected(client: TestClient) -> None:
    body = b"x" * (MAX_BODY_BYTES + 1)
    assert client.post(URL, content=body, headers={"x-hub-signature-256": sign(body)}).status_code == 413


def test_chunked_body_over_three_megabytes_is_rejected(client: TestClient) -> None:
    def chunks() -> Iterator[bytes]:
        for _ in range(3 * 16 + 1):
            yield b"x" * (64 * 1024)

    response = client.post(URL, content=chunks(), headers={"x-hub-signature-256": "sha256=" + "0" * 64})
    assert response.status_code == 413


def test_body_of_exactly_three_megabytes_is_read(client: TestClient) -> None:
    body = b" " * MAX_BODY_BYTES
    # Within the limit: it gets to the signature check, which fails.
    assert (
        client.post(URL, content=body, headers={"x-hub-signature-256": "sha256=" + "0" * 64}).status_code
        == 401
    )


def test_while_too_many_bodies_are_being_read_meta_is_asked_to_retry(client: TestClient) -> None:
    client.app.state.webhook_reads = asyncio.Semaphore(0)  # type: ignore[attr-defined]
    body = b"{}"
    assert client.post(URL, content=body, headers={"x-hub-signature-256": sign(body)}).status_code == 503


@pytest.mark.anyio
async def test_a_body_that_arrives_too_slowly_is_cut_off(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(whatsapp_webhook, "READ_DEADLINE_SECONDS", 0.2)
    app = create_app(make_settings())
    app.state.db = None  # the body never gets far enough to need it

    async def trickle() -> AsyncIterator[bytes]:
        yield b"{"
        await asyncio.sleep(2)
        yield b"}"

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as http:
        response = await http.post(
            URL, content=trickle(), headers={"x-hub-signature-256": "sha256=" + "0" * 64}
        )
    assert response.status_code == 408


def test_a_thousand_updates_in_one_notification_are_stored_and_queued(
    client: TestClient, database_url: str
) -> None:
    tag = uuid4().hex
    changes = [message_change(PHONE_NUMBER_ID_A, f"wamid.MANY.{tag}.{index}") for index in range(1000)]
    started = time.perf_counter()
    assert _post(client, webhook_payload(*changes)) == 200
    assert time.perf_counter() - started < 3
    queued = rows_as_clinic(
        database_url,
        CLINIC_A,
        """
        select count(*) from public.webhook_events e
        join pgmq.q_whatsapp_inbound q on q.message @> jsonb_build_object('event_id', e.id::text)
        where e.meta_id like %s
        """,
        (f"wamid.MANY.{tag}.%",),
    )
    assert queued == [(1000,)]


# Routing and deduplication -----------------------------------------------------------------------


def test_unknown_phone_number_is_discarded_with_200(
    client: TestClient, database_url: str, wamid: str
) -> None:
    assert _post(client, webhook_payload(message_change("999999999999999", wamid))) == 200
    assert _events(database_url, CLINIC_A, wamid) == []
    assert _events(database_url, CLINIC_B, wamid) == []


def test_duplicate_event_is_stored_once(client: TestClient, database_url: str, wamid: str) -> None:
    payload = webhook_payload(message_change(PHONE_NUMBER_ID_A, wamid))
    assert _post(client, payload) == 200
    assert _post(client, payload) == 200
    assert len(_events(database_url, CLINIC_A, wamid)) == 1


def _jobs_for(database_url: str, clinic_id: UUID, meta_id: str) -> list[tuple[Any, ...]]:
    """Queued jobs of the items stored for `meta_id`: only their ids travel in the queue."""
    return rows_as_clinic(
        database_url,
        clinic_id,
        """
        select q.message from public.webhook_events e
        join pgmq.q_whatsapp_inbound q on q.message @> jsonb_build_object('event_id', e.id::text)
        where e.meta_id = %s
        """,
        (meta_id,),
    )


def test_each_stored_item_is_queued_once_with_ids_only(
    client: TestClient, database_url: str, wamid: str
) -> None:
    payload = webhook_payload(message_change(PHONE_NUMBER_ID_A, wamid, text="Me duele una muela"))
    assert _post(client, payload) == 200
    assert _post(client, payload) == 200  # Meta retried

    [(job,)] = _jobs_for(database_url, CLINIC_A, wamid)
    assert set(job) == {"kind", "clinic_id", "event_id"}
    assert (job["kind"], job["clinic_id"]) == ("event", str(CLINIC_A))
    assert "muela" not in str(job)


def test_items_kept_only_as_a_trace_are_not_queued(client: TestClient, database_url: str) -> None:
    field = f"future_field_{uuid4().hex[:8]}"
    payload = webhook_payload(
        {"field": field, "value": {"metadata": {"phone_number_id": PHONE_NUMBER_ID_A}, "x": uuid4().hex}}
    )
    assert _post(client, payload) == 200
    queued = rows_as_clinic(
        database_url,
        CLINIC_A,
        """
        select count(*) from public.webhook_events e
        join pgmq.q_whatsapp_inbound q on q.message @> jsonb_build_object('event_id', e.id::text)
        where e.field = %s
        """,
        (field,),
    )
    assert queued == [(0,)]


def test_each_delivery_status_is_stored_once(client: TestClient, database_url: str, wamid: str) -> None:
    assert _post(client, webhook_payload(status_change(PHONE_NUMBER_ID_A, wamid, "sent"))) == 200
    assert _post(client, webhook_payload(status_change(PHONE_NUMBER_ID_A, wamid, "delivered"))) == 200
    assert _post(client, webhook_payload(status_change(PHONE_NUMBER_ID_A, wamid, "delivered"))) == 200
    assert [row[3] for row in _events(database_url, CLINIC_A, wamid)] == [
        f"status:{wamid}:delivered",
        f"status:{wamid}:sent",
    ]


def test_one_webhook_for_two_clinics_routes_each_item_to_its_own(
    client: TestClient, database_url: str, wamid: str
) -> None:
    wamid_b = wamid + ".B"
    payload = {
        "object": "whatsapp_business_account",
        "entry": [
            {"id": "900000000000001", "changes": [message_change(PHONE_NUMBER_ID_A, wamid)]},
            {"id": "900000000000002", "changes": [message_change(PHONE_NUMBER_ID_B, wamid_b)]},
        ],
    }
    assert _post(client, payload) == 200

    assert [row[0] for row in _events(database_url, CLINIC_A, wamid)] == [CLINIC_A]
    assert [row[0] for row in _events(database_url, CLINIC_B, wamid_b)] == [CLINIC_B]
    # Neither clinic sees the other's item.
    assert _events(database_url, CLINIC_A, wamid_b) == []
    assert _events(database_url, CLINIC_B, wamid) == []


def test_account_level_changes_are_acknowledged(client: TestClient) -> None:
    payload = webhook_payload({"field": "phone_number_quality_update", "value": {"event": "FLAGGED"}})
    assert _post(client, payload) == 200


@pytest.mark.parametrize("body", [b"{not json", b"[]", b'{"object": "page", "entry": []}'])
def test_signed_but_unexpected_payload_is_answered_400(client: TestClient, body: bytes) -> None:
    assert client.post(URL, content=body, headers={"x-hub-signature-256": sign(body)}).status_code == 400


def test_a_nul_character_never_loses_the_message_nor_its_neighbours(
    client: TestClient, database_url: str, wamid: str
) -> None:
    odd = message_change(PHONE_NUMBER_ID_A, wamid, text="me sangra la encía\x00")
    neighbour = message_change(PHONE_NUMBER_ID_A, wamid + ".2", text="¿tienen cita mañana?")
    assert _post(client, webhook_payload(odd, neighbour)) == 200

    stored = _events(database_url, CLINIC_A, wamid)
    assert stored[0][4]["messages"][0]["text"]["body"] == "me sangra la encía\ufffd"
    assert len(_events(database_url, CLINIC_A, wamid + ".2")) == 1


def test_a_lone_surrogate_never_loses_the_message(client: TestClient, database_url: str, wamid: str) -> None:
    assert _post(client, webhook_payload(message_change(PHONE_NUMBER_ID_A, wamid, text="hola \ud83d"))) == 200
    assert _events(database_url, CLINIC_A, wamid)[0][4]["messages"][0]["text"]["body"] == "hola \ufffd"


def test_unknown_fields_are_recorded_without_their_content(client: TestClient, database_url: str) -> None:
    marker = uuid4().hex
    change = {
        "field": "history",
        "value": {
            "metadata": {"display_phone_number": "529510000001", "phone_number_id": PHONE_NUMBER_ID_A},
            "contacts": [{"wa_id": "529510000555", "profile": {"name": f"Tía Lupe {marker}"}}],
        },
    }
    payload = webhook_payload(change)
    # The marker makes this change, and so its dedup key, unique to this run.
    (expected,), _ = split_payload(payload)
    assert _post(client, payload) == 200

    rows = rows_as_clinic(
        database_url,
        CLINIC_A,
        "select kind, payload, payload_purged_at is not null from public.webhook_events where dedup_key = %s",
        (expected.dedup_key,),
    )
    assert rows == [("other", None, True)]


def test_items_keep_their_arrival_order(client: TestClient, database_url: str, wamid: str) -> None:
    change = message_change(PHONE_NUMBER_ID_A, f"{wamid}.1")
    for n in (2, 3):
        change["value"]["messages"].append(dict(change["value"]["messages"][0], id=f"{wamid}.{n}"))
    assert _post(client, webhook_payload(change)) == 200
    assert _post(client, webhook_payload(message_change(PHONE_NUMBER_ID_A, f"{wamid}.4"))) == 200

    rows = rows_as_clinic(
        database_url,
        CLINIC_A,
        "select meta_id from public.webhook_events where meta_id like %s order by arrival_seq",
        (f"{wamid}.%",),
    )
    assert [row[0] for row in rows] == [f"{wamid}.{n}" for n in (1, 2, 3, 4)]


def test_a_silent_database_gets_a_503_on_time_so_meta_retries(
    freezable_db: tuple[FreezableProxy, str],
    monkeypatch: pytest.MonkeyPatch,
    client: TestClient,
    database_url: str,
    wamid: str,
) -> None:
    proxy, url = freezable_db
    monkeypatch.setattr(whatsapp_webhook, "STORE_DEADLINE_SECONDS", 1.0)
    payload = webhook_payload(message_change(PHONE_NUMBER_ID_A, wamid))
    with TestClient(create_app(make_settings(database_url=url))) as silent_client:
        # Connections already open when the link goes silent: the request gets one and its
        # query hangs. Cancelling it then makes psycopg wait for the database, which is exactly
        # what the deadline must not wait for.
        assert silent_client.get("/health").status_code == 200
        proxy.frozen.set()
        started = time.monotonic()
        status_code = _post(silent_client, payload)
        elapsed = time.monotonic() - started
        proxy.close()  # the link dies for good: stuck connections fail and get cleaned up
    assert status_code == 503
    assert elapsed < 2.0

    # Meta retries later, against a database that answers, and the event is stored.
    assert _post(client, payload) == 200
    assert len(_events(database_url, CLINIC_A, wamid)) == 1
