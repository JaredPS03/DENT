"""Evaluations of the bot against the real Claude API (milestone 1C). They cost money: run them on
purpose, with the local database up, after changing the prompt, the tools or the orchestrator:

    uv run pytest tests/evals -m evals

Each case is a conversation in cases/*.yaml. The summary by category is printed at the end."""

from collections.abc import AsyncIterator

import pytest
from psycopg_pool import AsyncConnectionPool

from app.db.connection import Database
from tests.evals.report import RESULTS
from tests.evals.runner import Case, load_cases, run_case

pytestmark = [pytest.mark.anyio, pytest.mark.evals]
CASES = load_cases()


@pytest.fixture
async def db(database_url: str) -> AsyncIterator[Database]:
    pool = AsyncConnectionPool(
        database_url,
        min_size=1,
        max_size=10,
        open=False,
        kwargs={"autocommit": True, "prepare_threshold": None},
    )
    await pool.open(wait=True)
    try:
        yield Database(pool)
    finally:
        await pool.close()


def test_there_are_at_least_50_cases() -> None:
    assert len(CASES) >= 50


@pytest.mark.parametrize("case", CASES, ids=[case.id for case in CASES])
async def test_case(case: Case, db: Database) -> None:
    result = await run_case(case, db)
    RESULTS.append(result)
    assert result.passed, "\n".join(result.failures)
