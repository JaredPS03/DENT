"""Graph API client for sending WhatsApp messages.

`send` posts one message and tells the caller exactly what happened, because a retry must never
send a message twice: Meta accepted it, Meta rejected it (retryable or not), it never left, or the
outcome is unknown (the request may have reached Meta). Retrying is the caller's decision.

Rule 20: outside production, sending to a number that is not in
WHATSAPP_ALLOWED_TEST_NUMBERS is blocked here, in code, before any request leaves.
"""

from dataclasses import dataclass
from typing import Any

import httpx

from app.core.config import Environment, Settings
from app.core.logs import mask_phone
from app.whatsapp import outbound
from app.whatsapp.phones import normalize_phone, recipient_fields

GRAPH_BASE_URL = "https://graph.facebook.com"
SEND_TIMEOUT_SECONDS = 10.0
# What Axon writes in biz_opaque_callback_data (the id of its message row) fits easily.
MAX_CALLBACK_DATA = 512

# Meta errors that say the message was turned away for now: overload, rate limits, maintenance,
# a template still syncing. Retrying them never duplicates it. Any other error is about the
# message itself and would fail again (Meta's error codes reference).
_RETRYABLE_CODES: frozenset[int] = frozenset({2, 4, 80007, 130429, 131016, 131056, 131057, 133004, 134101})
# "Unknown error" and "possible server error": Meta may have sent it anyway.
_UNKNOWN_OUTCOME_CODES: frozenset[int] = frozenset({1, 131000})
# Pair rate limit: one message every 6 seconds to the same user.
PAIR_RATE_LIMIT_CODE = 131056
PAIR_RATE_LIMIT_SECONDS = 6.0
# Meta's code for "recipient not in the allowed list", reused when Axon blocks a send outside
# production.
RECIPIENT_NOT_ALLOWED_CODE = 131030


class RecipientNotAllowedError(Exception):
    """Sending to this number is blocked outside production."""


class WhatsAppApiError(Exception):
    """Meta answered with an error. Carries status and Meta's error code, never the body sent."""

    def __init__(self, http_status: int, meta_code: int | None) -> None:
        super().__init__(f"Meta respondió {http_status} (código {meta_code})")
        self.http_status = http_status
        self.meta_code = meta_code

    @property
    def retryable(self) -> bool:
        return self.http_status == 429 or (self.meta_code is not None and self.meta_code in _RETRYABLE_CODES)

    @property
    def retry_after(self) -> float | None:
        return PAIR_RATE_LIMIT_SECONDS if self.meta_code == PAIR_RATE_LIMIT_CODE else None


class NotSentError(Exception):
    """The request never reached Meta (no connection): safe to retry."""


class OutcomeUnknownError(Exception):
    """The request may have reached Meta but no answer came back: retrying could duplicate it."""


def ensure_recipient_allowed(settings: Settings, phone: str | None) -> None:
    """Outside production only the listed test numbers receive messages. A patient known only by
    BSUID cannot be checked against the list, so it is blocked too."""
    if settings.environment is Environment.PRODUCTION:
        return
    allowed = {normalize_phone(number) for number in settings.whatsapp_allowed_test_numbers}
    if phone is None or normalize_phone(phone) not in allowed:
        raise RecipientNotAllowedError(
            f"{mask_phone(phone)} no está en WHATSAPP_ALLOWED_TEST_NUMBERS; "
            f"fuera de producción solo se envía a esos números"
        )


@dataclass(frozen=True)
class SentMessage:
    meta_message_id: str


class WhatsAppClient:
    def __init__(self, settings: Settings, http: httpx.AsyncClient) -> None:
        self._settings = settings
        self._http = http

    async def send_template(
        self, phone_number_id: str, to: str, template_name: str, language_code: str
    ) -> SentMessage:
        return await self.send(
            phone_number_id, _e164(to), None, outbound.template(template_name, language_code)
        )

    async def send_text(self, phone_number_id: str, to: str, body: str) -> SentMessage:
        """Free text only works inside the 24 h window opened by the patient."""
        return await self.send(phone_number_id, _e164(to), None, outbound.text(body))

    async def send(
        self,
        phone_number_id: str,
        phone: str | None,
        bsuid: str | None,
        message: dict[str, Any],
        *,
        callback_data: str | None = None,
    ) -> SentMessage:
        """Sends one message built by app.whatsapp.outbound. Raises WhatsAppApiError,
        NotSentError or OutcomeUnknownError; RecipientNotAllowedError before any request."""
        if not phone_number_id.isascii() or not phone_number_id.isdigit():
            raise ValueError("phone_number_id debe ser el id numérico que da Meta")
        ensure_recipient_allowed(self._settings, phone)
        body: dict[str, Any] = {
            "messaging_product": "whatsapp",
            "recipient_type": "individual",
            **recipient_fields(phone, bsuid),
            **message,
        }
        if callback_data is not None:
            if len(callback_data) > MAX_CALLBACK_DATA:
                raise ValueError("biz_opaque_callback_data pasa de 512 caracteres")
            # Comes back in the status webhooks, so a status finds its message even if the worker
            # stopped before saving Meta's id.
            body["biz_opaque_callback_data"] = callback_data
        url = f"{GRAPH_BASE_URL}/{self._settings.meta_graph_version}/{phone_number_id}/messages"
        try:
            response = await self._http.post(
                url,
                json=body,
                headers={
                    "Authorization": f"Bearer {self._settings.whatsapp_access_token.get_secret_value()}"
                },
                timeout=SEND_TIMEOUT_SECONDS,
            )
        except (httpx.ConnectError, httpx.ConnectTimeout, httpx.PoolTimeout) as exc:
            raise NotSentError(type(exc).__name__) from None
        except httpx.TransportError as exc:
            raise OutcomeUnknownError(type(exc).__name__) from None
        if response.status_code >= 400:
            code = _meta_code(response)
            if code in _UNKNOWN_OUTCOME_CODES or (
                response.status_code >= 500 and code not in _RETRYABLE_CODES
            ):
                # A server error without one of Meta's "not now" codes may still have gone out
                # (a gateway timing out after Meta took it): never sent twice.
                raise OutcomeUnknownError(f"http {response.status_code}, code {code}")
            raise WhatsAppApiError(response.status_code, code)
        try:
            return SentMessage(meta_message_id=str(response.json()["messages"][0]["id"]))
        except (ValueError, KeyError, IndexError, TypeError):
            # Accepted (2xx) but unreadable: it probably went out.
            raise OutcomeUnknownError("unreadable response") from None


def _e164(to: str) -> str:
    phone = normalize_phone(to)
    if phone is None or not to.startswith("+"):
        raise ValueError("el número debe ir en formato E.164, por ejemplo +5295...")
    return phone


def _meta_code(response: httpx.Response) -> int | None:
    try:
        error = response.json().get("error", {})
    except (ValueError, AttributeError):
        return None
    code = error.get("code") if isinstance(error, dict) else None
    return code if isinstance(code, int) else None
