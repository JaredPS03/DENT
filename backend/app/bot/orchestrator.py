"""One bot turn, in the order of bot-ia.md (milestone 1C).

1. Bot off for the clinic, paused in this chat or taken by a person: the message only reaches the
   inbox. If it carries an urgency signal, the team is alerted anyway, without writing to the
   patient (a person is already in charge).
2. Deterministic urgency filter: escalate as urgent, alert the team, send the fixed urgency
   message and stop. Before the privacy notice, so a patient in pain always gets help.
3. Consent gate: Acepto stores the proof and the turn goes on to answer what the patient wrote
   before; No acepto hands the chat to a person; a first message gets the notice; later ones get
   general answers from the AI with the notice under them. What a patient who never accepted
   wrote is erased 24 h later.
4. The AI with its tools. If anything fails, the fixed fallback message goes out and the chat
   passes to a person with a summary: the patient is never left without an answer.

Every turn that runs the bot leaves a row in bot_turns. No transaction stays open while the model
or Meta are called.
"""

import logging
import time
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Literal
from urllib.parse import urlsplit
from uuid import UUID

from app.bot import fixed
from app.bot.agent import AgentResult, run_agent
from app.bot.costs import estimate_microusd
from app.bot.fixed import Voice
from app.bot.history import build_messages
from app.bot.prompt import PatientContext, system_blocks
from app.bot.reply import CONSENT_ACCEPT_ID, CONSENT_BUTTONS, CONSENT_DECLINE_ID, OfferedSlot
from app.bot.tools import Escalation, Tools, TurnState, escalate
from app.bot.urgency import detect_urgency
from app.db import bot as data
from app.db import conversations
from app.db.conversations import PendingMessage
from app.jobs.context import LeaseLostError, WorkerContext
from app.jobs.queue import EraseUnconsentedJob, enqueue
from app.whatsapp import outbound

logger = logging.getLogger(__name__)

# The AI sees the last messages of the last day at most: never the whole history (seguridad.md).
HISTORY_LIMIT = 20
HISTORY_WINDOW = timedelta(hours=24)
# A slot offered longer ago than this can no longer be booked from its button.
OFFER_WINDOW = timedelta(hours=24)
CONSENT_WINDOW = timedelta(hours=24)
# While the team has an open urgent escalation for the chat, more urgent messages do not alert
# again: the person who answers already sees them.
URGENT_REPEAT_WINDOW = timedelta(hours=2)

KeepAlive = Callable[[], Awaitable[None]]
Outcome = Literal["replied", "escalated", "urgent", "consent_notice", "fallback"]


@dataclass(frozen=True, slots=True)
class BotReply:
    content: str
    payload: dict[str, Any]
    prompt_version: str | None


async def take_turn(  # noqa: PLR0911, PLR0912, PLR0913 - the steps of one turn, each with its own exit
    ctx: WorkerContext,
    *,
    clinic_id: UUID,
    conversation_id: UUID,
    lease_token: UUID,
    batch: Sequence[PendingMessage],
    now: datetime,
    renew: KeepAlive,
    final_attempt: bool = False,
) -> bool:
    """Takes care of the patient's batch. True if a reply was queued. On the job's last attempt a
    failure inside the AI step ends in the fallback message instead of another retry."""
    started = time.monotonic()
    async with ctx.db.clinic_transaction(clinic_id) as conn:
        conversation = await conversations.conversation_for_update(conn, conversation_id)
        if conversation is None:
            return False
        clinic = await data.clinic_profile(conn)
        profile = await data.patient_profile(conn, conversation.patient_id, clinic.notice_version)
        requested_at = await data.consent_requested_at(conn, conversation_id)
    voice = Voice(clinic.name, clinic.doctor, clinic.formal)
    signal = detect_urgency(_burst_text(batch), clinic.urgency_signals)
    paused = conversation.bot_paused_until is not None and conversation.bot_paused_until > now
    bot_on = clinic.bot_enabled and conversation.status == "bot" and not paused

    async def finish(
        reply: BotReply | None, outcome: Outcome | None, result: AgentResult | None = None
    ) -> bool:
        return await _finish(
            ctx,
            clinic_id=clinic_id,
            conversation_id=conversation_id,
            lease_token=lease_token,
            batch=batch,
            reply=reply,
            outcome=outcome,
            result=result,
            latency_ms=round((time.monotonic() - started) * 1000),
        )

    if not bot_on:
        if signal is not None:
            await _alert_only(
                ctx,
                clinic_id=clinic_id,
                conversation_id=conversation_id,
                signal=signal,
                name=profile.name,
                now=now,
            )
        return await finish(None, None)

    if signal is not None:
        # A retried turn finds the escalation it already opened and does not alert twice.
        await _alert_only(
            ctx,
            clinic_id=clinic_id,
            conversation_id=conversation_id,
            signal=signal,
            name=profile.name,
            now=now,
        )
        return await finish(_fixed(fixed.urgency(voice)), "urgent")

    if not clinic.notice_url or not clinic.notice_version:
        # Without a privacy notice the bot cannot ask for consent: the message stays in the inbox.
        logger.error("bot.privacy_notice_missing", extra={"conversation_id": str(conversation_id)})
        return await finish(None, None)

    consent_request: str | None = None
    if not profile.consented:
        gate = await _consent_gate(
            ctx,
            clinic=clinic,
            conversation_id=conversation_id,
            patient_id=conversation.patient_id,
            profile=profile,
            batch=batch,
            requested_at=requested_at,
            now=now,
        )
        if isinstance(gate, BotReply):
            return await finish(gate, "consent_notice")
        profile, consent_request = gate

    if ctx.model is None:
        logger.warning("bot.model_not_configured", extra={"conversation_id": str(conversation_id)})
        return await finish(None, None)

    state, system, messages = await _prepare(
        ctx,
        clinic_id=clinic_id,
        conversation_id=conversation_id,
        patient_id=conversation.patient_id,
        clinic=clinic,
        profile=profile,
        now=now,
        consent_request=consent_request,
    )
    try:
        result = await run_agent(
            ctx.model,
            Tools(ctx.db, ctx.agenda_connector(), state, ctx.clock),
            system=system,
            messages=messages,
            renew=renew,
        )
    except LeaseLostError:
        raise
    except Exception as error:
        if not final_attempt:
            raise  # the worker retries the turn
        # The last attempt: the patient still gets an answer. Without exc_info on purpose: the
        # error's text could quote a tool's data.
        logger.error("bot.turn_crashed", extra={"error": type(error).__name__})  # noqa: TRY400
        result = AgentResult(outcome="fallback", error_kind="internal_error")
    version = ctx.settings.bot_prompt_version
    if result.outcome == "urgent":
        return await finish(_fixed(fixed.urgency(voice)), "urgent", result)
    if result.outcome in {"replied", "escalated"} and state.reply is not None:
        reply = BotReply(state.reply.content, state.reply.payload, version)
        return await finish(reply, result.outcome, result)
    if state.escalation is None:
        state.escalation = await escalate(
            ctx.db,
            clinic_id=clinic_id,
            conversation_id=conversation_id,
            reason="el asistente no pudo contestar",
            priority="normal",
            summary=_fallback_summary(state.profile.name, result.error_kind),
        )
    return await finish(_fixed(fixed.fallback(voice)), "fallback", result)


async def _consent_gate(  # noqa: PLR0913 - what the gate looks at
    ctx: WorkerContext,
    *,
    clinic: data.ClinicProfile,
    conversation_id: UUID,
    patient_id: UUID,
    profile: data.PatientProfile,
    batch: Sequence[PendingMessage],
    requested_at: datetime | None,
    now: datetime,
) -> BotReply | tuple[data.PatientProfile, str | None]:
    """For a patient who has not accepted the notice: the message to send instead of the AI, or
    the profile to go on with and, while they have not accepted, the reminder for the AI's answer."""
    assert clinic.notice_url is not None  # noqa: S101 - checked by the caller
    assert clinic.notice_version is not None  # noqa: S101
    voice = Voice(clinic.name, clinic.doctor, clinic.formal)
    tap = _consent_tap(batch)
    if tap is not None and tap[0] == CONSENT_ACCEPT_ID:
        async with ctx.db.clinic_transaction(clinic.id) as conn:
            await data.accept_consent(conn, clinic.id, patient_id, clinic.notice_version, tap[1])
        return data.PatientProfile(profile.name, consented=True, returning=profile.returning), None
    if tap is not None and tap[0] == CONSENT_DECLINE_ID:
        async with ctx.db.clinic_transaction(clinic.id) as conn:
            await conversations.set_status(conn, conversation_id, "human")
        return _fixed(fixed.consent_declined(voice))
    if requested_at is None or now - requested_at >= CONSENT_WINDOW:
        async with ctx.db.clinic_transaction(clinic.id) as conn:
            await data.mark_consent_requested(conn, conversation_id)
            await enqueue(
                conn,
                EraseUnconsentedJob(clinic_id=clinic.id, conversation_id=conversation_id),
                delay_seconds=int(CONSENT_WINDOW.total_seconds()) + 60,
            )
        notice = fixed.consent_notice(voice, clinic.notice_url)
        return BotReply(notice, outbound.buttons(notice, CONSENT_BUTTONS), None)
    return profile, fixed.consent_reminder(voice, clinic.notice_url)


async def _prepare(  # noqa: PLR0913 - what one prompt is built from
    ctx: WorkerContext,
    *,
    clinic_id: UUID,
    conversation_id: UUID,
    patient_id: UUID,
    clinic: data.ClinicProfile,
    profile: data.PatientProfile,
    now: datetime,
    consent_request: str | None,
) -> tuple[TurnState, list[dict[str, Any]], list[dict[str, Any]]]:
    async with ctx.db.clinic_transaction(clinic_id) as conn:
        services = await data.active_services(conn)
        hours = await data.clinic_hours(conn)
        upcoming = await data.upcoming_appointments(conn, patient_id, now) if profile.consented else []
        offered = await data.offered_slots(conn, conversation_id, since=now - OFFER_WINDOW)
        rows = await data.recent_history(
            conn, conversation_id, since=now - HISTORY_WINDOW, limit=HISTORY_LIMIT
        )
    state = TurnState(
        clinic_id=clinic_id,
        conversation_id=conversation_id,
        patient_id=patient_id,
        clinic=clinic,
        services=services,
        hours=hours,
        profile=profile,
        upcoming=upcoming,
        offered={slot.key: slot for slot in offered},
        turn_started_at=now,
        allowed_link_hosts=_clinic_hosts(clinic),
        consent_request=consent_request,
    )
    slots_by_start = {
        slot.starts_at: OfferedSlot(slot.key, slot.service_id, slot.service_name, slot.starts_at, slot.label)
        for slot in offered
    }
    system = system_blocks(
        ctx.settings.bot_prompt_version,
        clinic=clinic,
        services=services,
        hours=hours,
        now=now,
        patient=PatientContext(profile, upcoming),
    )
    return state, system, build_messages(rows, slots_by_start, clinic.tz)


async def _finish(  # noqa: PLR0913 - the turn's results, written together
    ctx: WorkerContext,
    *,
    clinic_id: UUID,
    conversation_id: UUID,
    lease_token: UUID,
    batch: Sequence[PendingMessage],
    reply: BotReply | None,
    outcome: Outcome | None,
    result: AgentResult | None,
    latency_ms: int,
) -> bool:
    """Queues the reply, marks the batch handled and logs the turn, in one transaction."""
    async with ctx.db.clinic_transaction(clinic_id) as conn:
        if not await conversations.holds_lease(conn, conversation_id, lease_token):
            raise LeaseLostError
        if reply is not None:
            await conversations.insert_outbound(
                conn,
                clinic_id=clinic_id,
                conversation_id=conversation_id,
                sender="bot",
                message_type=reply.payload["type"],
                content=reply.content,
                send_payload=reply.payload,
                billing_category="service",
                delivery_status="pending",
                # One answer per batch, even if this turn is retried.
                idempotency_key=f"reply:{max(batch, key=lambda message: message.created_at).id}",
                prompt_version=reply.prompt_version,
            )
        await conversations.mark_handled(conn, [message.id for message in batch])
        if outcome is not None:
            usage = result.usage if result else None
            await data.insert_bot_turn(
                conn,
                clinic_id,
                conversation_id,
                data.TurnRecord(
                    model=ctx.model.name if ctx.model and result and result.model_calls else None,
                    prompt_version=ctx.settings.bot_prompt_version,
                    outcome=outcome,
                    error_kind=result.error_kind if result else None,
                    model_calls=result.model_calls if result else 0,
                    tools=tuple(result.tools) if result else (),
                    input_tokens=usage.input_tokens if usage else 0,
                    output_tokens=usage.output_tokens if usage else 0,
                    cache_read_tokens=usage.cache_read_tokens if usage else 0,
                    cache_write_tokens=usage.cache_write_tokens if usage else 0,
                    latency_ms=latency_ms,
                    cost_microusd=estimate_microusd(ctx.model.name, usage) if ctx.model and usage else 0,
                ),
            )
    logger.info(
        "bot.turn_done",
        extra={
            "conversation_id": str(conversation_id),
            "messages": len(batch),
            "outcome": outcome or "inbox",
            "model_calls": result.model_calls if result else 0,
            "tools": result.tools if result else [],
            "error_kind": result.error_kind if result else None,
            "latency_ms": latency_ms,
            "prompt_version": ctx.settings.bot_prompt_version,
        },
    )
    return reply is not None


async def _alert_only(  # noqa: PLR0913 - what the alert needs
    ctx: WorkerContext,
    *,
    clinic_id: UUID,
    conversation_id: UUID,
    signal: str,
    name: str | None,
    now: datetime,
) -> Escalation | None:
    async with ctx.db.clinic_transaction(clinic_id) as conn:
        if await data.open_escalation_since(conn, conversation_id, "urgent", now - URGENT_REPEAT_WINDOW):
            return None
    return await escalate(
        ctx.db,
        clinic_id=clinic_id,
        conversation_id=conversation_id,
        reason=signal,
        priority="urgent",
        summary=_urgent_summary(name, signal),
    )


def _burst_text(batch: Sequence[PendingMessage]) -> str:
    return "\n".join(part for message in batch for part in (message.content, message.transcription) if part)


def _consent_tap(batch: Sequence[PendingMessage]) -> tuple[str, UUID] | None:
    """The patient's last answer to the notice in this batch, with the message that carries it."""
    for message in reversed(batch):
        if message.reply_id in {CONSENT_ACCEPT_ID, CONSENT_DECLINE_ID}:
            return message.reply_id, message.id
    return None


def _fixed(text: str) -> BotReply:
    return BotReply(text, outbound.text(text), None)


def _clinic_hosts(clinic: data.ClinicProfile) -> frozenset[str]:
    hosts = {(urlsplit(url).hostname or "").lower() for url in (clinic.maps_url, clinic.notice_url) if url}
    return frozenset(host for host in hosts if host)


def _patient_label(name: str | None) -> str:
    first = name.split()[0] if name and name.split() else None
    return first or "sin nombre registrado"


def _urgent_summary(name: str | None, signal: str) -> str:
    return (
        f"Paciente: {_patient_label(name)}. Quiere: atención urgente ({signal}). "
        "Intentado: el filtro de urgencia lo pasó al equipo de inmediato."
    )


def _fallback_summary(name: str | None, error_kind: str | None) -> str:
    return (
        f"Paciente: {_patient_label(name)}. Quiere: no se pudo saber, el asistente falló "
        f"({error_kind or 'sin detalle'}). Intentado: se le avisó que el equipo le responderá."
    )
