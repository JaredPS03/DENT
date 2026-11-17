"""Claims of the scheduler and of the alerts to Jared, and the clinics scheduled runs visit.

These are the only reads and writes without a clinic fixed in the transaction, through
security definer functions that take and return ids only (base-de-datos.md).
"""

from datetime import datetime
from uuid import UUID

from psycopg import AsyncConnection


async def claim(conn: AsyncConnection, key: str, slot: datetime) -> bool:
    """True for the first caller of (key, slot), across workers and restarts; false for the rest."""
    cursor = await conn.execute("select private.claim(%s, %s)", (key, slot))
    row = await cursor.fetchone()
    return bool(row and row[0])


async def active_clinic_ids(conn: AsyncConnection) -> list[UUID]:
    cursor = await conn.execute("select id from private.active_clinic_ids() as id order by id")
    return [row[0] for row in await cursor.fetchall()]


async def clinics_for_waba(conn: AsyncConnection, waba_id: str) -> list[UUID]:
    """The clinics of a WhatsApp Business Account, for Meta's notices about the account."""
    cursor = await conn.execute("select id from private.clinics_for_waba(%s) as id order by id", (waba_id,))
    return [row[0] for row in await cursor.fetchall()]
