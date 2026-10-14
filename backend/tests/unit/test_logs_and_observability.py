import json
import logging
import sys

from app.core.logs import JsonFormatter, mask_phone
from app.core.observability import SCRUBBED, scrub_event


def test_mask_phone_keeps_only_the_last_four_digits() -> None:
    assert mask_phone("+529511234567") == "***4567"
    assert mask_phone("12") == "***"
    assert mask_phone(None) == "***"


def _record(**extra: object) -> logging.LogRecord:
    record = logging.LogRecord("app.test", logging.INFO, __file__, 1, "webhook.stored", None, None)
    for key, value in extra.items():
        setattr(record, key, value)
    return record


def test_json_logs_carry_the_event_and_the_extra_ids() -> None:
    line = json.loads(JsonFormatter().format(_record(clinic_id="a0000000", stored=2)))
    assert line["event"] == "webhook.stored"
    assert line["level"] == "INFO"
    assert line["clinic_id"] == "a0000000"
    assert line["stored"] == 2


def _fail_with_a_phone() -> None:
    raise ValueError("Key (phone)=(+529511234567) already exists")


def test_exception_messages_never_reach_the_logs() -> None:
    record = _record()
    try:
        _fail_with_a_phone()
    except ValueError:
        record.exc_info = sys.exc_info()
    line = JsonFormatter().format(record)
    assert "529511234567" not in line
    assert json.loads(line)["exc_type"] == "ValueError"


def test_sentry_events_lose_bodies_cookies_signatures_and_exception_text() -> None:
    event = {
        "request": {
            "url": "https://api.example.com/webhooks/whatsapp",
            "data": '{"text": {"body": "me duele la muela"}}',
            "cookies": {"sb": "token"},
            "query_string": "hub.verify_token=secreto",
            "headers": {
                "Content-Type": "application/json",
                "X-Hub-Signature-256": "sha256=abc",
                "Authorization": "Bearer x",
            },
        },
        "exception": {
            "values": [
                {
                    "type": "UniqueViolation",
                    "value": "Key (phone)=(+529511234567) already exists",
                    "stacktrace": {"frames": [{"function": "f", "vars": {"phone": "+529511234567"}}]},
                }
            ]
        },
        "user": {"ip_address": "10.0.0.1"},
    }

    scrubbed = json.dumps(scrub_event(event))

    for secret in ("me duele", "sb", "secreto", "sha256=abc", "Bearer", "529511234567", "10.0.0.1"):
        assert secret not in scrubbed, secret
    assert SCRUBBED in scrubbed
    assert "UniqueViolation" in scrubbed
