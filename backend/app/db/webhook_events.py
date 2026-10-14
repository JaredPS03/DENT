"""Routing and storage of raw WhatsApp webhook items."""

from collections.abc import Iterable
from uuid import UUID

from psycopg import AsyncConnection
from psycopg.types.json import Jsonb

from app.db.connection import Database
from app.whatsapp.payloads import WebhookItem


async def clinic_for_phone_number(db: Database, phone_number_id: str) -> UUID | None:
    """Clinic that owns Meta's phone number id, or None. Reads nothing but the id."""
    async with db.unscoped_transaction() as conn:
        cursor = await conn.execute("select private.clinic_for_phone_number(%s)", (phone_number_id,))
        row = await cursor.fetchone()
    return row[0] if row else None


async def insert_webhook_items(conn: AsyncConnection, clinic_id: UUID, items: Iterable[WebhookItem]) -> int:
    """Stores each item once. A retried webhook hits the (clinic_id, dedup_key) key and is a no-op.

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
        inserted += cursor.rowcount
    return inserted
