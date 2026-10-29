"""The work queue (Supabase Queues / pgmq).

A job carries ids only, never content: the clinic, plus the webhook item, conversation or message
to work on. Each job is idempotent by what it points to (a webhook item is processed once, an
inbound message is answered once, an outbound message is sent at most once), so a job read twice
never duplicates anything.

A job read but not finished becomes visible again after VISIBILITY_SECONDS, longer than a whole
turn. A failed job waits with exponential backoff; after MAX_ATTEMPTS it moves to the
dead-letter queue, with its error type, for a person to look at.
"""

from dataclasses import dataclass
from typing import Annotated, Any, Literal
from uuid import UUID

from psycopg import AsyncConnection
from psycopg.types.json import Jsonb
from pydantic import BaseModel, ConfigDict, Field, TypeAdapter

from app.db.connection import Database

QUEUE = "whatsapp_inbound"
DEAD_LETTERS = "whatsapp_inbound_dlq"
# Longer than a whole turn: media download, transcription and sending, each with its own timeout.
VISIBILITY_SECONDS = 120
POLL_SECONDS = 5
POLL_INTERVAL_MS = 250


class _Job(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    clinic_id: UUID


class EventJob(_Job):
    """A stored webhook item (message, status or echo) to process."""

    kind: Literal["event"] = "event"
    event_id: UUID


class ConversationJob(_Job):
    """A turn of one conversation: send what is queued and answer what the patient wrote."""

    kind: Literal["conversation"] = "conversation"
    conversation_id: UUID


class ExpireHoldJob(_Job):
    """A message written from the panel after the window closed, 24 h later: if the patient never
    came back, it is marked as not sent."""

    kind: Literal["expire_hold"] = "expire_hold"
    message_id: UUID


Job = Annotated[EventJob | ConversationJob | ExpireHoldJob, Field(discriminator="kind")]
_JOB: TypeAdapter[Job] = TypeAdapter(Job)


def parse_job(body: Any) -> Job:
    """Raises pydantic.ValidationError when the body is not one of Axon's jobs."""
    return _JOB.validate_python(body)


@dataclass(frozen=True, slots=True)
class QueuedJob:
    msg_id: int
    # 1 the first time the job is read, 2 on its first retry…
    attempt: int
    body: Any


async def enqueue(conn: AsyncConnection, job: Job, delay_seconds: int = 0) -> None:
    """Queues `job` in the caller's transaction: it exists only if that transaction commits."""
    await conn.execute(
        "select pgmq.send(%s::text, %s::jsonb, %s::integer)",
        (QUEUE, Jsonb(job.model_dump(mode="json")), max(0, delay_seconds)),
    )


async def read(db: Database, quantity: int) -> list[QueuedJob]:
    """Up to `quantity` jobs, waiting up to POLL_SECONDS for one to show up."""
    async with db.unscoped_transaction() as conn:
        cursor = await conn.execute(
            "select msg_id, read_ct, message from pgmq.read_with_poll("
            "%s::text, %s::integer, %s::integer, %s::integer, %s::integer, '{}'::jsonb)",
            (QUEUE, VISIBILITY_SECONDS, quantity, POLL_SECONDS, POLL_INTERVAL_MS),
        )
        return [QueuedJob(row[0], row[1], row[2]) for row in await cursor.fetchall()]


async def done(db: Database, msg_id: int) -> None:
    async with db.unscoped_transaction() as conn:
        await conn.execute("select pgmq.delete(%s::text, %s::bigint)", (QUEUE, msg_id))


async def retry_later(db: Database, msg_id: int, delay_seconds: float) -> None:
    async with db.unscoped_transaction() as conn:
        await conn.execute(
            "select 1 from pgmq.set_vt(%s::text, %s::bigint, %s::integer)",
            (QUEUE, msg_id, max(1, round(delay_seconds))),
        )


async def keep_hidden(db: Database, msg_id: int) -> None:
    """Gives a long job a full visibility timeout again, so no other worker picks it up."""
    await retry_later(db, msg_id, VISIBILITY_SECONDS)


async def dead_letter(db: Database, job: QueuedJob, error_type: str) -> None:
    """Moves the job to the dead-letter queue in one transaction: it is never lost nor duplicated."""
    body = job.body if isinstance(job.body, dict) else {"unreadable": True}
    failure = {"error": error_type, "attempts": job.attempt, "msg_id": job.msg_id}
    async with db.unscoped_transaction() as conn:
        await conn.execute(
            "select pgmq.send(%s::text, %s::jsonb)", (DEAD_LETTERS, Jsonb({**body, "failure": failure}))
        )
        await conn.execute("select pgmq.delete(%s::text, %s::bigint)", (QUEUE, job.msg_id))


async def replay_dead_letters(db: Database, limit: int) -> int:
    """Puts up to `limit` dead letters back in the queue, as new jobs. Returns how many."""
    moved = 0
    async with db.unscoped_transaction() as conn:
        cursor = await conn.execute(
            "select msg_id, message from pgmq.read(%s::text, %s::integer, %s::integer, '{}'::jsonb)",
            (DEAD_LETTERS, VISIBILITY_SECONDS, limit),
        )
        for msg_id, message in await cursor.fetchall():
            body = {key: value for key, value in message.items() if key != "failure"}
            await conn.execute("select pgmq.send(%s::text, %s::jsonb)", (QUEUE, Jsonb(body)))
            await conn.execute("select pgmq.delete(%s::text, %s::bigint)", (DEAD_LETTERS, msg_id))
            moved += 1
    return moved
