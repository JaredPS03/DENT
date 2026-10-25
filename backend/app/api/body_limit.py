"""A size cap for request bodies, applied before anything reads or parses them.

FastAPI reads and decodes the whole JSON body before it resolves the session, so without this a
request with no session could still make the server hold a huge body in memory.
"""

import contextlib
import json

from starlette.types import ASGIApp, Message, Receive, Scope, Send

# Panel requests are a few hundred bytes; this leaves room and nothing more.
MAX_BODY_BYTES = 16 * 1024
_TOO_LARGE = json.dumps({"detail": "La solicitud es demasiado grande."}).encode()


class _BodyTooLargeError(Exception):
    pass


# Routes that cap their own body: Meta's webhook allows up to 1 MB and checks it while reading.
EXEMPT_PATHS = ("/webhooks/whatsapp",)


class BodyLimit:
    """Applies to every route except EXEMPT_PATHS, so routes added later get the cap too."""

    def __init__(
        self, app: ASGIApp, *, limit: int = MAX_BODY_BYTES, exempt: tuple[str, ...] = EXEMPT_PATHS
    ) -> None:
        self.app = app
        self.limit = limit
        self.exempt = exempt

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or _route_path(scope) in self.exempt:
            await self.app(scope, receive, send)
            return
        declared = dict(scope["headers"]).get(b"content-length")
        if declared is not None and (not declared.isdigit() or int(declared) > self.limit):
            await _reject(send)
            return

        received = 0
        started = rejected = False

        async def capped_receive() -> Message:
            nonlocal received, rejected
            message = await receive()
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > self.limit:
                    # A body without Content-Length went over the cap while arriving: answer
                    # now (the app turns read errors into a 400) and stop reading.
                    if not started:
                        rejected = True
                        await _reject(send)
                    raise _BodyTooLargeError
            return message

        async def guarded_send(message: Message) -> None:
            nonlocal started
            if rejected:
                return  # the 413 already went out; whatever the app answers is dropped
            if message["type"] == "http.response.start":
                started = True
            await send(message)

        with contextlib.suppress(_BodyTooLargeError):
            await self.app(scope, capped_receive, guarded_send)


def _route_path(scope: Scope) -> str:
    """The path the router matches: servers prepend the root path (`--root-path`) to it."""
    path: str = scope["path"]
    root_path: str = scope.get("root_path", "")
    if root_path and path.startswith(root_path):
        return path[len(root_path) :] or "/"
    return path


async def _reject(send: Send) -> None:
    await send(
        {
            "type": "http.response.start",
            "status": 413,
            "headers": [
                (b"content-type", b"application/json"),
                (b"content-length", str(len(_TOO_LARGE)).encode()),
            ],
        }
    )
    await send({"type": "http.response.body", "body": _TOO_LARGE})
