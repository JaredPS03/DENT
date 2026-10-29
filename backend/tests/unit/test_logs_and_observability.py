import asyncio
import gc
import json
import logging
import logging.config
import sys
from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from uvicorn.config import LOGGING_CONFIG

from app.application import create_app
from app.core.logs import (
    JsonFormatter,
    configure_logging,
    log_asyncio_exception,
    mask_message_id,
    mask_phone,
)
from app.core.observability import SCRUBBED, scrub_event
from tests.conftest import make_settings


def test_mask_phone_keeps_only_the_last_four_digits() -> None:
    assert mask_phone("+529511234567") == "***4567"
    assert mask_phone("12") == "***"
    assert mask_phone(None) == "***"


def test_mask_message_id_keeps_only_the_tail() -> None:
    # Meta's ids carry the phone number of the other party, base64-encoded, near the start.
    assert mask_message_id("wamid.HBgMNTI5NTEwMDAwMTAxFQIAERgSOUEzQ0E1") == "***OUEzQ0E1"
    assert mask_message_id("wamid.ab") == "***"
    assert mask_message_id(None) == "***"


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


def _report_like_uvicorn() -> None:
    try:
        _fail_with_a_phone()
    except ValueError as exc:
        # Exactly how uvicorn reports an unhandled exception.
        logging.getLogger("uvicorn.error").error("Exception in ASGI application", exc_info=exc)  # noqa: TRY400


def _assert_clean_json(out: str, err: str) -> None:
    output = out + err
    assert "529511234567" not in output
    assert "Traceback" not in output
    last = json.loads(out.strip().splitlines()[-1])
    assert last["logger"] == "uvicorn.error"
    assert last["exc_type"] == "ValueError"


@pytest.mark.usefixtures("restore_logging")
def test_server_config_applied_before_the_app_is_imported(capsys: pytest.CaptureFixture[str]) -> None:
    # `fastapi dev` (reload child), `--workers` and uvicorn factories: the server configures
    # logging first and imports the app afterwards.
    logging.config.dictConfig(LOGGING_CONFIG)
    configure_logging("INFO")
    _report_like_uvicorn()
    _assert_clean_json(*capsys.readouterr())


@pytest.mark.usefixtures("restore_logging")
def test_server_config_applied_after_the_app_is_imported(capsys: pytest.CaptureFixture[str]) -> None:
    # `fastapi run`: the app is imported first (create_app configures logging), then uvicorn
    # applies its own config, then the lifespan starts. The lifespan must win.
    app = create_app(make_settings())
    logging.config.dictConfig(LOGGING_CONFIG)
    with TestClient(app):
        assert logging.getLogger("uvicorn.access").disabled
        _report_like_uvicorn()
    _assert_clean_json(*capsys.readouterr())


@pytest.mark.usefixtures("restore_logging")
def test_asyncio_reports_errors_without_the_reprs_of_tasks_or_exceptions(
    capsys: pytest.CaptureFixture[str],
) -> None:
    # asyncio's default handler logs "Task exception was never retrieved" followed by the repr
    # of the task and of its exception, which can quote data.
    configure_logging("INFO")

    async def fails() -> None:
        _fail_with_a_phone()

    async def main() -> None:
        asyncio.get_running_loop().set_exception_handler(log_asyncio_exception)
        task = asyncio.ensure_future(fails())
        await asyncio.sleep(0.01)
        del task
        gc.collect()

    asyncio.run(main())

    out, err = capsys.readouterr()
    assert "529511234567" not in out + err
    line = json.loads(out.strip().splitlines()[-1])
    assert line["event"] == "asyncio.unhandled_exception"
    assert line["source"] in {"task", "future"}  # which key asyncio uses depends on the version
    assert line["exc_type"] == "ValueError"


@pytest.mark.usefixtures("restore_logging")
def test_the_app_installs_the_asyncio_handler_on_its_loop() -> None:
    async def running_loop() -> asyncio.AbstractEventLoop:
        return asyncio.get_running_loop()

    with TestClient(create_app(make_settings())) as client:
        assert client.portal is not None
        loop = client.portal.call(running_loop)
        assert loop.get_exception_handler() is log_asyncio_exception


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


def test_libraries_that_log_request_bodies_stay_quiet_even_in_debug() -> None:
    configure_logging("DEBUG")
    try:
        for name in ("botocore", "boto3", "s3transfer", "urllib3", "httpx", "httpcore", "psycopg"):
            assert not logging.getLogger(name).isEnabledFor(logging.INFO), name
    finally:
        configure_logging("INFO")
