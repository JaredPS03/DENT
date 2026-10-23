"""Deadlines that hold even when the database stops answering.

`asyncio.timeout` cancels the work, but then waits for its cleanup: with a silent link psycopg
spends many more seconds trying to cancel the query and close the connection. Here the caller
gets its TimeoutError on time and the cleanup finishes in the background.
"""

import asyncio
from collections.abc import Awaitable

# Cancelled work still cleaning up; kept referenced so it is not garbage-collected mid-cleanup.
_cleaning_up: set[asyncio.Task[object]] = set()


def _forget(task: asyncio.Task[object]) -> None:
    _cleaning_up.discard(task)
    if not task.cancelled():
        task.exception()  # retrieved on purpose: nobody is waiting for this result any more


def _abandon(task: asyncio.Task[object]) -> None:
    """Cancels the work and lets it clean up in the background. An error it ends with is
    retrieved there: asyncio would otherwise log it with the repr of the task and the exception."""
    task.cancel()
    _cleaning_up.add(task)
    task.add_done_callback(_forget)


async def within[T](seconds: float, work: Awaitable[T]) -> T:
    """Result of `work`, or TimeoutError after `seconds` without waiting for its cleanup."""
    task: asyncio.Task[T] = asyncio.ensure_future(work)
    try:
        done, _ = await asyncio.wait({task}, timeout=seconds)
    except asyncio.CancelledError:
        _abandon(task)
        raise
    if task in done:
        return task.result()
    _abandon(task)
    raise TimeoutError
