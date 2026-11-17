"""What the scheduled runs look for in one clinic (milestone 1D): urgencies nobody attended,
patients without an answer, sends with an unknown outcome and reminders that failed; and what
the daily cleanup removes.

Every function runs inside a clinic transaction. `now` comes from the worker's clock, so the
tests can move it.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from uuid import UUID

from psycopg import AsyncConnection

# A patient's message waits this long for a turn before Jared hears of it (spec, monitoring).
UNANSWERED_AFTER = timedelta(minutes=5)
# A message handed to Meta without an answer this long ago has an unknown outcome.
UNCERTAIN_AFTER = timedelta(minutes=5)
# Older urgencies are not alerted again (the worker was down, or they predate milestone 1D).
REALERT_HORIZON = timedelta(hours=6)
# Reminders that failed are counted over the last hour, as often as alerts may go.
FAILED_REMINDERS_WINDOW = timedelta(hours=1)


async def urgencies_to_realert(conn: AsyncConnection, now: datetime) -> list[UUID]:
    """Urgent escalations still open after the clinic's minutes, alerted only once so far, with no
    answer of the team since: neither from the panel nor from the clinic's own WhatsApp app."""
    cursor = await conn.execute(
        """
        select e.id
        from public.escalations e
        cross join (select urgent_realert_minutes from public.clinics) c
        where e.priority = 'urgent' and e.status = 'open' and e.realerted_at is null
          and e.created_at <= %(now)s - make_interval(mins => c.urgent_realert_minutes)
          and e.created_at > %(now)s - %(horizon)s
          and not exists (
            select 1 from public.messages m
            where m.conversation_id = e.conversation_id and m.direction = 'outbound'
              and m.sender = 'human' and m.created_at > e.created_at
          )
        order by e.created_at
        for update of e skip locked
        """,
        {"now": now, "horizon": REALERT_HORIZON},
    )
    return [row[0] for row in await cursor.fetchall()]


async def mark_realerted(conn: AsyncConnection, escalation_id: UUID, now: datetime) -> None:
    await conn.execute("update public.escalations set realerted_at = %s where id = %s", (now, escalation_id))


@dataclass(frozen=True, slots=True)
class Troubles:
    unanswered: int
    uncertain_sends: int
    failed_reminders: int


async def troubles(conn: AsyncConnection, now: datetime) -> Troubles:
    cursor = await conn.execute(
        """
        select
          (select count(*) from public.messages
           where direction = 'inbound' and handled_at is null and created_at <= %(now)s - %(unanswered)s),
          (select count(*) from public.messages
           where direction = 'outbound' and delivery_status = 'pending' and meta_message_id is null
             and send_started_at <= %(now)s - %(uncertain)s
             and send_started_at > %(now)s - interval '24 hours'),
          (select count(*) from public.appointment_reminders r
           join public.messages m on m.id = r.message_id
           where r.status = 'queued' and m.delivery_status = 'failed'
             and m.updated_at > %(now)s - %(window)s)
        """,
        {
            "now": now,
            "unanswered": UNANSWERED_AFTER,
            "uncertain": UNCERTAIN_AFTER,
            "window": FAILED_REMINDERS_WINDOW,
        },
    )
    row = await cursor.fetchone()
    assert row is not None  # noqa: S101 - a select of counts always returns a row
    return Troubles(*row)


# Cleanup ------------------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class CleanupSettings:
    timezone: str
    media_retention_days: int


async def cleanup_settings(conn: AsyncConnection) -> CleanupSettings:
    cursor = await conn.execute("select timezone, media_retention_days from public.clinics")
    row = await cursor.fetchone()
    if row is None:
        raise LookupError("the clinic of the transaction is not there")
    return CleanupSettings(*row)


async def expire_media(conn: AsyncConnection, now: datetime, limit: int) -> list[str]:
    """Unlinks audios and photos older than the clinic's retention and returns their paths, for
    the caller to delete from Storage once this commits. The transcription stays."""
    cursor = await conn.execute(
        """
        with old as (
          select m.id, m.media_path
          from public.messages m
          cross join (select media_retention_days from public.clinics) c
          where m.media_path is not null
            and m.created_at < %(now)s - make_interval(days => c.media_retention_days)
          order by m.created_at
          limit %(limit)s
          for update of m skip locked
        )
        update public.messages m set media_path = null
        from old where m.id = old.id
        returning old.media_path
        """,
        {"now": now, "limit": limit},
    )
    return [row[0] for row in await cursor.fetchall()]


async def purge_webhook_payloads(conn: AsyncConnection, processed_before: datetime, now: datetime) -> int:
    """Erases the content of Meta's notices processed before the date; the row stays as a trace."""
    cursor = await conn.execute(
        """
        update public.webhook_events set payload = null, payload_purged_at = %s
        where processed_at < %s and payload is not null
        """,
        (now, processed_before),
    )
    return cursor.rowcount


async def delete_old_offered_slots(conn: AsyncConnection, offered_before: datetime) -> int:
    cursor = await conn.execute("delete from public.offered_slots where offered_at < %s", (offered_before,))
    return cursor.rowcount


async def referenced_paths(conn: AsyncConnection, paths: Sequence[str]) -> set[str]:
    """Which of these Storage paths a message of the clinic still points to."""
    if not paths:
        return set()
    cursor = await conn.execute(
        "select media_path from public.messages where media_path = any(%s)", (list(paths),)
    )
    return {row[0] for row in await cursor.fetchall()}
