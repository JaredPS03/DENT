import json

import httpx
import pytest

from app.core.config import Environment
from app.whatsapp import outbound
from app.whatsapp.client import (
    NotSentError,
    OutcomeUnknownError,
    RecipientNotAllowedError,
    WhatsAppApiError,
    WhatsAppClient,
)
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
        "to": ALLOWED_TEST_NUMBER.removeprefix("+"),
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


@pytest.mark.anyio
async def test_the_message_id_travels_as_callback_data() -> None:
    seen: list[dict[str, object]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(json.loads(request.content))
        return httpx.Response(200, json={"messages": [{"id": "wamid.OUT3"}]})

    client, http = _client(httpx.MockTransport(handler))
    async with http:
        await client.send(
            "123456789012345", ALLOWED_TEST_NUMBER, None, outbound.text("hola"), callback_data="row-1"
        )
    assert seen[0]["biz_opaque_callback_data"] == "row-1"


@pytest.mark.anyio
async def test_a_patient_known_only_by_bsuid_is_addressed_by_recipient_in_production() -> None:
    seen: list[dict[str, object]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(json.loads(request.content))
        return httpx.Response(200, json={"messages": [{"id": "wamid.OUT4"}]})

    client, http = _client(httpx.MockTransport(handler), environment=Environment.PRODUCTION)
    async with http:
        await client.send("123456789012345", None, "MX.1349", outbound.text("hola"))
    assert seen[0]["recipient"] == "MX.1349"
    assert "to" not in seen[0]


@pytest.mark.anyio
async def test_outside_production_a_bsuid_cannot_be_checked_so_it_is_blocked() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError("no request should leave")

    client, http = _client(httpx.MockTransport(handler))
    async with http:
        with pytest.raises(RecipientNotAllowedError):
            await client.send("123456789012345", None, "MX.1349", outbound.text("hola"))


@pytest.mark.anyio
async def test_the_allowlist_compares_normalized_numbers() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"messages": [{"id": "wamid.OUT5"}]})

    client, http = _client(httpx.MockTransport(handler))
    async with http:
        # The same Mexican number with the old mobile "1", as Meta's wa_id may bring it.
        sent = await client.send("123456789012345", "+5219510000101", None, outbound.text("hola"))
    assert sent.meta_message_id == "wamid.OUT5"


@pytest.mark.parametrize(
    ("http_status", "code", "retryable", "retry_after"),
    [
        (503, 131016, True, None),
        (503, 133004, True, None),
        (429, 130429, True, None),
        (429, None, True, None),
        (400, 131056, True, 6.0),
        (400, 131047, False, None),  # window closed: a template is needed, not a retry
        (400, 131030, False, None),
        (400, 100, False, None),
        (401, 190, False, None),  # expired token: an alert, not a retry
    ],
)
def test_which_meta_errors_are_retried(
    http_status: int, code: int | None, retryable: bool, retry_after: float | None
) -> None:
    error = WhatsAppApiError(http_status, code)
    assert error.retryable is retryable
    assert error.retry_after == retry_after


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("failure", "expected"),
    [
        (httpx.ConnectError("refused"), NotSentError),
        (httpx.ConnectTimeout("slow"), NotSentError),
        (httpx.ReadTimeout("no answer"), OutcomeUnknownError),
        (httpx.RemoteProtocolError("cut"), OutcomeUnknownError),
    ],
)
async def test_a_lost_answer_is_unknown_and_a_refused_connection_is_not_sent(
    failure: Exception, expected: type[Exception]
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise failure

    client, http = _client(httpx.MockTransport(handler))
    async with http:
        with pytest.raises(expected):
            await client.send_text("123456789012345", ALLOWED_TEST_NUMBER, "hola")


@pytest.mark.anyio
async def test_an_accepted_but_unreadable_answer_counts_as_unknown() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="ok")

    client, http = _client(httpx.MockTransport(handler))
    async with http:
        with pytest.raises(OutcomeUnknownError):
            await client.send_text("123456789012345", ALLOWED_TEST_NUMBER, "hola")


@pytest.mark.anyio
@pytest.mark.parametrize(
    "answer",
    [
        httpx.Response(502, text="Bad Gateway"),
        httpx.Response(504),
        httpx.Response(500, json={"error": {"code": 1, "message": "An unknown error occurred"}}),
        httpx.Response(500, json={"error": {"code": 131000, "message": "Something went wrong"}}),
        httpx.Response(400, json={"error": {"code": 131000}}),
    ],
)
async def test_a_server_error_without_a_not_now_code_may_have_gone_out(answer: httpx.Response) -> None:
    client, http = _client(httpx.MockTransport(lambda request: answer))
    async with http:
        with pytest.raises(OutcomeUnknownError):
            await client.send_text("123456789012345", ALLOWED_TEST_NUMBER, "hola")
