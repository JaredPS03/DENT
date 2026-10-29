"""What a stored webhook item says: an incoming message, a delivery status or a coexistence echo.

Field names confirmed in Meta's webhook reference (messages, status, smb_message_echoes) and in
the business-scoped user id guide; examples in tests/fixtures/meta. Anything Axon does not read
is ignored, so new Meta fields never break processing.
"""

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, ValidationError

from app.whatsapp.phones import normalize_bsuid, normalize_phone
from app.whatsapp.window import BillingCategory, meta_billing_category

MessageType = Literal["text", "audio", "image", "document", "button", "interactive", "template", "other"]
EchoAction = Literal["message", "revoke", "edit"]

_MEDIA_TYPES: frozenset[str] = frozenset({"audio", "image", "document"})


class UnreadableItemError(Exception):
    """The stored item is not what Meta documents; retrying will not change it."""


class _Model(BaseModel):
    model_config = ConfigDict(extra="ignore")


class _Text(_Model):
    body: str = ""


class _Media(_Model):
    id: str | None = None
    mime_type: str | None = None
    caption: str | None = None


class _Button(_Model):
    text: str | None = None
    payload: str | None = None


class _Reply(_Model):
    id: str | None = None
    title: str | None = None


class _Interactive(_Model):
    type: str | None = None
    button_reply: _Reply | None = None
    list_reply: _Reply | None = None


class _Message(_Model):
    id: str
    timestamp: str | int | None = None
    type: str = "unknown"
    from_: str | None = None
    from_user_id: str | None = None
    group_id: str | None = None
    text: _Text | None = None
    audio: _Media | None = None
    image: _Media | None = None
    document: _Media | None = None
    button: _Button | None = None
    interactive: _Interactive | None = None

    model_config = ConfigDict(extra="ignore", populate_by_name=True)

    @classmethod
    def read(cls, raw: dict[str, Any]) -> "_Message":
        return cls.model_validate({**raw, "from_": raw.get("from")})


class _Contact(_Model):
    wa_id: str | None = None
    user_id: str | None = None


class _Error(_Model):
    code: int | None = None


class _Pricing(_Model):
    billable: bool | None = None
    category: str | None = None


class _Status(_Model):
    id: str
    status: str
    timestamp: str | int | None = None
    errors: list[_Error] = []
    pricing: _Pricing | None = None
    biz_opaque_callback_data: str | None = None


class _Echo(_Model):
    id: str
    to: str | None = None
    timestamp: str | int | None = None
    type: str = "unknown"
    text: _Text | None = None
    image: _Media | None = None
    document: _Media | None = None


@dataclass(frozen=True, slots=True)
class InboundMessage:
    meta_id: str
    phone: str | None
    bsuid: str | None
    occurred_at: datetime
    type: MessageType
    content: str | None
    reply_id: str | None
    media_id: str | None
    media_mime_type: str | None
    # Messages sent in a WhatsApp group the business belongs to: not a patient's chat.
    in_group: bool


@dataclass(frozen=True, slots=True)
class StatusUpdate:
    meta_id: str
    status: str
    occurred_at: datetime
    error_code: int | None
    billable: bool | None
    category: BillingCategory | None
    # What Axon put in biz_opaque_callback_data when sending: the id of its message row.
    callback_data: str | None


@dataclass(frozen=True, slots=True)
class EchoMessage:
    meta_id: str
    phone: str | None
    occurred_at: datetime
    action: EchoAction
    type: MessageType
    content: str | None


def read_message(payload: dict[str, Any], received_at: datetime) -> InboundMessage:
    try:
        message = _Message.read(_single(payload, "messages"))
        contact = _Contact.model_validate((payload.get("contacts") or [{}])[0])
    except (ValidationError, TypeError, IndexError, AttributeError) as exc:
        raise UnreadableItemError("incoming message") from exc
    message_type, content, reply_id = _content(message)
    media = _media(message) if message_type in _MEDIA_TYPES else None
    return InboundMessage(
        meta_id=message.id,
        phone=normalize_phone(message.from_ or contact.wa_id),
        bsuid=normalize_bsuid(message.from_user_id or contact.user_id),
        occurred_at=_when(message.timestamp, received_at),
        type=message_type,
        content=content,
        reply_id=reply_id,
        media_id=media.id if media and media.id and media.id.isascii() and media.id.isdigit() else None,
        media_mime_type=media.mime_type if media else None,
        in_group=message.group_id is not None,
    )


def read_status(payload: dict[str, Any], received_at: datetime) -> StatusUpdate:
    try:
        status = _Status.model_validate(_single(payload, "statuses"))
    except (ValidationError, TypeError, IndexError, AttributeError) as exc:
        raise UnreadableItemError("status") from exc
    return StatusUpdate(
        meta_id=status.id,
        status=status.status,
        occurred_at=_when(status.timestamp, received_at),
        error_code=next((error.code for error in status.errors if error.code is not None), None),
        billable=status.pricing.billable if status.pricing else None,
        category=meta_billing_category(status.pricing.category if status.pricing else None),
        callback_data=status.biz_opaque_callback_data,
    )


def read_echo(payload: dict[str, Any], received_at: datetime) -> EchoMessage:
    try:
        echo = _Echo.model_validate(_single(payload, "message_echoes"))
    except (ValidationError, TypeError, IndexError, AttributeError) as exc:
        raise UnreadableItemError("echo") from exc
    action: EchoAction = echo.type if echo.type in ("revoke", "edit") else "message"  # type: ignore[assignment]
    message_type: MessageType = "other"
    content: str | None = None
    if echo.type == "text" and echo.text:
        message_type, content = "text", echo.text.body
    elif echo.type in ("image", "document"):
        media = echo.image if echo.type == "image" else echo.document
        message_type, content = echo.type, media.caption if media else None  # type: ignore[assignment]
    return EchoMessage(
        meta_id=echo.id,
        phone=normalize_phone(echo.to),
        occurred_at=_when(echo.timestamp, received_at),
        action=action,
        type=message_type,
        content=content or None,
    )


def _single(payload: dict[str, Any], list_name: str) -> dict[str, Any]:
    items = payload[list_name]
    if not isinstance(items, list) or len(items) != 1 or not isinstance(items[0], dict):
        raise TypeError(f"{list_name} must hold exactly one item")
    return items[0]


def _content(message: _Message) -> tuple[MessageType, str | None, str | None]:
    """Type, readable text and the id of a chosen option."""
    match message.type:
        case "text" if message.text:
            return "text", message.text.body or None, None
        case "audio":
            return "audio", None, None
        case "image" | "document":
            media = message.image if message.type == "image" else message.document
            return message.type, (media.caption or None) if media else None, None  # type: ignore[return-value]
        case "button" if message.button:
            return "button", message.button.text, _bounded(message.button.payload)
        case "interactive" if message.interactive:
            reply = message.interactive.button_reply or message.interactive.list_reply
            if reply:
                return "interactive", reply.title, _bounded(reply.id)
            return "interactive", None, None
        case _:
            # Stickers, video, location, contacts, reactions, unsupported…: the inbox shows the type.
            return "other", None, None


def _media(message: _Message) -> _Media | None:
    media: _Media | None = getattr(message, message.type)
    return media


def _bounded(reply_id: str | None) -> str | None:
    return reply_id if reply_id and len(reply_id) <= 256 else None


def _when(timestamp: str | int | None, fallback: datetime) -> datetime:
    """Meta's unix timestamp; the arrival time if it is missing or absurd."""
    try:
        moment = datetime.fromtimestamp(int(timestamp), UTC) if timestamp is not None else None
    except (ValueError, OverflowError, OSError):
        moment = None
    if moment is None or moment.year < 2020:
        return fallback
    # A clock far in the future would keep the 24 h window open for days.
    return min(moment, fallback)
