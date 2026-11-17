"""Meta's notices about a WhatsApp Business Account (milestone 1D): template status and category,
the number's quality and messaging limit, and restrictions on the account. They arrive without a
phone number id, under the account's id (entry[].id, the WABA id).

Field names and values confirmed in Meta's webhook reference (developers.facebook.com,
business-messaging/whatsapp/webhooks/reference: message_template_status_update,
template_category_update, phone_number_quality_update and account_update). The quality page lists
fewer events than Meta has sent over time, so an event this code does not know counts as bad news:
an unneeded alert costs less than a missed one.
"""

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Literal

from app.jobs.queue import OpsAlertKind

TemplateStatus = Literal["pending", "approved", "rejected", "paused", "disabled"]
TemplateCategory = Literal["utility", "marketing", "authentication"]

# Meta's event -> the status the catalog keeps. Events missing here leave the status alone.
_STATUS: dict[str, TemplateStatus] = {
    "APPROVED": "approved",
    "REINSTATED": "approved",
    "PENDING": "pending",
    "IN_APPEAL": "pending",
    "REJECTED": "rejected",
    "PAUSED": "paused",
    "DISABLED": "disabled",
    "DELETED": "disabled",
    "PENDING_DELETION": "disabled",
    "ARCHIVED": "disabled",
}
# A template that stops going out, or is about to (FLAGGED: its quality is low).
_TEMPLATE_ALERTS = frozenset(
    {"REJECTED", "PAUSED", "DISABLED", "DELETED", "PENDING_DELETION", "ARCHIVED", "FLAGGED"}
)
_CATEGORIES: dict[str, TemplateCategory] = {
    "UTILITY": "utility",
    "MARKETING": "marketing",
    "AUTHENTICATION": "authentication",
}
_GOOD_QUALITY_EVENTS = frozenset({"ONBOARDING", "THROUGHPUT_UPGRADE", "UPGRADE", "UNFLAGGED"})
_TIERS = {
    "TIER_50": 50,
    "TIER_250": 250,
    "TIER_1K": 1_000,
    "TIER_2K": 2_000,
    "TIER_10K": 10_000,
    "TIER_100K": 100_000,
    "TIER_UNLIMITED": 10**9,
}
_SERIOUS_ACCOUNT_EVENTS = frozenset(
    {"ACCOUNT_RESTRICTION", "ACCOUNT_VIOLATION", "DISABLED_UPDATE", "ACCOUNT_DELETED", "ACCOUNT_OFFBOARDED"}
)
_REASON_MAX = 64


@dataclass(frozen=True, slots=True)
class TemplateStatusNotice:
    name: str
    language: str
    meta_template_id: str | None
    event: str
    status: TemplateStatus | None
    reason: str | None
    category: TemplateCategory | None

    @property
    def alert(self) -> OpsAlertKind | None:
        return "template" if self.event in _TEMPLATE_ALERTS else None


@dataclass(frozen=True, slots=True)
class TemplateCategoryNotice:
    name: str
    language: str
    meta_template_id: str | None
    new_category: TemplateCategory | None
    # False: Meta announces a change it will make later (correct_category); True: it changed.
    done: bool

    @property
    def alert(self) -> OpsAlertKind | None:
        return "template"


@dataclass(frozen=True, slots=True)
class QualityNotice:
    display_phone: str | None
    event: str
    worse: bool

    @property
    def alert(self) -> OpsAlertKind | None:
        return "quality" if self.worse else None


@dataclass(frozen=True, slots=True)
class AccountNotice:
    event: str
    serious: bool

    @property
    def alert(self) -> OpsAlertKind | None:
        return "account" if self.serious else None


Notice = TemplateStatusNotice | TemplateCategoryNotice | QualityNotice | AccountNotice


def read_account_notice(field: str, value: dict[str, Any]) -> Notice | None:
    """The notice in a change's value, or None if it is not one Axon reads or lacks its fields."""
    reader = _READERS.get(field)
    return reader(value) if reader is not None else None


def _template_status(value: dict[str, Any]) -> TemplateStatusNotice | None:
    name, language = _template_key(value)
    event = _code(value.get("event"))
    if not name or not language or not event:
        return None
    return TemplateStatusNotice(
        name=name,
        language=language,
        meta_template_id=_template_id(value.get("message_template_id")),
        event=event,
        status=_STATUS.get(event),
        reason=_code(value.get("reason")),
        category=_CATEGORIES.get(_code(value.get("message_template_category")) or ""),
    )


def _template_category(value: dict[str, Any]) -> TemplateCategoryNotice | None:
    name, language = _template_key(value)
    if not name or not language:
        return None
    return TemplateCategoryNotice(
        name=name,
        language=language,
        meta_template_id=_template_id(value.get("message_template_id")),
        new_category=_CATEGORIES.get(_code(value.get("new_category")) or ""),
        done="previous_category" in value and "correct_category" not in value,
    )


def _quality(value: dict[str, Any]) -> QualityNotice:
    event = _code(value.get("event")) or "UNKNOWN"
    old = _TIERS.get(_code(value.get("old_limit")) or "")
    current = _TIERS.get(_code(value.get("current_limit")) or "")
    lower_limit = old is not None and current is not None and current < old
    return QualityNotice(
        display_phone=_text(value.get("display_phone_number")),
        event=event,
        worse=event not in _GOOD_QUALITY_EVENTS or lower_limit,
    )


def _account(value: dict[str, Any]) -> AccountNotice:
    event = _code(value.get("event")) or "UNKNOWN"
    ban_info = value.get("ban_info")
    ban_state = ban_info.get("waba_ban_state") if isinstance(ban_info, dict) else None
    return AccountNotice(event=event, serious=event in _SERIOUS_ACCOUNT_EVENTS and ban_state != "REINSTATE")


_READERS: dict[str, Callable[[dict[str, Any]], Notice | None]] = {
    "message_template_status_update": _template_status,
    "template_category_update": _template_category,
    "phone_number_quality_update": _quality,
    "account_update": _account,
}


def _template_key(value: dict[str, Any]) -> tuple[str | None, str | None]:
    return _text(value.get("message_template_name")), _language(value.get("message_template_language"))


def same_phone(display_phone: str | None, clinic_phone: str | None) -> bool:
    """Whether Meta's display number ("+52 951 000 0001", "529510000001") is the clinic's."""
    if not display_phone or not clinic_phone:
        return True  # nothing to tell them apart: the notice is the account's
    digits = "".join(char for char in display_phone if char.isdigit())
    return digits == "".join(char for char in clinic_phone if char.isdigit())


def _text(value: object) -> str | None:
    return value if isinstance(value, str) and 0 < len(value) <= 512 else None


def _code(value: object) -> str | None:
    """Meta's codes: uppercase letters, digits and underscores, short."""
    if not isinstance(value, str) or not 0 < len(value) <= _REASON_MAX:
        return None
    return value if all(char.isupper() or char.isdigit() or char == "_" for char in value) else None


def _language(value: object) -> str | None:
    """'es_MX' as the catalog keeps it; Meta's examples also show 'en-US'."""
    text = _text(value)
    return text.replace("-", "_") if text and len(text) <= 16 else None


def _template_id(value: object) -> str | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return str(value)
    if isinstance(value, str) and value.isdigit() and len(value) <= 32:
        return value
    return None
