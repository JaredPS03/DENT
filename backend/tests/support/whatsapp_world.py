"""A small world for the worker's integration tests: a fake Graph API, an in-memory bucket, and
helpers that deliver Meta's payloads and run exactly the jobs a test created (other tests may
leave jobs of their own in the shared queue)."""

import base64
import hashlib
import json
from collections import defaultdict
from typing import Any
from uuid import UUID, uuid4

import httpx
from psycopg.types.json import Jsonb

from app.bot.transcription import FakeTranscription
from app.core.config import Settings
from app.db.connection import Database
from app.db.webhook_events import clinic_for_phone_number
from app.jobs import queue
from app.jobs.context import WorkerContext
from app.jobs.queue import QueuedJob
from app.jobs.worker import run_job
from app.webhooks.whatsapp import _route_and_store
from app.whatsapp.client import WhatsAppClient
from app.whatsapp.media import MetaMediaDownloader
from app.whatsapp.payloads import WebhookItem, split_payload

MEDIA_HOST = "https://lookaside.fbsbx.com/whatsapp_business/attachments/"
VOICE_NOTE = b"OggS\x00fake-opus-voice-note"


class FakeMeta:
    """Meta's Graph API as the worker sees it. Sent messages are recorded; `script` holds the
    next answers to POST /messages (a response or an exception), then it accepts everything."""

    def __init__(self) -> None:
        self.sent: list[dict[str, Any]] = []
        self.script: list[httpx.Response | Exception] = []
        self.media: dict[str, tuple[bytes, str]] = {}
        self.media_url_override: dict[str, str] = {}

    def handler(self, request: httpx.Request) -> httpx.Response:
        if request.method == "POST" and request.url.path.endswith("/messages"):
            if self.script:
                answer = self.script.pop(0)
                if isinstance(answer, Exception):
                    raise answer
                if answer.status_code >= 400:
                    return answer
            body = json.loads(request.content)
            self.sent.append(body)
            return httpx.Response(
                200, json={"messaging_product": "whatsapp", "messages": [{"id": f"wamid.OUT.{uuid4().hex}"}]}
            )
        if request.url.host == "graph.facebook.com" and request.method == "GET":
            media_id = request.url.path.rsplit("/", 1)[-1]
            if media_id not in self.media:
                return httpx.Response(404, json={"error": {"code": 100}})
            content, mime_type = self.media[media_id]
            return httpx.Response(
                200,
                json={
                    "messaging_product": "whatsapp",
                    "url": self.media_url_override.get(media_id, f"{MEDIA_HOST}?mid={media_id}"),
                    "mime_type": mime_type,
                    "sha256": base64.b64encode(hashlib.sha256(content).digest()).decode(),
                    "file_size": str(len(content)),
                    "id": media_id,
                },
            )
        if request.url.host == "lookaside.fbsbx.com":
            assert request.headers["authorization"].startswith("Bearer ")
            media_id = request.url.params["mid"]
            return httpx.Response(200, content=self.media[media_id][0])
        raise AssertionError(f"unexpected request to {request.url.host}")

    def texts(self) -> list[str]:
        return [message["text"]["body"] for message in self.sent if message.get("type") == "text"]


class MemoryStore:
    def __init__(self) -> None:
        self.files: dict[str, tuple[bytes, str]] = {}

    async def put(self, path: str, content: bytes, mime_type: str) -> None:
        self.files[path] = (content, mime_type)

    async def get(self, path: str) -> bytes:
        return self.files[path][0]


def make_context(
    settings: Settings,
    db: Database,
    meta: FakeMeta,
    *,
    store: MemoryStore | None = None,
    transcriber: FakeTranscription | None = None,
) -> WorkerContext:
    http = httpx.AsyncClient(transport=httpx.MockTransport(meta.handler))
    return WorkerContext(
        settings=settings,
        db=db,
        whatsapp=WhatsAppClient(settings, http),
        media_source=MetaMediaDownloader(settings, http),
        media_store=store if store is not None else MemoryStore(),
        transcriber=transcriber,
    )


async def deliver(db: Database, payload: dict[str, Any]) -> list[UUID]:
    """Stores Meta's payload exactly as the webhook does; returns the ids of its stored items."""
    items, _ = split_payload(payload)
    by_number: dict[str, list[WebhookItem]] = defaultdict(list)
    for item in items:
        by_number[item.phone_number_id].append(item)
    await _route_and_store(db, by_number)
    ids: list[UUID] = []
    for phone_number_id, number_items in by_number.items():
        clinic_id = await clinic_for_phone_number(db, phone_number_id)
        assert clinic_id is not None
        async with db.clinic_transaction(clinic_id) as conn:
            cursor = await conn.execute(
                "select id from public.webhook_events where dedup_key = any(%s) order by arrival_seq",
                ([item.dedup_key for item in number_items],),
            )
            ids.extend(row[0] for row in await cursor.fetchall())
    return ids


async def run_events(ctx: WorkerContext, event_ids: list[UUID]) -> None:
    for event_id in event_ids:
        await run_matching(ctx, {"kind": "event", "event_id": str(event_id)})


async def run_matching(
    ctx: WorkerContext, match: dict[str, Any], *, now: bool = True, passes: int = 50
) -> int:
    """Runs the queued jobs whose body contains `match`, as the worker would, until none is left
    (or for `passes` rounds). With `now`, delayed ones (a burst's wait, a busy lease) are made
    visible first."""
    ran = 0
    for _ in range(passes):
        async with ctx.db.unscoped_transaction() as conn:
            if now:
                await conn.execute(
                    "update pgmq.q_whatsapp_inbound set vt = clock_timestamp() where message @> %s",
                    (Jsonb(match),),
                )
            cursor = await conn.execute(
                "select msg_id, read_ct, message from pgmq.read(%s::text, %s::integer, 10, %s::jsonb)",
                (queue.QUEUE, queue.VISIBILITY_SECONDS, Jsonb(match)),
            )
            jobs = [QueuedJob(row[0], row[1], row[2]) for row in await cursor.fetchall()]
        if not jobs:
            return ran
        for job in jobs:
            await run_job(ctx, job)
            ran += 1
    if passes < 50:
        return ran
    raise AssertionError("jobs kept coming back")


async def queued_count(db: Database, match: dict[str, Any], queue_name: str = queue.QUEUE) -> int:
    async with db.unscoped_transaction() as conn:
        cursor = await conn.execute(
            f"select count(*) from pgmq.q_{queue_name} where message @> %s",  # noqa: S608 - fixed names
            (Jsonb(match),),
        )
        row = await cursor.fetchone()
    assert row is not None
    return int(row[0])


def random_phone() -> str:
    """A Mexican test number nobody has: +52 951 0 + six digits."""
    return "+529510" + str(uuid4().int)[-6:]
