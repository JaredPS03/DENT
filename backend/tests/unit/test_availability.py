"""Free slots: weekly hours, breaks, buffer, exceptions, service length and the clinic's time zone."""

from datetime import UTC, date, datetime, time, timedelta
from uuid import UUID
from zoneinfo import ZoneInfo

from hypothesis import given, settings
from hypothesis import strategies as st

from app.scheduling.availability import (
    BOT_LEAD_TIME,
    MAX_LABEL_LENGTH,
    Interval,
    WeeklyHours,
    find_slots,
    is_open,
    slot_label,
)

MX = ZoneInfo("America/Mexico_City")
DENTIST = UUID("a2000000-0000-4000-8000-000000000001")
OTHER_DENTIST = UUID("a2000000-0000-4000-8000-000000000002")
# Monday 2026-10-26, a week with a month change on Sunday the 1st.
MONDAY = date(2026, 10, 26)


def local(day: date, hour: int, minute: int = 0) -> datetime:
    return datetime.combine(day, time(hour, minute), tzinfo=MX)


def weekdays(
    *,
    start: time = time(9),
    end: time = time(14),
    break_start: time | None = None,
    break_end: time | None = None,
    buffer_minutes: int = 0,
    provider_id: UUID = DENTIST,
) -> list[WeeklyHours]:
    return [
        WeeklyHours(provider_id, weekday, start, end, break_start, break_end, buffer_minutes)
        for weekday in range(1, 6)
    ]


def slots_on(
    day: date,
    *,
    hours: list[WeeklyHours],
    minutes: int = 30,
    appointments: tuple[Interval, ...] = (),
    exceptions: tuple[Interval, ...] = (),
    now: datetime | None = None,
    lead_time: timedelta = timedelta(0),
) -> list[datetime]:
    found = find_slots(
        hours=hours,
        appointments=appointments,
        exceptions=exceptions,
        duration=timedelta(minutes=minutes),
        tz=MX,
        now=now or local(day - timedelta(days=7), 8),
        window_start=local(day, 0),
        window_end=local(day + timedelta(days=1), 0),
        lead_time=lead_time,
    )
    return [slot.starts_at.astimezone(MX) for slot in found]


def test_slots_every_15_minutes_and_the_last_one_ends_at_closing_time() -> None:
    starts = slots_on(MONDAY, hours=weekdays(), minutes=60)
    assert starts[:3] == [local(MONDAY, 9), local(MONDAY, 9, 15), local(MONDAY, 9, 30)]
    assert starts[-1] == local(MONDAY, 13)  # 13:00-14:00, the last one that fits


def test_a_slot_never_crosses_the_break() -> None:
    starts = slots_on(MONDAY, hours=weekdays(break_start=time(11), break_end=time(12)), minutes=60)
    assert local(MONDAY, 10) in starts  # ends right when the break starts
    for minute in (15, 30, 45):
        assert local(MONDAY, 10, minute) not in starts
    assert local(MONDAY, 11) not in starts
    assert local(MONDAY, 12) in starts


def test_a_day_with_an_exception_offers_nothing() -> None:
    closed = Interval(local(MONDAY, 0), local(MONDAY + timedelta(days=1), 0))  # whole clinic
    assert slots_on(MONDAY, hours=weekdays(), exceptions=(closed,)) == []


def test_an_exception_of_one_dentist_does_not_close_the_other() -> None:
    hours = weekdays() + weekdays(provider_id=OTHER_DENTIST)
    away = Interval(local(MONDAY, 0), local(MONDAY + timedelta(days=1), 0), provider_id=DENTIST)
    found = find_slots(
        hours=hours,
        appointments=(),
        exceptions=(away,),
        duration=timedelta(minutes=30),
        tz=MX,
        now=local(MONDAY, 7),
        window_start=local(MONDAY, 0),
        window_end=local(MONDAY + timedelta(days=1), 0),
    )
    assert found
    assert {slot.provider_id for slot in found} == {OTHER_DENTIST}


def test_a_partial_exception_blocks_only_its_hours() -> None:
    block = Interval(local(MONDAY, 10), local(MONDAY, 12))
    starts = slots_on(MONDAY, hours=weekdays(), exceptions=(block,))
    assert local(MONDAY, 9, 30) in starts
    assert local(MONDAY, 9, 45) not in starts  # would end inside the block
    assert local(MONDAY, 11, 45) not in starts
    assert local(MONDAY, 12) in starts


def test_the_window_crosses_a_month_change() -> None:
    friday = date(2026, 10, 30)
    found = find_slots(
        hours=weekdays(),
        appointments=(),
        exceptions=(),
        duration=timedelta(minutes=60),
        tz=MX,
        now=local(friday, 7),
        window_start=local(friday, 0),
        window_end=local(date(2026, 11, 3), 0),
    )
    days = {slot.starts_at.astimezone(MX).date() for slot in found}
    assert days == {friday, date(2026, 11, 2)}  # the weekend is closed
    assert all(slot.label.startswith(("Hoy", "Lun")) for slot in found)


def test_a_service_longer_than_the_gap_does_not_fit() -> None:
    booked = (
        Interval(local(MONDAY, 9), local(MONDAY, 10), provider_id=DENTIST),
        Interval(local(MONDAY, 10, 30), local(MONDAY, 11, 30), provider_id=DENTIST),
    )
    starts = slots_on(MONDAY, hours=weekdays(), minutes=45, appointments=booked)
    assert not [start for start in starts if local(MONDAY, 9) <= start < local(MONDAY, 11, 30)]
    assert starts[0] == local(MONDAY, 11, 30)


def test_the_buffer_keeps_a_margin_on_both_sides_of_an_appointment() -> None:
    booked = (Interval(local(MONDAY, 10), local(MONDAY, 10, 30), provider_id=DENTIST),)
    starts = slots_on(MONDAY, hours=weekdays(buffer_minutes=10), minutes=30, appointments=booked)
    assert local(MONDAY, 9, 15) in starts  # ends 9:45, 15 minutes before
    assert local(MONDAY, 9, 30) not in starts  # would end right at 10:00, no margin
    assert local(MONDAY, 10, 30) not in starts  # starts right when it ends
    assert local(MONDAY, 10, 45) in starts


def test_another_dentist_s_appointment_does_not_block() -> None:
    booked = (Interval(local(MONDAY, 9), local(MONDAY, 12), provider_id=OTHER_DENTIST),)
    assert local(MONDAY, 9) in slots_on(MONDAY, hours=weekdays(), appointments=booked)


def test_never_offers_the_past_and_respects_the_lead_time() -> None:
    now = local(MONDAY, 10, 7)
    starts = slots_on(MONDAY, hours=weekdays(), now=now, lead_time=BOT_LEAD_TIME)
    assert starts[0] == local(MONDAY, 11, 15)  # 11:07 at the earliest, on the 15-minute grid
    without_lead = slots_on(MONDAY, hours=weekdays(), now=now)
    assert without_lead[0] == local(MONDAY, 10, 15)


def test_days_without_hours_offer_nothing() -> None:
    sunday = MONDAY - timedelta(days=1)
    assert slots_on(sunday, hours=weekdays()) == []


def test_slots_come_in_utc_with_ids_and_labels() -> None:
    found = find_slots(
        hours=weekdays(),
        appointments=(),
        exceptions=(),
        duration=timedelta(minutes=30),
        tz=MX,
        now=local(MONDAY, 7),
        window_start=local(MONDAY, 0),
        window_end=local(MONDAY + timedelta(days=7), 0),
    )
    first = found[0]
    assert first.starts_at.tzinfo is UTC
    assert first.starts_at == datetime(2026, 10, 26, 15, 0, tzinfo=UTC)  # 9:00 in Mexico City
    assert first.ends_at - first.starts_at == timedelta(minutes=30)
    assert first.label == "Hoy 9:00 am"
    assert len({slot.id for slot in found}) == len(found)
    assert all(len(slot.id) == 8 and slot.id.isalnum() for slot in found)


def test_labels_fit_a_whatsapp_button() -> None:
    today = MONDAY
    assert slot_label(local(today, 16), today) == "Hoy 4:00 pm"
    assert slot_label(local(today + timedelta(days=1), 10, 30), today) == "Mañana 10:30 am"
    assert slot_label(local(today + timedelta(days=3), 12), today) == "Jue 12:00 pm"
    assert slot_label(local(today + timedelta(days=9), 9, 45), today) == "Mié 4 nov 9:45 am"
    longest = slot_label(local(date(2026, 9, 23), 12, 45), today)
    assert len(longest) <= MAX_LABEL_LENGTH


def test_is_open_follows_hours_breaks_and_exceptions() -> None:
    hours = weekdays(break_start=time(11), break_end=time(12))
    assert is_open(hours, (), MX, local(MONDAY, 9))
    assert not is_open(hours, (), MX, local(MONDAY, 11, 30))
    assert not is_open(hours, (), MX, local(MONDAY, 14))
    assert not is_open(hours, (), MX, local(MONDAY - timedelta(days=1), 10))
    closed = (Interval(local(MONDAY, 0), local(MONDAY + timedelta(days=1), 0)),)
    assert not is_open(hours, closed, MX, local(MONDAY, 9))


# Properties --------------------------------------------------------------------------------

ZONES = st.sampled_from(["America/Mexico_City", "America/Cancun", "America/Tijuana"])


@st.composite
def day_schedules(draw: st.DrawFn) -> list[WeeklyHours]:
    start = time(draw(st.integers(7, 10)), draw(st.sampled_from([0, 15, 30, 45])))
    end = time(draw(st.integers(13, 20)), draw(st.sampled_from([0, 30])))
    break_start: time | None = None
    break_end: time | None = None
    if draw(st.booleans()):
        break_start = time(draw(st.integers(11, 12)), draw(st.sampled_from([0, 30])))
        break_end = time(break_start.hour + 1, break_start.minute)
    buffer_minutes = draw(st.sampled_from([0, 5, 10, 15, 30]))
    weekdays_open = draw(st.sets(st.integers(1, 7), min_size=1))
    return [
        WeeklyHours(DENTIST, weekday, start, end, break_start, break_end, buffer_minutes)
        for weekday in sorted(weekdays_open)
    ]


@st.composite
def bookings(draw: st.DrawFn, tz: ZoneInfo) -> tuple[Interval, ...]:
    result = []
    for _ in range(draw(st.integers(0, 8))):
        day = MONDAY + timedelta(days=draw(st.integers(0, 6)))
        at = time(draw(st.integers(7, 19)), draw(st.sampled_from([0, 10, 20, 40])))
        start = datetime.combine(day, at, tz)
        length = timedelta(minutes=draw(st.integers(5, 180)))
        result.append(Interval(start, start + length, provider_id=DENTIST))
    return tuple(result)


@settings(max_examples=300, deadline=None)
@given(data=st.data(), zone=ZONES, schedules=day_schedules(), minutes=st.integers(1, 36).map(lambda n: n * 5))
def test_no_slot_overlaps_an_appointment_or_leaves_the_clinic_hours(
    data: st.DataObject, zone: str, schedules: list[WeeklyHours], minutes: int
) -> None:
    tz = ZoneInfo(zone)
    booked = data.draw(bookings(tz))
    now = datetime.combine(MONDAY, time(data.draw(st.integers(0, 23)), data.draw(st.integers(0, 59))), tz)
    duration = timedelta(minutes=minutes)

    found = find_slots(
        hours=schedules,
        appointments=booked,
        exceptions=(),
        duration=duration,
        tz=tz,
        now=now,
        window_start=datetime.combine(MONDAY, time(0), tz),
        window_end=datetime.combine(MONDAY + timedelta(days=7), time(0), tz),
        lead_time=BOT_LEAD_TIME,
    )

    by_weekday = {hours.weekday: hours for hours in schedules}
    for slot in found:
        start, end = slot.starts_at.astimezone(tz), slot.ends_at.astimezone(tz)
        assert end - start == duration
        assert slot.starts_at >= now + BOT_LEAD_TIME
        hours = by_weekday[start.isoweekday()]
        assert start.date() == end.date() or end.time() == time(0)
        assert hours.start <= start.time()
        assert end.time() <= hours.end
        if hours.break_start and hours.break_end:
            assert end.time() <= hours.break_start or start.time() >= hours.break_end
        margin = timedelta(minutes=hours.buffer_minutes)
        for appointment in booked:
            assert end + margin <= appointment.starts_at or start >= appointment.ends_at + margin
        assert len(slot.label) <= MAX_LABEL_LENGTH
