"""What the bot reads and writes in a turn (milestone 1C).

Every function runs inside a clinic_transaction: RLS keeps each query in the clinic fixed there,
and the ids it receives were checked against that clinic before.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from uuid import UUID
from zoneinfo import ZoneInfo

from psycopg import AsyncConnection

from app.db.agenda import active_provider_ids, weekly_hours
from app.scheduling.availability import WeeklyHours

# Clinic and patient -----------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ClinicProfile:
    id: UUID
    name: str
    city: str | None
    address: str | None
    maps_url: str | None
    tz: ZoneInfo
    doctor: str | None
    formal: bool
    urgency_signals: tuple[str, ...]
    notice_url: str | None
    notice_version: str | None
    payment_methods: str | None
    cancellation_policy: str | None
    bot_enabled: bool


async def clinic_profile(conn: AsyncConnection) -> ClinicProfile:
    cursor = await conn.execute(
        """
        select id, name, city, address, maps_url, timezone, doctor_display_name,
               patient_address_form = 'formal', urgency_signals, privacy_notice_url,
               privacy_notice_version, payment_methods, cancellation_policy, bot_enabled
        from public.clinics
        """
    )
    row = await cursor.fetchone()
    if row is None:
        raise LookupError("no clinic is fixed in this transaction")
    return ClinicProfile(
        id=row[0],
        name=row[1],
        city=row[2],
        address=row[3],
        maps_url=row[4],
        tz=ZoneInfo(row[5]),
        doctor=row[6],
        formal=row[7],
        urgency_signals=tuple(row[8]),
        notice_url=row[9],
        notice_version=row[10],
        payment_methods=row[11],
        cancellation_policy=row[12],
        bot_enabled=row[13],
    )


@dataclass(frozen=True, slots=True)
class ServiceInfo:
    id: UUID
    name: str
    duration_minutes: int
    price_cents: int | None  # only when the clinic chose to show it


async def active_services(conn: AsyncConnection) -> list[ServiceInfo]:
    cursor = await conn.execute(
        """
        select id, name, duration_minutes, case when show_price then reference_price_cents end
        from public.services where active order by name
        """
    )
    return [ServiceInfo(*row) for row in await cursor.fetchall()]


async def clinic_hours(conn: AsyncConnection) -> list[WeeklyHours]:
    return await weekly_hours(conn, await active_provider_ids(conn))


@dataclass(frozen=True, slots=True)
class PatientProfile:
    name: str | None
    consented: bool  # accepted the clinic's current privacy notice
    returning: bool  # has had an appointment before


async def patient_profile(
    conn: AsyncConnection, patient_id: UUID, notice_version: str | None
) -> PatientProfile:
    cursor = await conn.execute(
        """
        select p.name,
               exists (select 1 from public.consents c
                       where c.patient_id = p.id and c.notice_version = %s),
               exists (select 1 from public.appointments a where a.patient_id = p.id)
        from public.patients p where p.id = %s
        """,
        (notice_version, patient_id),
    )
    row = await cursor.fetchone()
    if row is None:
        raise LookupError("patient not found in this clinic")
    return PatientProfile(name=row[0], consented=row[1], returning=row[2])


async def set_patient_name(conn: AsyncConnection, patient_id: UUID, name: str) -> None:
    await conn.execute("update public.patients set name = %s where id = %s", (name, patient_id))


@dataclass(frozen=True, slots=True)
class UpcomingAppointment:
    id: UUID
    service_id: UUID
    service_name: str
    starts_at: datetime


async def upcoming_appointments(
    conn: AsyncConnection, patient_id: UUID, now: datetime, *, limit: int = 5
) -> list[UpcomingAppointment]:
    cursor = await conn.execute(
        """
        select a.id, a.service_id, s.name, a.starts_at
        from public.appointments a join public.services s on s.id = a.service_id
        where a.patient_id = %s and a.status in ('pending', 'confirmed') and a.starts_at > %s
        order by a.starts_at
        limit %s
        """,
        (patient_id, now, limit),
    )
    return [UpcomingAppointment(*row) for row in await cursor.fetchall()]


# Consent ----------------------------------------------------------------------------------------


async def accept_consent(
    conn: AsyncConnection, clinic_id: UUID, patient_id: UUID, notice_version: str, message_id: UUID
) -> None:
    """Proof of consent: the notice version and the message where the patient tapped Acepto."""
    await conn.execute(
        """
        insert into public.consents (clinic_id, patient_id, notice_version, accepted_at, message_id)
        select %s, %s, %s, now(), %s
        where not exists (
          select 1 from public.consents where patient_id = %s and notice_version = %s
        )
        """,
        (clinic_id, patient_id, notice_version, message_id, patient_id, notice_version),
    )


async def mark_consent_requested(conn: AsyncConnection, conversation_id: UUID) -> None:
    await conn.execute(
        "update public.conversations set consent_requested_at = now() where id = %s", (conversation_id,)
    )


async def consent_requested_at(conn: AsyncConnection, conversation_id: UUID) -> datetime | None:
    cursor = await conn.execute(
        "select consent_requested_at from public.conversations where id = %s", (conversation_id,)
    )
    row = await cursor.fetchone()
    return row[0] if row else None


async def oldest_unerased_unconsented(
    conn: AsyncConnection, conversation_id: UUID, notice_version: str | None
) -> datetime | None:
    """When the oldest message still kept of a patient who has not accepted the notice arrived."""
    cursor = await conn.execute(
        """
        select min(m.created_at)
        from public.messages m join public.conversations c on c.id = m.conversation_id
        where m.conversation_id = %s and m.direction = 'inbound'
          and (m.content is not null or m.transcription is not null or m.media_path is not null)
          and not exists (
            select 1 from public.consents k where k.patient_id = c.patient_id and k.notice_version = %s
          )
        """,
        (conversation_id, notice_version),
    )
    row = await cursor.fetchone()
    return row[0] if row else None


async def unconsented_media_paths(
    conn: AsyncConnection, conversation_id: UUID, notice_version: str | None, before: datetime
) -> list[str]:
    """Files that erase_unconsented will let go of, to delete from Storage first."""
    cursor = await conn.execute(
        """
        select m.media_path
        from public.messages m join public.conversations c on c.id = m.conversation_id
        where m.conversation_id = %s and m.direction = 'inbound' and m.created_at <= %s
          and m.media_path is not null
          and not exists (
            select 1 from public.consents k where k.patient_id = c.patient_id and k.notice_version = %s
          )
        """,
        (conversation_id, before, notice_version),
    )
    return [row[0] for row in await cursor.fetchall()]


async def erase_unconsented(
    conn: AsyncConnection, conversation_id: UUID, notice_version: str | None, before: datetime
) -> int:
    """Erases what a patient who never accepted the notice wrote up to `before`: text,
    transcriptions and the path of their files (deleted from Storage before). Nothing if the
    patient accepted in the meantime. Returns how many messages were erased."""
    cursor = await conn.execute(
        """
        with target as (
          select m.id, m.media_path
          from public.messages m
          join public.conversations c on c.id = m.conversation_id
          where m.conversation_id = %s and m.direction = 'inbound' and m.created_at <= %s
            and (m.content is not null or m.transcription is not null or m.media_path is not null)
            and not exists (
              select 1 from public.consents k
              where k.patient_id = c.patient_id and k.notice_version = %s
            )
          for update of m
        )
        update public.messages m
        set content = null, transcription = null, media_path = null
        from target
        where m.id = target.id
        """,
        (conversation_id, before, notice_version),
    )
    return cursor.rowcount


# History ----------------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class HistoryRow:
    direction: str
    sender: str
    type: str
    content: str | None
    transcription: str | None
    reply_id: str | None
    created_at: datetime


async def recent_history(
    conn: AsyncConnection, conversation_id: UUID, *, since: datetime, limit: int
) -> list[HistoryRow]:
    """The last messages of the conversation, oldest first, with the patient's messages this turn
    takes care of always at the end: Meta's timestamps have whole seconds, so a quick tap could
    otherwise sort before the message it answers. Outbound messages count only once sent or on
    their way (a held or failed one never reached the patient)."""
    cursor = await conn.execute(
        """
        select direction, sender, type, content, transcription, reply_id, created_at
        from (
          select *, (direction = 'inbound' and handled_at is null) as current_batch
          from public.messages
          where conversation_id = %s and created_at >= %s
            and (direction = 'inbound' or delivery_status in ('pending', 'sent', 'delivered', 'read'))
          order by current_batch desc, occurred_at desc, created_at desc, id desc
          limit %s
        ) recent
        order by current_batch, occurred_at, created_at, id
        """,
        (conversation_id, since, limit),
    )
    return [HistoryRow(*row) for row in await cursor.fetchall()]


# Offered slots ----------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class StoredSlot:
    key: str
    service_id: UUID
    service_name: str
    provider_id: UUID
    starts_at: datetime
    label: str
    offered_at: datetime


async def offered_slots(conn: AsyncConnection, conversation_id: UUID, *, since: datetime) -> list[StoredSlot]:
    cursor = await conn.execute(
        """
        select o.slot_key, o.service_id, s.name, o.provider_id, o.starts_at, o.label, o.offered_at
        from public.offered_slots o join public.services s on s.id = o.service_id
        where o.conversation_id = %s and o.offered_at >= %s
        order by o.offered_at, o.starts_at
        """,
        (conversation_id, since),
    )
    return [StoredSlot(*row) for row in await cursor.fetchall()]


@dataclass(frozen=True, slots=True)
class NewSlot:
    key: str
    service_id: UUID
    provider_id: UUID
    starts_at: datetime
    label: str


async def save_offered_slots(
    conn: AsyncConnection, clinic_id: UUID, conversation_id: UUID, slots: Sequence[NewSlot]
) -> datetime:
    """Stores what buscar_disponibilidad returned; offering a slot again refreshes it."""
    cursor = await conn.execute("select now()")
    row = await cursor.fetchone()
    assert row is not None  # noqa: S101 - select now() always returns a row
    for slot in slots:
        await conn.execute(
            """
            insert into public.offered_slots
              (clinic_id, conversation_id, service_id, provider_id, slot_key, starts_at, label)
            values (%s, %s, %s, %s, %s, %s, %s)
            on conflict (clinic_id, conversation_id, service_id, slot_key) do update
              set provider_id = excluded.provider_id, starts_at = excluded.starts_at,
                  label = excluded.label, offered_at = now()
            """,
            (
                clinic_id,
                conversation_id,
                slot.service_id,
                slot.provider_id,
                slot.key,
                slot.starts_at,
                slot.label,
            ),
        )
    offered_at: datetime = row[0]
    return offered_at


# Escalations and team alerts --------------------------------------------------------------------


async def insert_escalation(  # noqa: PLR0913 - one value per column
    conn: AsyncConnection,
    *,
    clinic_id: UUID,
    conversation_id: UUID,
    reason: str,
    priority: str,
    summary: str,
) -> UUID:
    cursor = await conn.execute(
        """
        insert into public.escalations (clinic_id, conversation_id, reason, priority, summary)
        values (%s, %s, %s, %s, %s)
        returning id
        """,
        (clinic_id, conversation_id, reason, priority, summary),
    )
    row = await cursor.fetchone()
    assert row is not None  # noqa: S101 - insert ... returning always returns a row
    escalation_id: UUID = row[0]
    return escalation_id


async def open_escalation_since(
    conn: AsyncConnection, conversation_id: UUID, priority: str, since: datetime
) -> bool:
    """Whether the team already has an open escalation of this priority for the conversation."""
    cursor = await conn.execute(
        """
        select exists (
          select 1 from public.escalations
          where conversation_id = %s and priority = %s and status = 'open' and created_at >= %s
        )
        """,
        (conversation_id, priority, since),
    )
    row = await cursor.fetchone()
    return bool(row and row[0])


async def queue_team_alerts(conn: AsyncConnection, clinic_id: UUID, escalation_id: UUID) -> int:
    """One pending alert per team member with a phone for alerts; a retry adds nothing."""
    cursor = await conn.execute(
        """
        insert into public.team_alerts (clinic_id, escalation_id, user_id)
        select %s, %s, u.user_id from public.clinic_users u where u.alert_phone is not null
        on conflict (clinic_id, escalation_id, user_id) do nothing
        """,
        (clinic_id, escalation_id),
    )
    return cursor.rowcount


@dataclass(frozen=True, slots=True)
class PendingAlert:
    id: UUID
    member_name: str | None
    phone: str
    priority: str
    conversation_id: UUID
    clinic_name: str


async def claim_team_alert(conn: AsyncConnection, escalation_id: UUID) -> PendingAlert | None:
    """The next alert of the escalation not sent yet, marked as on its way before Meta is called:
    if the answer is lost it is never sent twice."""
    cursor = await conn.execute(
        """
        update public.team_alerts t set status = 'unknown', send_started_at = now()
        from public.clinic_users u, public.escalations e, public.clinics c
        where t.id = (
                select id from public.team_alerts
                where escalation_id = %s and status = 'pending'
                order by created_at, id limit 1 for update skip locked
              )
          and u.clinic_id = t.clinic_id and u.user_id = t.user_id
          and e.id = t.escalation_id and c.id = t.clinic_id
          and u.alert_phone is not null
        returning t.id, u.display_name, u.alert_phone, e.priority, e.conversation_id, c.name
        """,
        (escalation_id,),
    )
    row = await cursor.fetchone()
    return PendingAlert(*row) if row else None


async def finish_team_alert(
    conn: AsyncConnection, alert_id: UUID, *, meta_message_id: str | None, error_code: int | None
) -> None:
    """sent with Meta's id; failed with its error code; with neither it stays unknown."""
    status = "sent" if meta_message_id else ("failed" if error_code is not None else "unknown")
    await conn.execute(
        "update public.team_alerts set status = %s, meta_message_id = %s, error_code = %s where id = %s",
        (status, meta_message_id, error_code, alert_id),
    )


async def release_team_alert(conn: AsyncConnection, alert_id: UUID) -> None:
    """Meta never received it (no connection, a "not now" code): it can be sent again."""
    await conn.execute(
        "update public.team_alerts set status = 'pending', send_started_at = null where id = %s",
        (alert_id,),
    )


# Turn log ---------------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class TurnRecord:
    model: str | None
    prompt_version: str
    outcome: str
    error_kind: str | None
    model_calls: int
    tools: tuple[str, ...]
    input_tokens: int
    output_tokens: int
    cache_read_tokens: int
    cache_write_tokens: int
    latency_ms: int
    cost_microusd: int


async def insert_bot_turn(
    conn: AsyncConnection, clinic_id: UUID, conversation_id: UUID, record: TurnRecord
) -> None:
    values: dict[str, Any] = {
        "clinic_id": clinic_id,
        "conversation_id": conversation_id,
        "model": record.model,
        "prompt_version": record.prompt_version,
        "outcome": record.outcome,
        "error_kind": record.error_kind,
        "model_calls": record.model_calls,
        "tools": list(record.tools),
        "input_tokens": record.input_tokens,
        "output_tokens": record.output_tokens,
        "cache_read_tokens": record.cache_read_tokens,
        "cache_write_tokens": record.cache_write_tokens,
        "latency_ms": record.latency_ms,
        "cost_microusd": record.cost_microusd,
    }
    columns = ", ".join(values)
    placeholders = ", ".join(f"%({name})s" for name in values)
    await conn.execute(
        f"insert into public.bot_turns ({columns}) values ({placeholders})",  # noqa: S608 - fixed column names
        values,
    )
