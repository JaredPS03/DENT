"""Structured JSON logs that carry ids only.

Never log message content, names, transcriptions or full phone numbers. Exception messages
are left out as well, because database errors quote the offending values
(for example `Key (phone)=(+52...) already exists`).
"""

import asyncio
import json
import logging
import sys
import traceback
from datetime import UTC, datetime
from typing import Any

# Attributes every LogRecord has; anything else came through `extra=` and is logged as a field.
_STANDARD_ATTRS = frozenset(
    logging.LogRecord("", 0, "", 0, "", None, None).__dict__.keys() | {"message", "asctime", "taskName"}
)


def mask_phone(phone: str | None) -> str:
    """Keeps only the last 4 digits: '+529511234567' -> '***4567'."""
    if not phone:
        return "***"
    digits = "".join(char for char in phone if char.isdigit())
    return f"***{digits[-4:]}" if len(digits) >= 4 else "***"


def mask_message_id(message_id: str | None) -> str:
    """Meta's message ids (wamid) carry the phone number of the other party, base64-encoded near
    the start: keeps only the last 8 characters, enough to find the row in webhook_events."""
    if not message_id or len(message_id) <= 8:
        return "***"
    return f"***{message_id[-8:]}"


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": datetime.fromtimestamp(record.created, UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "event": record.getMessage(),
        }
        for key, value in record.__dict__.items():
            if key not in _STANDARD_ATTRS and not key.startswith("_"):
                payload[key] = value
        if record.exc_info and record.exc_info[0] is not None:
            payload["exc_type"] = record.exc_info[0].__name__
            frames = traceback.extract_tb(record.exc_info[2])
            if frames:
                last = frames[-1]
                payload["exc_where"] = f"{last.filename}:{last.lineno} in {last.name}"
        return json.dumps(payload, default=str, ensure_ascii=False)


def log_asyncio_exception(loop: asyncio.AbstractEventLoop, context: dict[str, Any]) -> None:
    """Event loop exception handler. asyncio's default one logs the repr of the failed task,
    callback or future and of its exception, any of which can quote data; this one keeps only
    which kind of object failed, plus the exception's type and location."""
    source = next((key for key in ("task", "future", "handle") if key in context), "loop")
    logging.getLogger("asyncio").error(
        "asyncio.unhandled_exception", extra={"source": source}, exc_info=context.get("exception")
    )


# Servers configure these loggers with their own plain-text handlers before importing the app.
# "uvicorn.error" logs unhandled exceptions with their full message ("Exception in ASGI
# application"), which can quote patient data, so they must go through JsonFormatter too.
_SERVER_LOGGERS = ("uvicorn", "uvicorn.error", "fastapi")


def configure_logging(level: str = "INFO") -> None:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())
    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(level)
    for name in _SERVER_LOGGERS:
        server_logger = logging.getLogger(name)
        server_logger.handlers.clear()
        server_logger.propagate = True
    # Access logs would print full URLs and client addresses; requests are logged by the app instead.
    logging.getLogger("uvicorn.access").disabled = True
