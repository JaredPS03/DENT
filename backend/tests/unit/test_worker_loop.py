"""The worker's loop: concurrency cap, a database outage while reading, and a clean stop."""

import asyncio
from typing import Any

import pytest

from app.jobs import queue, worker
from app.jobs.context import WorkerContext
from app.jobs.queue import QueuedJob
from tests.conftest import make_settings

pytestmark = pytest.mark.anyio


class Script:
    """A fake queue: hands out `total` jobs, `fail_reads` read errors first."""

    def __init__(self, total: int, fail_reads: int = 0) -> None:
        self.left = total
        self.fail_reads = fail_reads
        self.asked: list[int] = []

    async def read(self, db: Any, quantity: int) -> list[QueuedJob]:
        self.asked.append(quantity)
        if self.fail_reads:
            self.fail_reads -= 1
            raise OSError("database unreachable")
        if not self.left:
            await asyncio.sleep(0.01)
            return []
        batch = min(quantity, self.left)
        self.left -= batch
        return [QueuedJob(msg_id=self.left + index, attempt=1, body={}) for index in range(batch)]


def context(concurrency: int) -> WorkerContext:
    return WorkerContext(settings=make_settings(worker_concurrency=concurrency), db=None, whatsapp=None)  # type: ignore[arg-type]


async def test_never_more_jobs_at_once_than_the_concurrency(monkeypatch: pytest.MonkeyPatch) -> None:
    script = Script(total=12)
    running = 0
    peak = 0
    finished = 0
    stop = asyncio.Event()

    async def run(ctx: WorkerContext, job: QueuedJob) -> None:
        nonlocal running, peak, finished
        running += 1
        peak = max(peak, running)
        await asyncio.sleep(0.01)
        running -= 1
        finished += 1
        if finished == 12:
            stop.set()

    monkeypatch.setattr(queue, "read", script.read)
    monkeypatch.setattr(worker, "run_job", run)
    await asyncio.wait_for(worker.work(context(3), stop), timeout=5)

    assert finished == 12
    assert peak == 3
    assert max(script.asked) <= 3


async def test_a_database_outage_while_reading_does_not_stop_the_worker(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    script = Script(total=1, fail_reads=2)
    stop = asyncio.Event()

    async def run(ctx: WorkerContext, job: QueuedJob) -> None:
        stop.set()

    monkeypatch.setattr(queue, "read", script.read)
    monkeypatch.setattr(worker, "run_job", run)
    monkeypatch.setattr(worker, "READ_ERROR_PAUSE_SECONDS", 0.01)
    await asyncio.wait_for(worker.work(context(2), stop), timeout=5)

    assert script.left == 0


async def test_a_stop_lets_running_jobs_finish(monkeypatch: pytest.MonkeyPatch) -> None:
    script = Script(total=2)
    stop = asyncio.Event()
    finished: list[int] = []

    async def run(ctx: WorkerContext, job: QueuedJob) -> None:
        stop.set()  # the stop arrives while jobs are running
        await asyncio.sleep(0.05)
        finished.append(job.msg_id)

    monkeypatch.setattr(queue, "read", script.read)
    monkeypatch.setattr(worker, "run_job", run)
    await asyncio.wait_for(worker.work(context(2), stop), timeout=5)

    assert sorted(finished) == [0, 1]
