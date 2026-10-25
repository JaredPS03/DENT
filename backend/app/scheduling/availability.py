"""Free slots, computed in the clinic's own time zone.

Pure functions, no database: the agenda connector loads the weekly hours, exceptions and active
appointments and asks here which starts are free. The bot may only offer slots that came out of
this module (`buscar_disponibilidad`).
"""

import hashlib
from base64 import b32encode
from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from uuid import UUID
from zoneinfo import ZoneInfo

# Starts are offered on a 15-minute grid: 9:00, 9:15, 9:30...
STEP = timedelta(minutes=15)
# The bot never offers a slot that starts in less than an hour; the panel may book any future slot.
BOT_LEAD_TIME = timedelta(hours=1)
# WhatsApp button titles take at most 20 characters.
MAX_LABEL_LENGTH = 20

_WEEKDAYS = ("Lun", "Mar", "Mié", "Jue", "Vie", "Sáb", "Dom")
_MONTHS = ("ene", "feb", "mar", "abr", "may", "jun", "jul", "ago", "sep", "oct", "nov", "dic")


@dataclass(frozen=True, slots=True)
class WeeklyHours:
    """One row of `schedules`: a provider's hours on an ISO weekday (1 Monday ... 7 Sunday)."""

    provider_id: UUID
    weekday: int
    start: time
    end: time
    break_start: time | None
    break_end: time | None
    buffer_minutes: int


@dataclass(frozen=True, slots=True)
class Interval:
    """An active appointment or an exception. `provider_id` None means the whole clinic."""

    starts_at: datetime
    ends_at: datetime
    provider_id: UUID | None = None

    def blocks(self, provider_id: UUID) -> bool:
        return self.provider_id is None or self.provider_id == provider_id


@dataclass(frozen=True, slots=True)
class Slot:
    provider_id: UUID
    starts_at: datetime  # UTC
    ends_at: datetime  # UTC
    id: str  # short and stable for (provider, start); the bot refers to slots by it
    label: str  # ready for a button: "Jue 4:00 pm"


def find_slots(  # noqa: PLR0913 - keyword-only, each one a different input
    *,
    hours: Sequence[WeeklyHours],
    appointments: Sequence[Interval],
    exceptions: Sequence[Interval],
    duration: timedelta,
    tz: ZoneInfo,
    now: datetime,
    window_start: datetime,
    window_end: datetime,
    lead_time: timedelta = timedelta(0),
) -> list[Slot]:
    """Free starts in [window_start, window_end), in order, for every provider with hours.

    A slot fits inside the day's hours without crossing the break, keeps the day's buffer from
    every active appointment of its provider on both sides, avoids the exceptions and starts no
    sooner than `now + lead_time`.
    """
    if duration <= timedelta(0):
        raise ValueError("the service must last more than zero minutes")
    earliest = max(window_start, now + lead_time)
    today = now.astimezone(tz).date()
    found: list[Slot] = []
    for day in _local_days(window_start, window_end, tz):
        for schedule in hours:
            if schedule.weekday != day.isoweekday():
                continue
            margin = timedelta(minutes=schedule.buffer_minutes)
            for start in _grid_starts(day, schedule, duration, tz):
                end = start + duration
                if start < earliest or start >= window_end:
                    continue
                if _overlaps(start, end, exceptions, schedule.provider_id, timedelta(0)):
                    continue
                if _overlaps(start, end, appointments, schedule.provider_id, margin):
                    continue
                found.append(
                    Slot(
                        provider_id=schedule.provider_id,
                        starts_at=start.astimezone(UTC),
                        ends_at=end.astimezone(UTC),
                        id=slot_id(schedule.provider_id, start),
                        label=slot_label(start.astimezone(tz), today),
                    )
                )
    found.sort(key=lambda slot: (slot.starts_at, str(slot.provider_id)))
    return found


def is_open(hours: Sequence[WeeklyHours], exceptions: Sequence[Interval], tz: ZoneInfo, at: datetime) -> bool:
    """Whether any provider is working at `at`: inside the hours, outside the break and the exceptions."""
    moment = at.astimezone(tz)
    for schedule in hours:
        if schedule.weekday != moment.isoweekday():
            continue
        if not schedule.start <= moment.time() < schedule.end:
            continue
        if _on_break(schedule, moment.time()):
            continue
        if any(e.blocks(schedule.provider_id) and e.starts_at <= at < e.ends_at for e in exceptions):
            continue
        return True
    return False


def slot_id(provider_id: UUID, starts_at: datetime) -> str:
    """8 characters, the same every time for the same provider and start."""
    key = f"{provider_id}|{starts_at.astimezone(UTC).isoformat()}".encode()
    return b32encode(hashlib.blake2s(key, digest_size=5).digest()).decode().lower()


def slot_label(start: datetime, today: date) -> str:
    """'Hoy 4:00 pm', 'Mañana 10:30 am', 'Jue 12:00 pm' within a week, else 'Mié 4 nov 9:45 am'."""
    days_ahead = (start.date() - today).days
    weekday = _WEEKDAYS[start.isoweekday() - 1]
    if days_ahead == 0:
        prefix = "Hoy"
    elif days_ahead == 1:
        prefix = "Mañana"
    elif 1 < days_ahead < 7:
        prefix = weekday
    else:
        prefix = f"{weekday} {start.day} {_MONTHS[start.month - 1]}"
    hour = start.hour % 12 or 12
    label = f"{prefix} {hour}:{start.minute:02d} {'am' if start.hour < 12 else 'pm'}"
    if len(label) > MAX_LABEL_LENGTH:
        raise ValueError("slot label longer than a WhatsApp button allows")
    return label


def _on_break(schedule: WeeklyHours, moment: time) -> bool:
    if schedule.break_start is None or schedule.break_end is None:
        return False
    return schedule.break_start <= moment < schedule.break_end


def _local_days(window_start: datetime, window_end: datetime, tz: ZoneInfo) -> Iterator[date]:
    day = window_start.astimezone(tz).date()
    last = window_end.astimezone(tz).date()
    while day <= last:
        yield day
        day += timedelta(days=1)


def _grid_starts(day: date, schedule: WeeklyHours, duration: timedelta, tz: ZoneInfo) -> Iterator[datetime]:
    """Starts on the grid whose whole service fits in the morning or afternoon block."""
    blocks = [(schedule.start, schedule.end)]
    if schedule.break_start and schedule.break_end:
        blocks = [(schedule.start, schedule.break_start), (schedule.break_end, schedule.end)]
    step_minutes = int(STEP.total_seconds() // 60)
    for block_start, block_end in blocks:
        opens = datetime.combine(day, block_start, tz)
        closes = datetime.combine(day, block_end, tz)
        minutes = block_start.hour * 60 + block_start.minute
        offset = -minutes % step_minutes  # first start on the grid
        start = opens + timedelta(minutes=offset)
        while start + duration <= closes:
            yield start
            start += STEP


def _overlaps(
    start: datetime, end: datetime, intervals: Sequence[Interval], provider_id: UUID, margin: timedelta
) -> bool:
    return any(
        interval.blocks(provider_id)
        and start < interval.ends_at + margin
        and interval.starts_at < end + margin
        for interval in intervals
    )
