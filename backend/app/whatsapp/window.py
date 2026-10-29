"""The 24 h customer service window, billing categories and delivery states.

The window opens or renews when the patient writes. Inside it the clinic may send free messages;
outside it, only approved templates. Messages the clinic sends from its own app (coexistence)
neither open nor renew it.
"""

from datetime import datetime, timedelta
from typing import Literal, get_args

CUSTOMER_SERVICE_WINDOW = timedelta(hours=24)
# A free message sent right at the edge could reach Meta after the window closed.
SEND_MARGIN = timedelta(minutes=2)

BillingCategory = Literal["service", "utility", "marketing", "authentication"]
DeliveryStatus = Literal["held", "pending", "sent", "delivered", "read", "failed"]

# Statuses Meta reports that Axon records ("played" is only for voice messages the clinic sends).
META_STATUSES: frozenset[str] = frozenset({"sent", "delivered", "read", "failed"})
# A status only moves forward, whatever order Meta's webhooks arrive in. "failed" is final.
_STATUS_RANK: dict[str, int] = {
    "held": 0,
    "pending": 0,
    "sent": 1,
    "delivered": 2,
    "read": 3,
    "failed": 4,
}
# Pricing categories of Meta's status webhooks, folded into the four the plan caps count.
_META_CATEGORIES: dict[str, BillingCategory] = {
    "service": "service",
    "utility": "utility",
    "marketing": "marketing",
    "marketing_lite": "marketing",
    "authentication": "authentication",
    "authentication_international": "authentication",
}


def window_after(patient_message_at: datetime) -> datetime:
    return patient_message_at + CUSTOMER_SERVICE_WINDOW


def window_is_open(expires_at: datetime | None, now: datetime) -> bool:
    return expires_at is not None and now + SEND_MARGIN < expires_at


def billing_category(message_type: str, template_category: str | None) -> BillingCategory:
    """Category a message counts against: free messages are service; a template, its own."""
    if message_type != "template":
        return "service"
    if template_category not in get_args(BillingCategory):
        raise ValueError("una plantilla necesita su categoría de cobro")
    return template_category  # type: ignore[return-value]


def meta_billing_category(meta_category: str | None) -> BillingCategory | None:
    return _META_CATEGORIES.get(meta_category or "")


def status_advances(current: str | None, new: str) -> bool:
    return current is None or _STATUS_RANK[new] > _STATUS_RANK[current]
