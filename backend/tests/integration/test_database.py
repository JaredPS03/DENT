"""The data layer: axon_app sees only the clinic fixed in its transaction, and the setting
never survives the transaction, even on a reused pooled connection."""

from collections.abc import AsyncIterator
from contextlib import AbstractAsyncContextManager

import pytest
from psycopg import AsyncConnection
from psycopg_pool import AsyncConnectionPool

from app.db.connection import Database
from app.db.webhook_events import clinic_for_phone_number
from tests.conftest import CLINIC_A, CLINIC_B, PHONE_NUMBER_ID_A, PHONE_NUMBER_ID_B

pytestmark = pytest.mark.anyio


@pytest.fixture
async def db(database_url: str) -> AsyncIterator[Database]:
    # One connection, so every transaction below reuses the same physical connection.
    pool = AsyncConnectionPool(
        database_url,
        min_size=1,
        max_size=1,
        open=False,
        kwargs={"autocommit": True, "prepare_threshold": None},
    )
    await pool.open(wait=True)
    try:
        yield Database(pool)
    finally:
        await pool.close()


async def _patient_clinics(scope: AbstractAsyncContextManager[AsyncConnection]) -> set[object]:
    async with scope as conn:
        cursor = await conn.execute("select distinct clinic_id from public.patients")
        return {row[0] for row in await cursor.fetchall()}


async def test_clinic_transaction_shows_only_that_clinic(db: Database) -> None:
    assert await _patient_clinics(db.clinic_transaction(CLINIC_A)) == {CLINIC_A}
    assert await _patient_clinics(db.clinic_transaction(CLINIC_B)) == {CLINIC_B}


async def test_without_a_clinic_nothing_is_visible(db: Database) -> None:
    assert await _patient_clinics(db.unscoped_transaction()) == set()


async def test_the_clinic_never_leaks_to_the_next_transaction(db: Database) -> None:
    async with db.clinic_transaction(CLINIC_A) as conn:
        cursor = await conn.execute("select private.current_clinic_id()")
        assert (await cursor.fetchone()) == (CLINIC_A,)

    async with db.unscoped_transaction() as conn:
        cursor = await conn.execute("select private.current_clinic_id(), count(*) from public.patients")
        assert (await cursor.fetchone()) == (None, 0)


async def test_the_clinic_is_cleared_even_when_the_transaction_fails(db: Database) -> None:
    with pytest.raises(ZeroDivisionError):
        async with db.clinic_transaction(CLINIC_A):
            raise ZeroDivisionError

    async with db.unscoped_transaction() as conn:
        cursor = await conn.execute("select private.current_clinic_id()")
        assert (await cursor.fetchone()) == (None,)


async def test_routing_by_phone_number_id(db: Database) -> None:
    assert await clinic_for_phone_number(db, PHONE_NUMBER_ID_A) == CLINIC_A
    assert await clinic_for_phone_number(db, PHONE_NUMBER_ID_B) == CLINIC_B
    assert await clinic_for_phone_number(db, "999999999999999") is None
