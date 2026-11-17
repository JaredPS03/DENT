"""Models of Meta's WhatsApp webhook payload and how it is split into storable items.

The models are strict on the fields Axon reads and tolerant of anything else, because Meta
adds fields over time. For messages and statuses the stored payload is the original JSON of
each item, not the model, so it can be reprocessed. Text Postgres cannot store (NUL characters,
lone surrogates) is replaced first: one odd character must never lose a patient's message.

One webhook can carry several entries and changes, even for different clinics: each item is
routed on its own by `metadata.phone_number_id`. Account-level changes (template status and
category, number quality, account restrictions) carry no phone number id: they are routed by the
account's id (entry[].id, the WABA id) and keep their content, which holds no patient data
(milestone 1D, app.whatsapp.account).

Confirmed field names (Meta's official sample app fbsamples/whatsapp-business-jaspers-market and
the official Node SDK): object, entry[].id, entry[].changes[].field and .value,
value.metadata.phone_number_id, value.messages[].id, value.statuses[].id and .status.
Only two fields keep their content: `messages` (messages and delivery statuses) and
`smb_message_echoes` (what the clinic sends from its WhatsApp Business app, under coexistence;
field and `message_echoes` list confirmed in Meta's smb_message_echoes reference). Any other change
for a known number (history or contact sync, fields Meta adds later) is recorded as 'other'
WITHOUT its content: the spec says to store only what it asks for, and a contact sync would bring
people who are not patients. A field joins the list once it is confirmed in Meta's documentation
and something processes it.
"""

import hashlib
import json
from dataclasses import dataclass
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

ItemKind = Literal["message", "status", "message_echo", "account", "other"]

# Account-level fields whose content is kept and processed (app.whatsapp.account).
ACCOUNT_FIELDS = frozenset(
    {
        "message_template_status_update",
        "template_category_update",
        "phone_number_quality_update",
        "account_update",
    }
)


class _MetaModel(BaseModel):
    model_config = ConfigDict(extra="allow", strict=True)


class Metadata(_MetaModel):
    phone_number_id: str = Field(min_length=1, max_length=64)
    display_phone_number: str | None = None


class MessageItem(_MetaModel):
    id: str = Field(min_length=1, max_length=256)


class StatusItem(_MetaModel):
    id: str = Field(min_length=1, max_length=256)
    status: str = Field(min_length=1, max_length=32)


class ChangeValue(_MetaModel):
    metadata: Metadata | None = None
    messages: list[MessageItem] = []
    statuses: list[StatusItem] = []
    message_echoes: list[MessageItem] = []


class Change(_MetaModel):
    field: str = Field(min_length=1, max_length=64)
    value: ChangeValue


class Entry(_MetaModel):
    id: str = Field(min_length=1, max_length=64)
    changes: list[Change] = []


class WebhookPayload(_MetaModel):
    object: Literal["whatsapp_business_account"]
    entry: list[Entry]


@dataclass(frozen=True)
class WebhookItem:
    phone_number_id: str
    field: str
    kind: ItemKind
    meta_id: str | None
    dedup_key: str
    # None: recorded as a trace only, content never stored.
    payload: dict[str, Any] | None


@dataclass(frozen=True)
class AccountChange:
    """A change without metadata.phone_number_id: about the WhatsApp Business Account `entry_id`.
    `payload` is its value for the fields Axon reads (ACCOUNT_FIELDS) and None for the rest."""

    entry_id: str
    field: str
    dedup_key: str = ""
    payload: dict[str, Any] | None = None


# The webhook fields whose content is stored, and the lists each one carries.
_ITEM_LISTS: dict[str, tuple[tuple[str, ItemKind], ...]] = {
    "messages": (("messages", "message"), ("statuses", "status")),
    "smb_message_echoes": (("message_echoes", "message_echo"),),
}
_DEDUP_PREFIX: dict[ItemKind, str] = {"message": "msg", "message_echo": "echo"}

REPLACEMENT_CHARACTER = "\ufffd"


def _clean_text(text: str) -> str:
    if "\x00" in text:
        text = text.replace("\x00", REPLACEMENT_CHARACTER)
    try:
        text.encode("utf-8")
    except UnicodeEncodeError:
        # Lone surrogates ("\ud83d" without its pair) are valid JSON but not valid UTF-8.
        text = "".join(REPLACEMENT_CHARACTER if 0xD800 <= ord(char) <= 0xDFFF else char for char in text)
    return text


def clean_for_postgres(value: Any) -> Any:
    """Replaces, in every key and string, what Postgres text and jsonb reject."""
    if isinstance(value, str):
        return _clean_text(value)
    if isinstance(value, dict):
        return {_clean_text(str(k)): clean_for_postgres(v) for k, v in value.items()}
    if isinstance(value, list):
        return [clean_for_postgres(v) for v in value]
    return value


def split_payload(raw: dict[str, Any]) -> tuple[list[WebhookItem], list[AccountChange]]:
    """Validates the payload and splits it into one item per message or status.

    Raises pydantic.ValidationError when the payload is not a WhatsApp webhook.
    """
    raw = clean_for_postgres(raw)
    payload = WebhookPayload.model_validate(raw)
    items: list[WebhookItem] = []
    account: list[AccountChange] = []

    for entry_index, entry in enumerate(payload.entry):
        for change_index, change in enumerate(entry.changes):
            raw_value: dict[str, Any] = raw["entry"][entry_index]["changes"][change_index]["value"]
            if change.value.metadata is None:
                if change.field in ACCOUNT_FIELDS:
                    account.append(
                        AccountChange(
                            entry_id=entry.id,
                            field=change.field,
                            dedup_key=f"account:{change.field}:{_digest(raw_value)}",
                            payload=raw_value,
                        )
                    )
                else:
                    account.append(AccountChange(entry_id=entry.id, field=change.field))
                continue
            phone_number_id = change.value.metadata.phone_number_id
            lists = _ITEM_LISTS.get(change.field, ())
            common = {k: v for k, v in raw_value.items() if k not in {name for name, _ in lists}}

            found = False
            for list_name, kind in lists:
                parsed_list = getattr(change.value, list_name)
                for position, parsed in enumerate(parsed_list):
                    found = True
                    raw_item = raw_value[list_name][position]
                    if kind == "status":
                        dedup_key = f"status:{parsed.id}:{parsed.status}"
                    else:
                        dedup_key = f"{_DEDUP_PREFIX[kind]}:{parsed.id}"
                    items.append(
                        WebhookItem(
                            phone_number_id=phone_number_id,
                            field=change.field,
                            kind=kind,
                            meta_id=parsed.id,
                            dedup_key=dedup_key,
                            payload={**common, list_name: [raw_item]},
                        )
                    )

            if not found:
                # Not a message nor a status: only a trace (field and a digest for deduplication).
                digest = _digest(raw_value)
                items.append(
                    WebhookItem(
                        phone_number_id=phone_number_id,
                        field=change.field,
                        kind="other",
                        meta_id=None,
                        dedup_key=f"other:{change.field}:{digest}",
                        payload=None,
                    )
                )

    return items, account


def _digest(value: dict[str, Any]) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
