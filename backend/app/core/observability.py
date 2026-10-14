"""Sentry, configured so no patient data leaves the server.

Only active when SENTRY_DSN is set. Request bodies, cookies, query strings, signatures,
local variables and exception messages are stripped before anything is sent.
"""

from typing import Any

import sentry_sdk

from app.core.config import Settings

_SAFE_HEADERS = frozenset({"content-type", "content-length", "user-agent", "host"})
SCRUBBED = "[scrubbed]"


def scrub_event(event: dict[str, Any], hint: dict[str, Any] | None = None) -> dict[str, Any]:
    request = event.get("request")
    if isinstance(request, dict):
        for key in ("data", "cookies", "query_string", "env"):
            request.pop(key, None)
        headers = request.get("headers")
        if isinstance(headers, dict):
            request["headers"] = {k: v for k, v in headers.items() if k.lower() in _SAFE_HEADERS}

    exception = event.get("exception")
    if isinstance(exception, dict):
        for value in exception.get("values") or []:
            if isinstance(value, dict):
                # Database and HTTP errors quote values (phones, names); keep only the type.
                value["value"] = SCRUBBED
                for frame in (value.get("stacktrace") or {}).get("frames") or []:
                    if isinstance(frame, dict):
                        frame.pop("vars", None)

    if "logentry" in event and isinstance(event["logentry"], dict):
        event["logentry"].pop("params", None)
    event.pop("user", None)
    return event


def init_sentry(settings: Settings) -> None:
    if settings.sentry_dsn is None:
        return
    sentry_sdk.init(
        dsn=settings.sentry_dsn.get_secret_value(),
        environment=settings.environment.value,
        send_default_pii=False,
        include_local_variables=False,
        max_request_body_size="never",
        traces_sample_rate=0.0,
        before_send=scrub_event,  # type: ignore[arg-type]
    )
