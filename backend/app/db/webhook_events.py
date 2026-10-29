"""Routing and storage of raw WhatsApp webhook items."""

from collections.abc import Iterable
from uuid import UUID

from psycopg import AsyncConnection
from psycopg.types.json import Jsonb

from app.db.connection import Database
from app.jobs.queue import EventJob, enqueue
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

    `conn` must come from Database.clinic_transaction(clinic_id).
    """
    inserted = 0
    for item in items:
        # Items without content are a trace only: the row is born already purged.
        has_content = item.payload is not None
        cursor = await conn.execute(
            """
            insert into public.webhook_events
              (clinic_id, field, kind, meta_id, dedup_key, payload, payload_purged_at)
            values (%s, %s, %s, %s, %s, %s, case when %s then null else now() end)
            on conflict (clinic_id, dedup_key) do nothing
            returning id
            """,
            (
                clinic_id,
                item.field,
                item.kind,
                item.meta_id,
                item.dedup_key,
                Jsonb(item.payload) if has_content else None,
                has_content,
            ),
        )
        row = await cursor.fetchone()
        if row is None:
            continue
        inserted += 1
        if item.kind in _QUEUED_KINDS:
            await enqueue(conn, EventJob(clinic_id=clinic_id, event_id=row[0]))
    return inserted
