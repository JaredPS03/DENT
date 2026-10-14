"""Minimal Graph API client for sending WhatsApp messages.

Phase 0 only needs it for the test-message command. Milestone 1B grows it: 24 h window,
retries with backoff, billing category and delivery statuses.

Rule 20: outside production, sending to a number that is not in
WHATSAPP_ALLOWED_TEST_NUMBERS is blocked here, in code, before any request leaves.
"""

from dataclasses import dataclass
from typing import Any

import httpx

from app.core.config import E164_PATTERN, Environment, Settings
from app.core.logs import mask_phone

GRAPH_BASE_URL = "https://graph.facebook.com"


class RecipientNotAllowedError(Exception):
    """Sending to this number is blocked outside production."""


class WhatsAppApiError(Exception):
    """Meta rejected the request. Carries status and Meta's error code, never the body sent."""

    def __init__(self, http_status: int, meta_code: int | None) -> None:
        super().__init__(f"Meta respondió {http_status} (código {meta_code})")
        self.http_status = http_status
        self.meta_code = meta_code


def ensure_recipient_allowed(settings: Settings, to: str) -> None:
    if not E164_PATTERN.fullmatch(to):
        raise ValueError("el número debe ir en formato E.164, por ejemplo +5295...")
    if settings.environment is Environment.PRODUCTION:
        return
    if to not in settings.whatsapp_allowed_test_numbers:
        raise RecipientNotAllowedError(
            f"{mask_phone(to)} no está en WHATSAPP_ALLOWED_TEST_NUMBERS; "
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
        return await self._send(
            phone_number_id,
            to,
            {"type": "template", "template": {"name": template_name, "language": {"code": language_code}}},
        )

    async def send_text(self, phone_number_id: str, to: str, body: str) -> SentMessage:
        """Free text only works inside the 24 h window opened by the patient."""
        return await self._send(phone_number_id, to, {"type": "text", "text": {"body": body}})

    async def _send(self, phone_number_id: str, to: str, message: dict[str, Any]) -> SentMessage:
        ensure_recipient_allowed(self._settings, to)
        if not phone_number_id.isdigit():
            raise ValueError("phone_number_id debe ser el id numérico que da Meta")
        url = f"{GRAPH_BASE_URL}/{self._settings.meta_graph_version}/{phone_number_id}/messages"
        response = await self._http.post(
            url,
            json={"messaging_product": "whatsapp", "recipient_type": "individual", "to": to, **message},
            headers={"Authorization": f"Bearer {self._settings.whatsapp_access_token.get_secret_value()}"},
            timeout=10.0,
        )
        if response.status_code >= 400:
            meta_code: int | None = None
            try:
                error = response.json().get("error", {})
                meta_code = error.get("code") if isinstance(error.get("code"), int) else None
            except ValueError:
                pass
            raise WhatsAppApiError(response.status_code, meta_code)
        data = response.json()
        return SentMessage(meta_message_id=str(data["messages"][0]["id"]))
