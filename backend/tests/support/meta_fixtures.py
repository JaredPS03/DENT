"""Meta's official example payloads (tests/fixtures/meta), routed to the seeded clinics.

Only identifiers change: the business phone number id, the user's phone number (or BSUID), the
message id and the time. Everything else stays exactly as Meta documents it.
"""

import copy
import json
from pathlib import Path
from typing import Any

FIXTURES = Path(__file__).parent.parent / "fixtures" / "meta"

# Identifiers used in Meta's examples.
EXAMPLE_USER_PHONE = "16505551234"
EXAMPLE_BSUID = "US.13491208655302741918"


def meta_payload(  # noqa: PLR0913 - one override per identifier
    name: str,
    *,
    phone_number_id: str,
    user_phone: str | None = None,
    user_bsuid: str | None = None,
    message_id: str | None = None,
    timestamp: int | None = None,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """The example in `<name>.json` for one business number. `extra` merges into the first item
    (message, status or echo) for fields the example leaves out, such as biz_opaque_callback_data."""
    payload: dict[str, Any] = json.loads((FIXTURES / f"{name}.json").read_text())
    replacements: dict[str, str] = {}
    if user_phone is not None:
        replacements[EXAMPLE_USER_PHONE] = user_phone
    if user_bsuid is not None:
        replacements[EXAMPLE_BSUID] = user_bsuid
    payload = _replace_values(payload, replacements)
    for entry in payload["entry"]:
        for change in entry["changes"]:
            value = change["value"]
            value["metadata"]["phone_number_id"] = phone_number_id
            for list_name in ("messages", "statuses", "message_echoes"):
                for item in value.get(list_name, []):
                    if message_id is not None:
                        item["id"] = message_id
                    if timestamp is not None:
                        item["timestamp"] = str(timestamp) if isinstance(item["timestamp"], str) else timestamp
                    if extra:
                        item.update(copy.deepcopy(extra))
    return payload


def first_item(payload: dict[str, Any], list_name: str) -> dict[str, Any]:
    item: dict[str, Any] = payload["entry"][0]["changes"][0]["value"][list_name][0]
    return item


def _replace_values(value: Any, replacements: dict[str, str]) -> Any:
    if isinstance(value, str):
        return replacements.get(value, value)
    if isinstance(value, dict):
        return {key: _replace_values(item, replacements) for key, item in value.items()}
    if isinstance(value, list):
        return [_replace_values(item, replacements) for item in value]
    return value
