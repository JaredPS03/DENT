"""Routing and storage of raw WhatsApp webhook items."""

from collections.abc import Iterable
from uuid import UUID

from psycopg import AsyncConnection
from psycopg.types.json import Jsonb

from app.db.connection import Database
from app.jobs.queue import QUEUE, EventJob
from app.whatsapp.payloads import WebhookItem

# Items the worker processes; the rest ('other') are only a trace.
_QUEUED_KINDS = frozenset({"message", "status", "message_echo"})


async def clinic_for_phone_number(db: Database, phone_number_id: str) -> UUID | None:
    """Clinic that owns Meta's phone number id, or None. Reads nothing but the id."""
    async with db.unscoped_transaction() as conn:
        cursor = await conn.execute("select private.clinic_for_phone_number(%s)", (phone_number_id,))
        row = await cursor.fetchone()
    return row[0] if row else None


async def insert_webhook_items(conn: AsyncConnection, clinic_id: UUID, items: Iterable[WebhookItem]) -> int:
    """Stores each item once and queues the id of each new one, in the same transaction: an item
    is never stored without its job, nor queued without being stored. A retried webhook hits the
    (clinic_id, dedup_key) key and is a no-op.

    Two round trips per clinic whatever the number of items: Meta packs up to 1000 updates in one
    notification, and storing them one by one could outlast the webhook's deadline every time.

    `conn` must come from Database.clinic_transaction(clinic_id).
    """
    rows = [
        {
            "seq": position,
            "field": item.field,
            "kind": item.kind,
            "meta_id": item.meta_id,
            "dedup_key": item.dedup_key,
            # Items without content are a trace only: the row is born already purged.
            "payload": item.payload,
        }
        for position, item in enumerate(items)
    ]
    if not rows:
        return 0
    cursor = await conn.execute(
        """
        insert into public.webhook_events
          (clinic_id, field, kind, meta_id, dedup_key, payload, payload_purged_at)
        select %s, r.field, r.kind, r.meta_id, r.dedup_key, r.payload,
               case when r.payload is null then now() end
        from jsonb_to_recordset(%s)
          as r(seq int, field text, kind text, meta_id text, dedup_key text, payload jsonb)
        order by r.seq
        on conflict (clinic_id, dedup_key) do nothing
        returning id, kind
        """,
        (clinic_id, Jsonb(rows)),
    )
    stored = await cursor.fetchall()
    jobs = [
        Jsonb(EventJob(clinic_id=clinic_id, event_id=event_id).model_dump(mode="json"))
        for event_id, kind in stored
        if kind in _QUEUED_KINDS
    ]
    if jobs:
        await conn.execute("select pgmq.send_batch(%s::text, %s::jsonb[])", (QUEUE, jobs))
    return len(stored)
