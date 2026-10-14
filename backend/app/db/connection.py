"""Connections as the axon_app role and clinic-scoped transactions.

Every unit of work runs in a transaction whose first statement fixes the clinic:
`select set_config('app.clinic_id', <id>, true)`, the parameterized form of
`SET LOCAL app.clinic_id`. RLS then shows only that clinic's rows, and the setting disappears
when the transaction ends, so it never leaks to the next user of a pooled connection.

Never keep one of these transactions open while waiting on Meta, the AI or transcription.
"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from uuid import UUID

from psycopg import AsyncConnection
from psycopg_pool import AsyncConnectionPool

from app.core.config import Settings


def create_pool(settings: Settings) -> AsyncConnectionPool:
    return AsyncConnectionPool(
        conninfo=settings.database_url.get_secret_value(),
        min_size=1,
        max_size=settings.db_pool_max_size,
        open=False,
        timeout=5.0,
        check=AsyncConnectionPool.check_connection,
        kwargs={
            # Explicit transactions only: nothing stays open by accident.
            "autocommit": True,
            # The Supabase pooler in transaction mode does not support prepared statements.
            "prepare_threshold": None,
            "application_name": "axon-backend",
        },
    )


class Database:
    def __init__(self, pool: AsyncConnectionPool) -> None:
        self.pool = pool

    @asynccontextmanager
    async def clinic_transaction(self, clinic_id: UUID) -> AsyncIterator[AsyncConnection]:
        """Transaction where RLS shows only `clinic_id`. The clinic comes from the session or
        the webhook routing, never from a request body or from what the AI says."""
        async with self.pool.connection() as conn, conn.transaction():
            await conn.execute("select set_config('app.clinic_id', %s, true)", (str(clinic_id),))
            yield conn

    @asynccontextmanager
    async def unscoped_transaction(self) -> AsyncIterator[AsyncConnection]:
        """Transaction without a clinic: RLS shows no business rows at all. Only for the
        id-returning private functions (webhook routing, scheduled jobs) and health checks."""
        async with self.pool.connection() as conn, conn.transaction():
            yield conn
