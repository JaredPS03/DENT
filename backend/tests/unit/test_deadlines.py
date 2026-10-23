import asyncio
import gc
import time
from typing import Any

import pytest

from app.core.deadlines import within

pytestmark = pytest.mark.anyio


async def _answer() -> int:
    await asyncio.sleep(0.01)
    return 42


async def _hangs_and_cleans_up_slowly(cleaned: asyncio.Event) -> None:
    try:
        await asyncio.sleep(60)
    except asyncio.CancelledError:
        # Like psycopg trying to cancel a query on a silent link.
        await asyncio.sleep(1)
        cleaned.set()
        raise


async def test_returns_the_result_in_time() -> None:
    assert await within(1, _answer()) == 42


async def test_raises_on_time_without_waiting_for_the_cleanup() -> None:
    cleaned = asyncio.Event()
    started = time.monotonic()
    with pytest.raises(TimeoutError):
        await within(0.1, _hangs_and_cleans_up_slowly(cleaned))
    assert time.monotonic() - started < 0.5
    assert not cleaned.is_set()
    # The cleanup still runs to the end in the background.
    await asyncio.wait_for(cleaned.wait(), timeout=3)


async def test_errors_of_the_work_reach_the_caller() -> None:
    async def fails() -> None:
        raise ZeroDivisionError

    with pytest.raises(ZeroDivisionError):
        await within(1, fails())


async def test_cancelling_the_caller_cancels_the_work() -> None:
    cancelled = asyncio.Event()

    async def work() -> None:
        try:
            await asyncio.sleep(60)
        except asyncio.CancelledError:
            cancelled.set()
            raise

    caller = asyncio.ensure_future(within(10, work()))
    await asyncio.sleep(0.05)
    caller.cancel()
    with pytest.raises(asyncio.CancelledError):
        await caller
    await asyncio.wait_for(cancelled.wait(), timeout=1)


async def test_an_error_after_the_caller_is_cancelled_is_not_left_unretrieved() -> None:
    # asyncio reports an exception nobody retrieved with the repr of the task and of the
    # exception, which can quote data; work abandoned this way is tracked like a timeout.
    reported: list[dict[str, Any]] = []
    loop = asyncio.get_running_loop()
    previous = loop.get_exception_handler()
    loop.set_exception_handler(lambda _loop, context: reported.append(context))
    finished = asyncio.Event()

    async def work() -> None:
        try:
            await asyncio.sleep(60)
        except asyncio.CancelledError:
            # Like psycopg when the link dies while it cancels the query.
            await asyncio.sleep(0.01)
            finished.set()
            raise ConnectionResetError from None

    try:
        caller = asyncio.ensure_future(within(10, work()))
        await asyncio.sleep(0.05)
        caller.cancel()
        with pytest.raises(asyncio.CancelledError):
            await caller
        await asyncio.wait_for(finished.wait(), timeout=1)
        await asyncio.sleep(0.01)
        gc.collect()
    finally:
        loop.set_exception_handler(previous)
    assert reported == []
