"""Domain rules of WhatsApp: identifiers, the 24 h window, billing categories, delivery states,
the burst of patient messages and the retry backoff."""

from datetime import UTC, datetime, timedelta

import pytest

from app.jobs.timing import BURST_MAX_WAIT, BURST_QUIET, MAX_ATTEMPTS, backoff_seconds, burst_ready
from app.whatsapp.phones import normalize_bsuid, normalize_phone, recipient_fields
from app.whatsapp.window import (
    CUSTOMER_SERVICE_WINDOW,
    SEND_MARGIN,
    billing_category,
    meta_billing_category,
    status_advances,
    window_after,
    window_is_open,
)

NOW = datetime(2026, 10, 29, 18, 0, tzinfo=UTC)


# Identifiers -----------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("5219511234567", "+529511234567"),  # Meta's wa_id with Mexico's old mobile "1"
        ("529511234567", "+529511234567"),
        ("+529511234567", "+529511234567"),
        ("+5219511234567", "+529511234567"),
        ("16505551234", "+16505551234"),  # not Mexican: untouched
        ("5511987654321", "+5511987654321"),  # Brazil keeps its digits
        ("", None),
        ("52 951 123 4567", None),
        ("+52-951", None),
        ("٥٢٩٥١١٢٣٤٥٦٧", None),  # digits of other scripts are not phone digits
        ("0529511234567", None),
        (None, None),
    ],
)
def test_phone_numbers_become_e164_with_mexico_normalized(raw: str | None, expected: str | None) -> None:
    assert normalize_phone(raw) == expected


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("US.13491208655302741918", "US.13491208655302741918"),
        ("MX.abc123", "MX.abc123"),
        ("US.ENT.11815799212886844830", None),  # a parent BSUID: Axon does not use them
        ("13491208655302741918", None),
        ("us.1349", None),
        ("", None),
        (None, None),
        ("US." + "1" * 200, None),
    ],
)
def test_only_well_formed_bsuids_are_kept(raw: str | None, expected: str | None) -> None:
    assert normalize_bsuid(raw) == expected


def test_the_phone_wins_over_the_bsuid_when_sending() -> None:
    assert recipient_fields("+529511234567", "US.1349") == {"to": "529511234567"}
    assert recipient_fields(None, "US.1349") == {"recipient": "US.1349"}
    with pytest.raises(ValueError, match="no tiene teléfono ni BSUID"):
        recipient_fields(None, None)


# 24 h window -----------------------------------------------------------------------------


def test_the_window_lasts_24_hours_from_the_patient_message() -> None:
    assert window_after(NOW) == NOW + CUSTOMER_SERVICE_WINDOW


def test_free_text_needs_the_window_open_with_a_margin() -> None:
    assert window_is_open(NOW + timedelta(hours=1), NOW)
    assert not window_is_open(None, NOW)
    assert not window_is_open(NOW - timedelta(seconds=1), NOW)
    # A message sent right at the edge could arrive after it closes.
    assert not window_is_open(NOW + SEND_MARGIN, NOW)
    assert window_is_open(NOW + SEND_MARGIN + timedelta(seconds=1), NOW)


# Billing -----------------------------------------------------------------------------------


def test_free_messages_inside_the_window_are_service() -> None:
    assert billing_category("text", None) == "service"
    assert billing_category("interactive", None) == "service"


def test_a_template_is_billed_by_its_category() -> None:
    assert billing_category("template", "utility") == "utility"
    assert billing_category("template", "marketing") == "marketing"
    with pytest.raises(ValueError, match="categoría"):
        billing_category("template", None)


@pytest.mark.parametrize(
    ("meta", "ours"),
    [
        ("service", "service"),
        ("utility", "utility"),
        ("marketing", "marketing"),
        ("marketing_lite", "marketing"),
        ("authentication", "authentication"),
        ("authentication_international", "authentication"),
        ("referral_conversion", None),
        (None, None),
    ],
)
def test_meta_pricing_categories_map_to_ours(meta: str | None, ours: str | None) -> None:
    assert meta_billing_category(meta) == ours


# Delivery states ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("current", "new", "advances"),
    [
        ("pending", "sent", True),
        (None, "sent", True),
        ("sent", "delivered", True),
        ("delivered", "read", True),
        ("read", "delivered", False),  # out of order: read already happened
        ("delivered", "sent", False),
        ("read", "read", False),  # duplicate
        ("sent", "failed", True),
        ("failed", "sent", False),
        ("held", "sent", True),
    ],
)
def test_delivery_states_never_go_back(current: str | None, new: str, advances: bool) -> None:
    assert status_advances(current, new) is advances


# Bursts and retries ------------------------------------------------------------------------


def test_a_burst_is_answered_once_the_patient_pauses() -> None:
    first = NOW
    last = NOW + timedelta(seconds=2)
    assert not burst_ready(first, last, last + timedelta(seconds=1))
    assert burst_ready(first, last, last + BURST_QUIET)


def test_a_patient_who_never_pauses_still_gets_an_answer() -> None:
    first = NOW
    last = NOW + BURST_MAX_WAIT - timedelta(seconds=1)
    assert not burst_ready(first, last, last)
    assert burst_ready(first, last, first + BURST_MAX_WAIT)


def test_the_quiet_time_is_between_3_and_5_seconds() -> None:
    assert timedelta(seconds=3) <= BURST_QUIET <= timedelta(seconds=5)


def test_retries_back_off_exponentially_up_to_a_cap() -> None:
    delays = [backoff_seconds(attempt, jitter=0.0) for attempt in range(1, MAX_ATTEMPTS + 1)]
    assert delays[:4] == [5.0, 10.0, 20.0, 40.0]
    assert delays == sorted(delays)
    assert max(delays) == 300.0
    assert backoff_seconds(3, jitter=0.999) < 20.0 * 1.2
