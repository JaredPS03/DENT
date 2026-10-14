"""Models of Meta's WhatsApp webhook payload and how it is split into storable items.

The models are strict on the fields Axon reads and tolerant of anything else, because Meta
adds fields over time. The stored payload is the original JSON of each item, not the model,
so nothing Meta sent is lost for reprocessing.

One webhook can carry several entries and changes, even for different clinics: each item is
routed on its own by `metadata.phone_number_id`. Account-level changes (template status,
number quality) carry no phone number id; milestone 1D decides how to route them.

Confirmed field names (Meta's official sample app fbsamples/whatsapp-business-jaspers-market and
the official Node SDK): object, entry[].id, entry[].changes[].field and .value,
value.metadata.phone_number_id, value.messages[].id, value.statuses[].id and .status.
Anything else for a known number (for example coexistence echoes, whose exact field name is
still to be confirmed against Meta's documentation in milestone 1B) is stored whole as 'other',
so nothing is lost.
"""

import hashlib
import json
from dataclasses import dataclass
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

ItemKind = Literal["message", "status", "other"]


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
    payload: dict[str, Any]


@dataclass(frozen=True)
class UnroutableChange:
    """A change without metadata.phone_number_id (account-level). Ids only, for logging."""

    entry_id: str
    field: str


_ITEM_LISTS: tuple[tuple[str, ItemKind], ...] = (
    ("messages", "message"),
    ("statuses", "status"),
)


def split_payload(raw: dict[str, Any]) -> tuple[list[WebhookItem], list[UnroutableChange]]:
    """Validates the payload and splits it into one item per message or status.

    Raises pydantic.ValidationError when the payload is not a WhatsApp webhook.
    """
    payload = WebhookPayload.model_validate(raw)
    items: list[WebhookItem] = []
    unroutable: list[UnroutableChange] = []

    for entry_index, entry in enumerate(payload.entry):
        for change_index, change in enumerate(entry.changes):
            if change.value.metadata is None:
                unroutable.append(UnroutableChange(entry_id=entry.id, field=change.field))
                continue
            phone_number_id = change.value.metadata.phone_number_id
            raw_value: dict[str, Any] = raw["entry"][entry_index]["changes"][change_index]["value"]
            common = {k: v for k, v in raw_value.items() if k not in {name for name, _ in _ITEM_LISTS}}

            found = False
            for list_name, kind in _ITEM_LISTS:
                parsed_list = getattr(change.value, list_name)
                for position, parsed in enumerate(parsed_list):
                    found = True
                    raw_item = raw_value[list_name][position]
                    if kind == "status":
                        dedup_key = f"status:{parsed.id}:{parsed.status}"
                    else:
                        dedup_key = f"msg:{parsed.id}"
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
                # A change for this number that is neither a message nor a status:
                # kept whole, deduplicated by its content.
                digest = hashlib.sha256(
                    json.dumps(raw_value, sort_keys=True, separators=(",", ":")).encode()
                ).hexdigest()
                items.append(
                    WebhookItem(
                        phone_number_id=phone_number_id,
                        field=change.field,
                        kind="other",
                        meta_id=None,
                        dedup_key=f"other:{change.field}:{digest}",
                        payload=raw_value,
                    )
                )

    return items, unroutable
