from collections.abc import Iterator
from typing import Any
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient

from tests.conftest import (
    CLINIC_A,
    CLINIC_B,
    PHONE_NUMBER_ID_A,
    PHONE_NUMBER_ID_B,
    TEST_VERIFY_TOKEN,
    message_change,
    sign,
    status_change,
    to_body,
    webhook_payload,
)
from tests.integration.conftest import rows_as_clinic

URL = "/webhooks/whatsapp"


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


def test_body_over_one_megabyte_is_rejected(client: TestClient) -> None:
    body = b"x" * (1024 * 1024 + 1)
    assert client.post(URL, content=body, headers={"x-hub-signature-256": sign(body)}).status_code == 413


def test_chunked_body_over_one_megabyte_is_rejected(client: TestClient) -> None:
    def chunks() -> Iterator[bytes]:
        for _ in range(17):
            yield b"x" * (64 * 1024)

    response = client.post(URL, content=chunks(), headers={"x-hub-signature-256": "sha256=" + "0" * 64})
    assert response.status_code == 413


def test_body_of_exactly_one_megabyte_is_read(client: TestClient) -> None:
    body = b" " * (1024 * 1024)
    # Within the limit: it gets to the signature check, which fails.
    assert (
        client.post(URL, content=body, headers={"x-hub-signature-256": "sha256=" + "0" * 64}).status_code
        == 401
    )


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
