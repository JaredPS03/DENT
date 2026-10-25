"""The body cap as raw ASGI, the way servers call it, root path included."""

import pytest
from starlette.types import Message, Receive, Scope, Send

from app.api.body_limit import MAX_BODY_BYTES, BodyLimit

pytestmark = pytest.mark.anyio


def scope_for(path: str, *, root_path: str = "", content_length: int | None = None) -> Scope:
    headers = [(b"content-type", b"application/json")]
    if content_length is not None:
        headers.append((b"content-length", str(content_length).encode()))
    return {"type": "http", "method": "POST", "path": path, "root_path": root_path, "headers": headers}


async def run(scope: Scope, body_chunks: list[bytes]) -> tuple[int | None, bool]:
    """Status sent to the client, and whether the app got to read the whole body."""
    sent: list[Message] = []
    pending = list(body_chunks)
    read_all = False

    async def receive() -> Message:
        chunk = pending.pop(0) if pending else b""
        return {"type": "http.request", "body": chunk, "more_body": bool(pending)}

    async def send(message: Message) -> None:
        sent.append(message)

    async def app(scope: Scope, receive: Receive, send: Send) -> None:
        nonlocal read_all
        while (await receive()).get("more_body"):
            pass
        read_all = True
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"ok"})

    await BodyLimit(app)(scope, receive, send)
    status = next((m["status"] for m in sent if m["type"] == "http.response.start"), None)
    return status, read_all


@pytest.mark.parametrize("root_path", ["", "/backend"])
async def test_a_declared_big_body_is_refused_even_under_a_root_path(root_path: str) -> None:
    scope = scope_for(f"{root_path}/api/citas", root_path=root_path, content_length=MAX_BODY_BYTES + 1)
    assert await run(scope, [b"x" * (MAX_BODY_BYTES + 1)]) == (413, False)


@pytest.mark.parametrize("root_path", ["", "/backend"])
async def test_a_chunked_big_body_is_cut_while_it_arrives(root_path: str) -> None:
    scope = scope_for(f"{root_path}/api/citas", root_path=root_path)
    assert await run(scope, [b"x" * 4096] * 10) == (413, False)


async def test_new_routes_outside_api_get_the_cap_too() -> None:
    scope = scope_for("/webhooks/mercadopago", content_length=MAX_BODY_BYTES + 1)
    assert await run(scope, [b"x"]) == (413, False)


@pytest.mark.parametrize("root_path", ["", "/backend"])
async def test_the_whatsapp_webhook_keeps_its_own_cap(root_path: str) -> None:
    scope = scope_for(f"{root_path}/webhooks/whatsapp", root_path=root_path, content_length=100_000)
    assert await run(scope, [b"x" * 100_000]) == (200, True)


async def test_small_bodies_pass() -> None:
    assert await run(scope_for("/api/citas", content_length=20), [b"x" * 20]) == (200, True)
