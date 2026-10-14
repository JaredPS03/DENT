import json

import httpx
import pytest

from app.core.config import Environment
from app.whatsapp.client import RecipientNotAllowedError, WhatsAppApiError, WhatsAppClient
from tests.conftest import ALLOWED_TEST_NUMBER, make_settings


def _client(handler: httpx.MockTransport, **overrides: object) -> tuple[WhatsAppClient, httpx.AsyncClient]:
    http = httpx.AsyncClient(transport=handler)
    return WhatsAppClient(make_settings(**overrides), http), http


@pytest.mark.anyio
async def test_template_request_matches_the_cloud_api() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"messages": [{"id": "wamid.OUT1"}]})

    client, http = _client(httpx.MockTransport(handler))
    async with http:
        sent = await client.send_template("123456789012345", ALLOWED_TEST_NUMBER, "hello_world", "en_US")

    assert sent.meta_message_id == "wamid.OUT1"
    request = seen[0]
    assert str(request.url) == "https://graph.facebook.com/v24.0/123456789012345/messages"
    assert request.headers["authorization"] == "Bearer test-access-token"
    assert json.loads(request.content) == {
        "messaging_product": "whatsapp",
        "recipient_type": "individual",
        "to": ALLOWED_TEST_NUMBER,
        "type": "template",
        "template": {"name": "hello_world", "language": {"code": "en_US"}},
    }


@pytest.mark.anyio
@pytest.mark.parametrize("environment", [Environment.DEVELOPMENT, Environment.STAGING])
async def test_outside_production_unlisted_numbers_are_blocked_before_any_request(
    environment: Environment,
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError("no request should leave")

    client, http = _client(httpx.MockTransport(handler), environment=environment)
    async with http:
        with pytest.raises(RecipientNotAllowedError) as error:
            await client.send_text("123456789012345", "+529510000999", "hola")

    assert "+529510000999" not in str(error.value)
    assert "***0999" in str(error.value)


@pytest.mark.anyio
async def test_production_does_not_use_the_test_allowlist() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"messages": [{"id": "wamid.OUT2"}]})

    client, http = _client(httpx.MockTransport(handler), environment=Environment.PRODUCTION)
    async with http:
        sent = await client.send_text("123456789012345", "+529510000999", "hola")
    assert sent.meta_message_id == "wamid.OUT2"


@pytest.mark.anyio
async def test_meta_errors_keep_status_and_code_only() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            400, json={"error": {"code": 131030, "message": "Recipient +529510000101 not allowed"}}
        )

    client, http = _client(httpx.MockTransport(handler))
    async with http:
        with pytest.raises(WhatsAppApiError) as error:
            await client.send_text("123456789012345", ALLOWED_TEST_NUMBER, "hola")

    assert (error.value.http_status, error.value.meta_code) == (400, 131030)
    assert "529510000101" not in str(error.value)


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("phone_number_id", "to"), [("123456789012345", "9511234567"), ("../me", ALLOWED_TEST_NUMBER)]
)
async def test_invalid_inputs_are_rejected_before_any_request(phone_number_id: str, to: str) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError("no request should leave")

    client, http = _client(httpx.MockTransport(handler))
    async with http:
        with pytest.raises(ValueError, match=r"E\.164|numérico"):
            await client.send_text(phone_number_id, to, "hola")
