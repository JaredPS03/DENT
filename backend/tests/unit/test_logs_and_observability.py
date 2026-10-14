import json
import logging
import logging.config
import sys
from collections.abc import Iterator

import pytest
from uvicorn.config import LOGGING_CONFIG

from app.core.logs import JsonFormatter, configure_logging, mask_phone
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


@pytest.fixture
def restore_logging() -> Iterator[None]:
    names = ("", "uvicorn", "uvicorn.error", "uvicorn.access", "fastapi")
    saved = {
        name: (list(lg.handlers), lg.level, lg.propagate, lg.disabled)
        for name in names
        for lg in [logging.getLogger(name)]
    }
    yield
    for name, (handlers, level, propagate, disabled) in saved.items():
        lg = logging.getLogger(name)
        lg.handlers[:] = handlers
        lg.setLevel(level)
        lg.propagate = propagate
        lg.disabled = disabled


@pytest.mark.usefixtures("restore_logging")
def test_server_errors_go_through_the_json_formatter_without_their_message(
    capsys: pytest.CaptureFixture[str],
) -> None:
    # What uvicorn (and `fastapi run`/`fastapi dev`) does before importing the app...
    logging.config.dictConfig(LOGGING_CONFIG)
    # ...and what create_app() does when it is imported.
    configure_logging("INFO")

    try:
        _fail_with_a_phone()
    except ValueError as exc:
        # Exactly how uvicorn reports an unhandled exception.
        logging.getLogger("uvicorn.error").error("Exception in ASGI application", exc_info=exc)  # noqa: TRY400

    captured = capsys.readouterr()
    output = captured.out + captured.err
    assert "529511234567" not in output
    assert "Traceback" not in output
    line = json.loads(captured.out.strip().splitlines()[-1])
    assert line["logger"] == "uvicorn.error"
    assert line["exc_type"] == "ValueError"


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
