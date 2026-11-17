"""The scheduler inside the worker (milestone 1D; decision 008).

Every few seconds it looks at the clock and, for each task whose run is due, claims that run in
the database (private.claim): the first worker to claim it queues one job per active clinic, in
the same transaction, and any other worker or a restart finds it taken. A run missed while no
worker was up is not repeated: the next one covers it, because each task looks at what is due,
not at what happened in its own slot.

Every minute it also checks in with Sentry (Cron Monitors). If the check-ins stop, because the
worker died or the database is down, Sentry emails Jared: the WhatsApp alerts go out from this
same worker and could not tell him.
"""

import asyncio
import contextlib
import logging
from collections.abc import Callable
from datetime import datetime, timedelta
from typing import TYPE_CHECKING

from sentry_sdk.crons import capture_checkin

from app.db import claims
from app.jobs import ops_alerts
from app.jobs.context import WorkerContext
from app.jobs.queue import ScheduledJob, ScheduledTask, dead_letters_since, enqueue
from app.jobs.timing import slot_of

if TYPE_CHECKING:
    from sentry_sdk._types import MonitorConfig

logger = logging.getLogger(__name__)

TASKS: tuple[tuple[ScheduledTask, timedelta], ...] = (
    ("reminders", timedelta(minutes=15)),
    ("watchdog", timedelta(minutes=1)),
    ("cleanup", timedelta(hours=1)),
)
TICK_SECONDS = 20.0
HEARTBEAT_EVERY = timedelta(minutes=1)
MONITOR_SLUG = "axon-worker"
# Sentry alerts when no check-in arrives for this many minutes.
MONITOR_CONFIG: "MonitorConfig" = {
    "schedule": {"type": "interval", "value": 1, "unit": "minute"},
    "checkin_margin": 5,
    "max_runtime": 1,
    "timezone": "UTC",
}
DEAD_LETTERS_WINDOW = timedelta(hours=1)

Heartbeat = Callable[[bool], None]


async def tick(ctx: WorkerContext, now: datetime) -> list[ScheduledTask]:
    """Claims and queues every run due at `now`. Returns the tasks this call started."""
    started: list[ScheduledTask] = []
    for task, every in TASKS:
        slot = slot_of(now, every)
        dead_letters = 0
        async with ctx.db.unscoped_transaction() as conn:
            if not await claims.claim(conn, f"task:{task}", slot):
                continue
            for clinic_id in await claims.active_clinic_ids(conn):
                await enqueue(conn, ScheduledJob(clinic_id=clinic_id, task=task, slot=slot))
            if task == "watchdog":
                dead_letters = await dead_letters_since(conn, now - DEAD_LETTERS_WINDOW)
        started.append(task)
        if dead_letters:
            await ops_alerts.raise_alert(ctx.db, "dead_letters", None, dead_letters, now)
    return started


def sentry_heartbeat(healthy: bool) -> None:
    """A check-in of the monitor; nothing happens when Sentry is not configured."""
    capture_checkin(
        monitor_slug=MONITOR_SLUG,
        status="ok" if healthy else "error",
        monitor_config=MONITOR_CONFIG,
    )


async def run_scheduler(
    ctx: WorkerContext, stop: asyncio.Event, heartbeat: Heartbeat = sentry_heartbeat
) -> None:
    last_beat: datetime | None = None
    while not stop.is_set():
        now = ctx.clock()
        healthy = True
        try:
            await tick(ctx, now)
        except Exception:
            logger.exception("scheduler.tick_failed")
            healthy = False
        beat = slot_of(now, HEARTBEAT_EVERY)
        if beat != last_beat:
            try:
                heartbeat(healthy)
            except Exception:
                logger.exception("scheduler.heartbeat_failed")
            last_beat = beat
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(stop.wait(), TICK_SECONDS)
