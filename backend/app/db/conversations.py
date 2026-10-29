"""SQL of patients, conversations and messages. Every function takes a connection from
Database.clinic_transaction, so RLS already limits it to that clinic; nothing here receives a
clinic id it did not get from there."""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Literal
from uuid import UUID

from psycopg import AsyncConnection, errors
from psycopg.types.json import Jsonb

from app.whatsapp.inbound import EchoMessage, InboundMessage
from app.whatsapp.window import CUSTOMER_SERVICE_WINDOW, WINDOW_CLOSED_CODE, BillingCategory, DeliveryStatus

# A message held for a closed window waits this long for the patient to come back.
HOLD_TTL = CUSTOMER_SERVICE_WINDOW


@dataclass(frozen=True, slots=True)
class Conversation:
    id: UUID
    patient_id: UUID
    status: str
    window_expires_at: datetime | None
    bot_paused_until: datetime | None


@dataclass(frozen=True, slots=True)
class PendingMessage:
    """An inbound message no turn has taken care of yet."""

    id: UUID
    type: str
    content: str | None
    transcription: str | None
    meta_media_id: str | None
    media_mime_type: str | None
    media_path: str | None
    created_at: datetime


@dataclass(frozen=True, slots=True)
class OutboxItem:
    """The next message of a conversation to send, with what sending it needs."""

    id: UUID
    type: str
    send_payload: dict[str, Any] | None
    patient_phone: str | None
    patient_bsuid: str | None
    window_expires_at: datetime | None
    phone_number_id: str | None
    # The database's clock, the one the window was measured with.
    now: datetime


@dataclass(frozen=True, slots=True)
class OutboundMessage:
    id: UUID
    delivery_status: str | None
    created: bool


_CONVERSATION_COLUMNS = "id, patient_id, status, window_expires_at, bot_paused_until"


# Patients ------------------------------------------------------------------------------------


async def find_or_create_patient(
    conn: AsyncConnection, clinic_id: UUID, phone: str | None, bsuid: str | None
) -> UUID:
    """The patient behind a WhatsApp user, created on their first message. The BSUID is the
    stronger key (a username can hide the phone); each identifier fills in the other if missing."""
    if phone is None and bsuid is None:
        raise ValueError("a patient needs a phone or a BSUID")
    by_bsuid = await _patient_by(conn, "bsuid", bsuid)
    by_phone = await _patient_by(conn, "phone", phone)
    found = by_bsuid or by_phone
    if found is not None:
        patient_id, known_phone, known_bsuid = found
        if bsuid and known_bsuid is None:
            await _fill_in(conn, patient_id, "bsuid", bsuid)
        if phone and known_phone is None and by_phone is None:
            await _fill_in(conn, patient_id, "phone", phone)
        return patient_id

    cursor = await conn.execute(
        """
        insert into public.patients (clinic_id, phone, bsuid, source) values (%s, %s, %s, 'bot')
        on conflict do nothing
        returning id
        """,
        (clinic_id, phone, bsuid),
    )
    row = await cursor.fetchone()
    if row is not None:
        return UUID(str(row[0]))
    # Another worker created them a moment ago.
    again = await _patient_by(conn, "bsuid", bsuid) or await _patient_by(conn, "phone", phone)
    if again is None:
        raise LookupError("the patient is not there right after a conflicting insert")
    return again[0]


async def _patient_by(
    conn: AsyncConnection, column: Literal["phone", "bsuid"], value: str | None
) -> tuple[UUID, str | None, str | None] | None:
    if value is None:
        return None
    query = {
        "phone": "select id, phone, bsuid from public.patients where phone = %s",
        "bsuid": "select id, phone, bsuid from public.patients where bsuid = %s",
    }[column]
    cursor = await conn.execute(query, (value,))
    row = await cursor.fetchone()
    return (row[0], row[1], row[2]) if row else None


async def _fill_in(
    conn: AsyncConnection, patient_id: UUID, column: Literal["phone", "bsuid"], value: str
) -> None:
    """Adds the identifier the patient was missing, unless another patient already has it."""
    query = {
        "phone": "update public.patients set phone = %s where id = %s and phone is null",
        "bsuid": "update public.patients set bsuid = %s where id = %s and bsuid is null",
    }[column]
    try:
        async with conn.transaction():
            await conn.execute(query, (value, patient_id))
    except errors.UniqueViolation:
        pass


# Conversations -----------------------------------------------------------------------------------


async def open_conversation(conn: AsyncConnection, clinic_id: UUID, patient_id: UUID) -> Conversation:
    """The patient's conversation, created if needed and locked until the transaction ends."""
    await conn.execute(
        """
        insert into public.conversations (clinic_id, patient_id) values (%s, %s)
        on conflict (clinic_id, patient_id) do nothing
        """,
        (clinic_id, patient_id),
    )
    cursor = await conn.execute(
        f"select {_CONVERSATION_COLUMNS} from public.conversations where patient_id = %s for update",  # noqa: S608
        (patient_id,),
    )
    return _conversation(await cursor.fetchone())


async def conversation_for_update(conn: AsyncConnection, conversation_id: UUID) -> Conversation | None:
    cursor = await conn.execute(
        f"select {_CONVERSATION_COLUMNS} from public.conversations where id = %s for update",  # noqa: S608
        (conversation_id,),
    )
    row = await cursor.fetchone()
    return _conversation(row) if row else None


async def record_patient_activity(
    conn: AsyncConnection, conversation_id: UUID, occurred_at: datetime
) -> None:
    """The patient wrote: the 24 h window opens or renews, and a closed chat reopens with the bot."""
    await conn.execute(
        """
        update public.conversations
        set window_expires_at = greatest(coalesce(window_expires_at, '-infinity'), %s),
            last_activity_at = greatest(coalesce(last_activity_at, '-infinity'), %s),
            status = case when status = 'closed' then 'bot' else status end
        where id = %s
        """,
        (occurred_at + CUSTOMER_SERVICE_WINDOW, occurred_at, conversation_id),
    )


async def pause_bot(
    conn: AsyncConnection, conversation_id: UUID, until: datetime, activity_at: datetime
) -> None:
    await conn.execute(
        """
        update public.conversations
        set bot_paused_until = greatest(coalesce(bot_paused_until, '-infinity'), %s),
            last_activity_at = greatest(coalesce(last_activity_at, '-infinity'), %s)
        where id = %s
        """,
        (until, activity_at, conversation_id),
    )


async def set_status(conn: AsyncConnection, conversation_id: UUID, status: str) -> None:
    await conn.execute(
        """
        update public.conversations
        set status = %s, bot_paused_until = case when %s = 'bot' then null else bot_paused_until end
        where id = %s
        """,
        (status, status, conversation_id),
    )


async def human_reply_pause(conn: AsyncConnection) -> timedelta:
    cursor = await conn.execute("select human_reply_pause_minutes from public.clinics")
    row = await cursor.fetchone()
    if row is None:
        raise LookupError("no clinic is fixed in this transaction")
    return timedelta(minutes=row[0])


@dataclass(frozen=True, slots=True)
class BotSettings:
    clinic_bot_enabled: bool
    clinic_name: str


async def bot_settings(conn: AsyncConnection) -> BotSettings:
    cursor = await conn.execute("select bot_enabled, name from public.clinics")
    row = await cursor.fetchone()
    if row is None:
        raise LookupError("no clinic is fixed in this transaction")
    return BotSettings(row[0], row[1])


# Inbound messages --------------------------------------------------------------------------------


async def insert_inbound(
    conn: AsyncConnection, clinic_id: UUID, conversation_id: UUID, message: InboundMessage
) -> UUID | None:
    """Stores the patient's message once. None if Meta's id was already stored (a retry)."""
    cursor = await conn.execute(
        """
        insert into public.messages
          (clinic_id, conversation_id, direction, sender, type, content, reply_id,
           meta_message_id, meta_media_id, media_mime_type, occurred_at)
        values (%s, %s, 'inbound', 'patient', %s, %s, %s, %s, %s, %s, %s)
        on conflict (meta_message_id) do nothing
        returning id
        """,
        (
            clinic_id,
            conversation_id,
            message.type,
            message.content,
            message.reply_id,
            message.meta_id,
            message.media_id,
            message.media_mime_type[:100] if message.media_mime_type else None,
            message.occurred_at,
        ),
    )
    row = await cursor.fetchone()
    return row[0] if row else None


async def insert_echo(
    conn: AsyncConnection, clinic_id: UUID, conversation_id: UUID, echo: EchoMessage
) -> UUID | None:
    """What the clinic sent from its own app, as a human message. None if already stored."""
    cursor = await conn.execute(
        """
        insert into public.messages
          (clinic_id, conversation_id, direction, sender, type, content, meta_message_id, occurred_at)
        values (%s, %s, 'outbound', 'human', %s, %s, %s, %s)
        on conflict (meta_message_id) do nothing
        returning id
        """,
        (clinic_id, conversation_id, echo.type, echo.content, echo.meta_id, echo.occurred_at),
    )
    row = await cursor.fetchone()
    return row[0] if row else None


async def unhandled_inbound(
    conn: AsyncConnection, conversation_id: UUID
) -> tuple[list[PendingMessage], datetime]:
    """The patient's messages no turn has taken care of, in the order the patient sent them (Meta's
    webhooks may arrive out of order), and the database's clock, the one that also timed the queue."""
    cursor = await conn.execute(
        """
        select id, type, content, transcription, meta_media_id, media_mime_type, media_path, created_at,
               now()
        from public.messages
        where conversation_id = %s and direction = 'inbound' and handled_at is null
        order by occurred_at, created_at, id
        """,
        (conversation_id,),
    )
    rows = await cursor.fetchall()
    now = rows[0][-1] if rows else await _now(conn)
    return [PendingMessage(*row[:-1]) for row in rows], now


async def mark_handled(conn: AsyncConnection, message_ids: Sequence[UUID]) -> None:
    await conn.execute(
        "update public.messages set handled_at = now() where id = any(%s) and handled_at is null",
        (list(message_ids),),
    )


async def set_media_stored(conn: AsyncConnection, message_id: UUID, media_path: str | None) -> None:
    """The file is in Storage (or will never be): Meta's media id is no longer needed."""
    await conn.execute(
        "update public.messages set media_path = %s, meta_media_id = null where id = %s",
        (media_path, message_id),
    )


async def set_transcription(conn: AsyncConnection, message_id: UUID, transcription: str) -> None:
    await conn.execute(
        "update public.messages set transcription = %s where id = %s and transcription is null",
        (transcription, message_id),
    )


# Outbound messages -------------------------------------------------------------------------------


async def insert_outbound(  # noqa: PLR0913 - one value per column
    conn: AsyncConnection,
    *,
    clinic_id: UUID,
    conversation_id: UUID,
    sender: Literal["bot", "human", "system"],
    message_type: str,
    content: str | None,
    send_payload: dict[str, Any],
    billing_category: BillingCategory,
    delivery_status: DeliveryStatus,
    idempotency_key: str,
) -> OutboundMessage:
    """Queues a message to send. A second call with the same key finds the first one."""
    cursor = await conn.execute(
        """
        insert into public.messages
          (clinic_id, conversation_id, direction, sender, type, content, send_payload,
           billing_category, delivery_status, idempotency_key, created_at, occurred_at)
        -- clock_timestamp(): two messages queued in one transaction keep their order.
        values (%s, %s, 'outbound', %s, %s, %s, %s, %s, %s, %s, clock_timestamp(), clock_timestamp())
        on conflict (clinic_id, idempotency_key) do nothing
        returning id, delivery_status
        """,
        (
            clinic_id,
            conversation_id,
            sender,
            message_type,
            content,
            Jsonb(send_payload),
            billing_category,
            delivery_status,
            idempotency_key,
        ),
    )
    row = await cursor.fetchone()
    if row is not None:
        return OutboundMessage(row[0], row[1], created=True)
    cursor = await conn.execute(
        "select id, delivery_status from public.messages where idempotency_key = %s", (idempotency_key,)
    )
    existing = await cursor.fetchone()
    if existing is None:
        raise LookupError("the message is not there right after a conflicting insert")
    return OutboundMessage(existing[0], existing[1], created=False)


async def next_outbound(conn: AsyncConnection, conversation_id: UUID) -> OutboxItem | None:
    """The oldest message of the conversation waiting to be sent, locked."""
    cursor = await conn.execute(
        """
        select m.id, m.type, m.send_payload, p.phone, p.bsuid, c.window_expires_at,
               cl.whatsapp_phone_number_id, now()
        from public.messages m
        join public.conversations c on c.id = m.conversation_id
        join public.patients p on p.id = c.patient_id
        join public.clinics cl on cl.id = m.clinic_id
        where m.conversation_id = %s and m.direction = 'outbound'
          and m.delivery_status = 'pending' and m.send_started_at is null
        order by m.created_at, m.id
        limit 1
        for update of m skip locked
        """,
        (conversation_id,),
    )
    row = await cursor.fetchone()
    return OutboxItem(*row) if row else None


async def mark_send_started(conn: AsyncConnection, message_id: UUID) -> None:
    await conn.execute("update public.messages set send_started_at = now() where id = %s", (message_id,))


async def mark_not_sent(conn: AsyncConnection, message_id: UUID) -> None:
    """Meta never got it: the next attempt may send it."""
    await conn.execute(
        "update public.messages set send_started_at = null where id = %s and meta_message_id is null",
        (message_id,),
    )


async def mark_sent(conn: AsyncConnection, message_id: UUID, meta_message_id: str) -> None:
    """Meta accepted it. A status webhook may have been faster and already advanced it."""
    await conn.execute(
        """
        update public.messages
        set meta_message_id = coalesce(meta_message_id, %s),
            delivery_status = case when delivery_status = 'pending' then 'sent' else delivery_status end,
            occurred_at = now()
        where id = %s
        """,
        (meta_message_id, message_id),
    )


async def mark_failed(conn: AsyncConnection, message_id: UUID, error_code: int | None) -> None:
    await conn.execute(
        "update public.messages set delivery_status = 'failed', delivery_error_code = %s where id = %s",
        (error_code, message_id),
    )


async def release_held(conn: AsyncConnection, conversation_id: UUID) -> int:
    """The patient came back: held messages still fresh go out; older ones are marked not sent."""
    await conn.execute(
        """
        update public.messages set delivery_status = 'failed', delivery_error_code = %s
        where conversation_id = %s and delivery_status = 'held' and created_at <= now() - %s
        """,
        (WINDOW_CLOSED_CODE, conversation_id, HOLD_TTL),
    )
    cursor = await conn.execute(
        """
        update public.messages set delivery_status = 'pending'
        where conversation_id = %s and delivery_status = 'held' and created_at > now() - %s
        """,
        (conversation_id, HOLD_TTL),
    )
    return cursor.rowcount


async def expire_held(conn: AsyncConnection, message_id: UUID) -> bool:
    cursor = await conn.execute(
        """
        update public.messages set delivery_status = 'failed', delivery_error_code = %s
        where id = %s and delivery_status = 'held' and created_at <= now() - %s
        """,
        (WINDOW_CLOSED_CODE, message_id, HOLD_TTL),
    )
    return cursor.rowcount > 0


@dataclass(frozen=True, slots=True)
class TrackedMessage:
    id: UUID
    delivery_status: str | None
    meta_message_id: str | None


async def message_for_status(
    conn: AsyncConnection, meta_message_id: str, callback_id: UUID | None
) -> TrackedMessage | None:
    """The outbound message a status talks about: by Meta's id, or by the id Axon sent along as
    biz_opaque_callback_data when Meta's id never got saved."""
    cursor = await conn.execute(
        """
        select id, delivery_status, meta_message_id from public.messages
        where direction = 'outbound'
          and (meta_message_id = %s or (id = %s and meta_message_id is null))
        order by meta_message_id is null
        limit 1
        for update
        """,
        (meta_message_id, callback_id),
    )
    row = await cursor.fetchone()
    return TrackedMessage(*row) if row else None


async def apply_status(  # noqa: PLR0913 - one value per column
    conn: AsyncConnection,
    message_id: UUID,
    *,
    meta_message_id: str,
    new_status: str | None,
    error_code: int | None,
    billable: bool | None,
    billing_category: BillingCategory | None,
) -> None:
    await conn.execute(
        """
        update public.messages
        set meta_message_id = coalesce(meta_message_id, %s),
            delivery_status = coalesce(%s, delivery_status),
            delivery_error_code = case when %s = 'failed' then %s else delivery_error_code end,
            billable = coalesce(%s, billable),
            billing_category = coalesce(%s, billing_category)
        where id = %s
        """,
        (meta_message_id, new_status, new_status, error_code, billable, billing_category, message_id),
    )


# Turn lease ----------------------------------------------------------------------------------


async def acquire_lease(
    conn: AsyncConnection, conversation_id: UUID, token: UUID, seconds: int
) -> Literal["acquired", "busy", "missing"]:
    cursor = await conn.execute(
        """
        select turn_lease_expires_at is not null and turn_lease_expires_at >= now()
        from public.conversations where id = %s for update
        """,
        (conversation_id,),
    )
    row = await cursor.fetchone()
    if row is None:
        return "missing"  # erased (ARCO) or not this clinic's
    if row[0]:
        return "busy"
    await conn.execute(
        """
        update public.conversations
        set turn_lease_token = %s, turn_lease_expires_at = now() + make_interval(secs => %s)
        where id = %s
        """,
        (token, seconds, conversation_id),
    )
    return "acquired"


async def renew_lease(conn: AsyncConnection, conversation_id: UUID, token: UUID, seconds: int) -> bool:
    cursor = await conn.execute(
        """
        update public.conversations set turn_lease_expires_at = now() + make_interval(secs => %s)
        where id = %s and turn_lease_token = %s and turn_lease_expires_at > now()
        """,
        (seconds, conversation_id, token),
    )
    return cursor.rowcount == 1


async def holds_lease(conn: AsyncConnection, conversation_id: UUID, token: UUID) -> bool:
    """Whether the lease is still ours, locking the conversation until the transaction ends."""
    cursor = await conn.execute(
        """
        select 1 from public.conversations
        where id = %s and turn_lease_token = %s and turn_lease_expires_at > now()
        for update
        """,
        (conversation_id, token),
    )
    return await cursor.fetchone() is not None


async def release_lease(conn: AsyncConnection, conversation_id: UUID, token: UUID) -> None:
    await conn.execute(
        """
        update public.conversations set turn_lease_token = null, turn_lease_expires_at = null
        where id = %s and turn_lease_token = %s
        """,
        (conversation_id, token),
    )


async def _now(conn: AsyncConnection) -> datetime:
    cursor = await conn.execute("select now()")
    row = await cursor.fetchone()
    if row is None:
        raise LookupError("now() returned nothing")
    value: datetime = row[0]
    return value


def _conversation(row: tuple[Any, ...] | None) -> Conversation:
    if row is None:
        raise LookupError("the conversation row is not there inside its own transaction")
    return Conversation(*row)


# Panel -----------------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ApprovedTemplate:
    name: str
    language: str
    category: BillingCategory


async def approved_template(conn: AsyncConnection, name: str) -> ApprovedTemplate | None:
    cursor = await conn.execute(
        """
        select name, language, category from public.templates
        where name = %s and status = 'approved'
        order by language = 'es_MX' desc
        limit 1
        """,
        (name,),
    )
    row = await cursor.fetchone()
    return ApprovedTemplate(*row) if row else None


async def reopen_request_pending(conn: AsyncConnection, conversation_id: UUID, template_name: str) -> bool:
    """Whether the template that asks the patient to come back was already sent (or queued) after
    their last message and within the last 24 h: one is enough, and each one is charged."""
    cursor = await conn.execute(
        """
        select exists (
          select 1 from public.messages m
          where m.conversation_id = %s and m.direction = 'outbound' and m.type = 'template'
            and m.content = %s and m.delivery_status <> 'failed'
            and m.created_at > now() - %s
            and m.created_at > coalesce(
              (select max(i.created_at) from public.messages i
               where i.conversation_id = %s and i.direction = 'inbound'),
              '-infinity')
        )
        """,
        (conversation_id, template_name, CUSTOMER_SERVICE_WINDOW, conversation_id),
    )
    row = await cursor.fetchone()
    return bool(row and row[0])


async def patient_and_clinic_names(conn: AsyncConnection, conversation_id: UUID) -> tuple[str | None, str]:
    cursor = await conn.execute(
        """
        select p.name, cl.name from public.conversations c
        join public.patients p on p.id = c.patient_id
        join public.clinics cl on cl.id = c.clinic_id
        where c.id = %s
        """,
        (conversation_id,),
    )
    row = await cursor.fetchone()
    if row is None:
        raise LookupError("the conversation is not there inside its own transaction")
    return row[0], row[1]


async def database_now(conn: AsyncConnection) -> datetime:
    return await _now(conn)


async def insert_audit(  # noqa: PLR0913 - one value per column
    conn: AsyncConnection,
    *,
    clinic_id: UUID,
    user_id: UUID,
    action: str,
    table_name: str,
    record_id: UUID,
    changes: dict[str, Any],
) -> None:
    """Who did what from the panel. Ids and states only, never the text of a message."""
    await conn.execute(
        """
        insert into public.audit_log
          (clinic_id, actor_type, actor_user_id, action, table_name, record_id, changes)
        values (%s, 'user', %s, %s, %s, %s, %s)
        """,
        (clinic_id, user_id, action, table_name, record_id, Jsonb(changes)),
    )
