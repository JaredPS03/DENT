"""One bot turn, in the order of bot-ia.md (milestone 1C).

1. Bot off for the clinic, paused in this chat or taken by a person: the message only reaches the
   inbox. If it carries an urgency signal, the team is alerted anyway, without writing to the
   patient (a person is already in charge).
2. Deterministic urgency filter: escalate as urgent, alert the team, send the fixed urgency
   message and stop. Before the privacy notice, so a patient in pain always gets help. A voice
   note nobody could transcribe might be an urgency too: it goes to the team, also before the
   notice.
3. Consent gate: Acepto stores the proof (first of all, even if a person has the chat) and the
   turn goes on to answer what the patient wrote before; No acepto hands the chat to the team;
   a first message gets the notice; later ones get general answers from the AI with the notice
   under them. What a patient who never accepted wrote is erased 24 h later.
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
from app.bot.fixed import Voice, patient_label
from app.bot.history import build_messages
from app.bot.prompt import PatientContext, system_blocks
from app.bot.reminder_taps import handle_taps
from app.bot.reply import (
    CONSENT_DECLINE_ID,
    ConsentRequest,
    OfferedSlot,
    consent_accept_id,
    consent_buttons,
)
from app.bot.tools import Tools, TurnState, escalate
from app.bot.urgency import active_signals, detect_urgency, is_breathing_signal
from app.db import bot as data
from app.db import conversations
from app.db.conversations import PendingMessage
from app.jobs.context import LeaseLostError, WorkerContext
from app.jobs.queue import EraseUnconsentedJob, enqueue, erase_scheduled
from app.whatsapp import outbound

logger = logging.getLogger(__name__)

# The AI sees the last messages of the last day at most: never the whole history (seguridad.md).
HISTORY_LIMIT = 20
HISTORY_WINDOW = timedelta(hours=24)
# A slot offered longer ago than this can no longer be booked from its button.
OFFER_WINDOW = timedelta(hours=24)
CONSENT_WINDOW = timedelta(hours=24)
# While the team has an open urgent escalation for the chat, more urgent messages with the same
# signal do not alert again: the person who answers already sees them. Trouble breathing or
# swallowing alerts again after a few minutes.
URGENT_REPEAT_WINDOW = timedelta(hours=2)
BREATHING_REPEAT_WINDOW = timedelta(minutes=10)
# The slots already shown that the AI sees, so the patient can choose one by words or audio.
MAX_SHOWN_SLOTS = 10

KeepAlive = Callable[[], Awaitable[None]]
Outcome = Literal["replied", "escalated", "urgent", "consent_notice", "fallback"]


@dataclass(frozen=True, slots=True)
class BotReply:
    content: str
    payload: dict[str, Any]
    prompt_version: str | None = None
    # Slots (key, service) the reply shows: from the next turn on, the patient may choose them.
    slot_keys: tuple[tuple[str, UUID], ...] = ()
    # The chat passes to the team with this reply.
    hand_over: bool = False


async def take_turn(  # noqa: PLR0911, PLR0912, PLR0913, PLR0915 - the steps of one turn, each with its own exit
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
    turn_key = f"turn:{max(batch, key=lambda message: message.created_at).id}"
    async with ctx.db.clinic_transaction(clinic_id) as conn:
        conversation = await conversations.conversation_for_update(conn, conversation_id)
        if conversation is None:
            return False
        clinic = await data.clinic_profile(conn)
        profile = await data.patient_profile(conn, conversation.patient_id, clinic.notice_version)
        requested_at = await data.consent_requested_at(conn, conversation_id)
    voice = Voice(clinic.name, clinic.doctor, clinic.formal)
    profile = await _record_acepto(ctx, clinic, conversation.patient_id, profile, batch)
    signal = detect_urgency(_burst_text(batch), active_signals(clinic.urgency_signals))
    paused = conversation.bot_paused_until is not None and conversation.bot_paused_until > now
    bot_on = clinic.bot_enabled and conversation.status == "bot" and not paused
    # Whatever way the turn ends, what a patient who has not accepted wrote is erased after 24 h,
    # but only if the bot answers this chat or showed the notice in the last 24 h: with the bot
    # off, or without a notice to show, the clinic keeps its inbox as it always did.
    noticed = requested_at is not None and now - requested_at < CONSENT_WINDOW
    has_notice = bool(clinic.notice_url and clinic.notice_version)
    erase_later = not profile.consented and has_notice and (bot_on or noticed)

    async def finish(
        reply: BotReply | None,
        outcome: Outcome | None,
        result: AgentResult | None = None,
        *,
        hand_over: bool = False,
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
            hand_over=hand_over,
            erase_later=erase_later,
            latency_ms=round((time.monotonic() - started) * 1000),
        )

    # Buttons of the reminder: code, before the AI and the consent gate (they only touch the
    # appointment). While the team has the chat or an urgency is answered, only the appointment
    # changes and nothing is written to the patient.
    taps = await handle_taps(
        ctx,
        clinic_id=clinic_id,
        conversation_id=conversation_id,
        batch=batch,
        now=now,
        voice=voice,
        tz=clinic.tz,
        answering=bot_on and signal is None,
    )
    if taps is not None and taps.only_taps and taps.reply is not None:
        tapped = taps.reply
        return await finish(BotReply(tapped.content, dict(tapped.payload), None, tapped.slot_keys), None)

    if not bot_on:
        alerted = False
        if signal is not None:
            alerted = await _alert_only(
                ctx,
                clinic_id=clinic_id,
                conversation_id=conversation_id,
                signal=signal,
                name=profile.name,
                now=now,
                turn_key=turn_key,
                erasing=erase_later,
            )
        return await finish(None, None, hand_over=alerted)

    if signal is not None:
        # Once per turn: a retried turn finds this escalation and does not alert twice.
        await escalate(
            ctx.db,
            clinic_id=clinic_id,
            conversation_id=conversation_id,
            reason=signal,
            priority="urgent",
            summary=_urgent_summary(profile.name, signal, answered=True, erasing=erase_later),
            turn_key=turn_key,
        )
        return await finish(_fixed(fixed.urgency(voice)), "urgent", hand_over=True)

    if _unheard_voice_note(batch):
        # Nobody could read it, and it might be an urgency: like one, it goes to a person before
        # the privacy notice (bot rule 16).
        await escalate(
            ctx.db,
            clinic_id=clinic_id,
            conversation_id=conversation_id,
            reason="nota de voz sin transcribir",
            priority="normal",
            summary=_unheard_summary(profile.name, erasing=erase_later),
            turn_key=turn_key,
        )
        return await finish(_fixed(fixed.voice_note_unheard(voice)), "escalated", hand_over=True)

    if not clinic.notice_url or not clinic.notice_version:
        # Without a privacy notice the bot cannot ask for consent: the message stays in the inbox.
        logger.error("bot.privacy_notice_missing", extra={"conversation_id": str(conversation_id)})
        return await finish(None, None)

    consent_request: ConsentRequest | None = None
    if not profile.consented:
        gate = await _consent_gate(
            ctx,
            clinic=clinic,
            conversation_id=conversation_id,
            profile=profile,
            batch=batch,
            requested_at=requested_at,
            now=now,
            turn_key=turn_key,
        )
        if isinstance(gate, BotReply):
            return await finish(gate, "consent_notice", hand_over=gate.hand_over)
        profile, consent_request = gate

    if ctx.model is None:
        logger.warning("bot.model_not_configured", extra={"conversation_id": str(conversation_id)})
        return await finish(None, None)

    state: TurnState | None = None
    try:
        state, system, messages = await _prepare(
            ctx,
            clinic_id=clinic_id,
            conversation_id=conversation_id,
            patient_id=conversation.patient_id,
            clinic=clinic,
            profile=profile,
            now=now,
            consent_request=consent_request,
            turn_key=turn_key,
        )
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
    escalated = state is not None and state.escalation is not None
    if result.outcome == "urgent":
        return await finish(_fixed(fixed.urgency(voice)), "urgent", result, hand_over=True)
    if state is not None and result.outcome in {"replied", "escalated"} and state.reply is not None:
        reply = BotReply(state.reply.content, state.reply.payload, version, state.reply.slot_keys)
        return await finish(reply, result.outcome, result, hand_over=escalated)
    if not escalated:
        await escalate(
            ctx.db,
            clinic_id=clinic_id,
            conversation_id=conversation_id,
            reason="el asistente no pudo contestar",
            priority="normal",
            summary=_fallback_summary(profile.name),
            turn_key=turn_key,
        )
    return await finish(_fixed(fixed.fallback(voice)), "fallback", result, hand_over=True)


async def _record_acepto(
    ctx: WorkerContext,
    clinic: data.ClinicProfile,
    patient_id: UUID,
    profile: data.PatientProfile,
    batch: Sequence[PendingMessage],
) -> data.PatientProfile:
    """Acepto on the current notice counts even if a person has the chat, the bot is off or the
    turn ends early: the consent is the patient's, and what they wrote must not be erased after
    they gave it."""
    if profile.consented or not clinic.notice_version:
        return profile
    tap = _consent_tap(batch, clinic.notice_version)
    if tap is None or tap[0] != consent_accept_id(clinic.notice_version):
        return profile
    async with ctx.db.clinic_transaction(clinic.id) as conn:
        await data.accept_consent(conn, clinic.id, patient_id, clinic.notice_version, tap[1])
    return data.PatientProfile(profile.name, consented=True, returning=profile.returning)


async def _consent_gate(  # noqa: PLR0913 - what the gate looks at
    ctx: WorkerContext,
    *,
    clinic: data.ClinicProfile,
    conversation_id: UUID,
    profile: data.PatientProfile,
    batch: Sequence[PendingMessage],
    requested_at: datetime | None,
    now: datetime,
    turn_key: str,
) -> BotReply | tuple[data.PatientProfile, ConsentRequest | None]:
    """For a patient who has not accepted the notice: the message to send instead of the AI, or
    the profile to go on with and, while they have not accepted, the notice for the AI's answer."""
    assert clinic.notice_url is not None  # noqa: S101 - checked by the caller
    assert clinic.notice_version is not None  # noqa: S101
    voice = Voice(clinic.name, clinic.doctor, clinic.formal)
    tap = _consent_tap(batch, clinic.notice_version)
    if tap is not None and tap[0] == CONSENT_DECLINE_ID:
        # The message promises that someone answers: the team is alerted, without details.
        await escalate(
            ctx.db,
            clinic_id=clinic.id,
            conversation_id=conversation_id,
            reason="no aceptó el aviso de privacidad",
            priority="normal",
            summary=_declined_summary(profile.name),
            turn_key=turn_key,
        )
        text = fixed.consent_declined(voice)
        return BotReply(text, outbound.text(text), hand_over=True)
    if requested_at is None or now - requested_at >= CONSENT_WINDOW:
        async with ctx.db.clinic_transaction(clinic.id) as conn:
            await data.mark_consent_requested(conn, conversation_id)
        notice = fixed.consent_notice(voice, clinic.notice_url)
        return BotReply(notice, outbound.buttons(notice, consent_buttons(clinic.notice_version)))
    return profile, ConsentRequest(fixed.consent_reminder(voice, clinic.notice_url), clinic.notice_version)


async def _prepare(  # noqa: PLR0913 - what one prompt is built from
    ctx: WorkerContext,
    *,
    clinic_id: UUID,
    conversation_id: UUID,
    patient_id: UUID,
    clinic: data.ClinicProfile,
    profile: data.PatientProfile,
    now: datetime,
    consent_request: ConsentRequest | None,
    turn_key: str,
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
        turn_key=turn_key,
        allowed_link_hosts=_clinic_hosts(clinic),
        consent_request=consent_request,
    )
    slots_by_start = {slot.starts_at: _offered(slot) for slot in offered}
    shown = [_offered(slot) for slot in offered if slot.shown_at is not None][-MAX_SHOWN_SLOTS:]
    system = system_blocks(
        ctx.settings.bot_prompt_version,
        clinic=clinic,
        services=services,
        hours=hours,
        now=now,
        patient=PatientContext(profile, upcoming),
    )
    return state, system, build_messages(rows, slots_by_start, clinic.tz, shown=shown)


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
    hand_over: bool,
    erase_later: bool,
    latency_ms: int,
) -> bool:
    """Queues the reply, marks the slots it shows, hands the chat to a person if the turn
    escalated, schedules the erasure of what a patient without consent wrote, marks the batch
    handled and logs the turn, all in one transaction: a turn that fails before this point is
    retried as if it never happened."""
    async with ctx.db.clinic_transaction(clinic_id) as conn:
        if not await conversations.holds_lease(conn, conversation_id, lease_token):
            raise LeaseLostError
        if reply is not None:
            message = await conversations.insert_outbound(
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
            if reply.slot_keys:
                await data.mark_slots_shown(conn, conversation_id, message.id, reply.slot_keys)
        if hand_over:
            await conversations.set_status(conn, conversation_id, "human")
        if erase_later and not await erase_scheduled(conn, conversation_id):
            # Also when the turn ends early (urgency, voice note, chat with the team): the job
            # erases what is older than 24 h and comes back for the rest. One chain per chat.
            await enqueue(
                conn,
                EraseUnconsentedJob(clinic_id=clinic_id, conversation_id=conversation_id),
                delay_seconds=int(CONSENT_WINDOW.total_seconds()) + 60,
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
    turn_key: str,
    erasing: bool,
) -> bool:
    """Alerts the team about an urgency in a chat the bot does not answer. While the same signal
    is still open from less than 2 h ago it does not alert again; a new signal always does, and
    trouble breathing or swallowing does again after 10 minutes. True if an escalation was opened."""
    window = BREATHING_REPEAT_WINDOW if is_breathing_signal(signal) else URGENT_REPEAT_WINDOW
    async with ctx.db.clinic_transaction(clinic_id) as conn:
        if await data.open_escalation_since(conn, conversation_id, "urgent", now - window, reason=signal):
            return False
    await escalate(
        ctx.db,
        clinic_id=clinic_id,
        conversation_id=conversation_id,
        reason=signal,
        priority="urgent",
        summary=_urgent_summary(name, signal, answered=False, erasing=erasing),
        turn_key=turn_key,
    )
    return True


def _burst_text(batch: Sequence[PendingMessage]) -> str:
    return "\n".join(part for message in batch for part in (message.content, message.transcription) if part)


def _unheard_voice_note(batch: Sequence[PendingMessage]) -> bool:
    return any(message.type == "audio" and not message.transcription for message in batch)


def _consent_tap(batch: Sequence[PendingMessage], version: str) -> tuple[str, UUID] | None:
    """The patient's last answer to the current notice in this batch, with the message that
    carries it. A tap on an older notice does not count."""
    answers = {consent_accept_id(version), CONSENT_DECLINE_ID}
    for message in reversed(batch):
        if message.reply_id in answers:
            return message.reply_id, message.id
    return None


def _offered(slot: data.StoredSlot) -> OfferedSlot:
    return OfferedSlot(slot.key, slot.service_id, slot.service_name, slot.starts_at, slot.label)


def _fixed(text: str) -> BotReply:
    return BotReply(text, outbound.text(text))


def _clinic_hosts(clinic: data.ClinicProfile) -> frozenset[str]:
    hosts = {(urlsplit(url).hostname or "").lower() for url in (clinic.maps_url, clinic.notice_url) if url}
    return frozenset(host for host in hosts if host)


_ERASED_SOON = " No ha aceptado el aviso de privacidad: lo que escribió se borra a las 24 h."


def _urgent_summary(name: str | None, signal: str, *, answered: bool, erasing: bool) -> str:
    wants = f"Paciente: {patient_label(name)}. Quiere: atención por una posible urgencia ({signal})."
    if answered:
        tried = "Intentado: se le mandó el mensaje de urgencia, con el 911 si le cuesta respirar o tragar."
    else:
        tried = (
            "Intentado: nada; el chat estaba en pausa o con el equipo y no se le contestó. "
            "Llámale o escríbele ya."
        )
    return f"{wants} {tried}{_ERASED_SOON if erasing else ''}"


def _fallback_summary(name: str | None) -> str:
    return (
        f"Paciente: {patient_label(name)}. Quiere: no se sabe; el asistente no pudo contestar su "
        "último mensaje por una falla técnica. Intentado: se le dijo que el equipo le responderá. "
        "Revisa la conversación y contéstale."
    )


def _unheard_summary(name: str | None, *, erasing: bool) -> str:
    summary = (
        f"Paciente: {patient_label(name)}. Quiere: no se sabe; mandó una nota de voz que no se pudo "
        "transcribir. Intentado: se le dijo que el equipo la escuchará; escúchala en la bandeja y "
        "contéstale."
    )
    if erasing:
        summary += (
            " No ha aceptado el aviso de privacidad: la nota se borra a las 24 h de enviada; escúchala antes."
        )
    return summary


def _declined_summary(name: str | None) -> str:
    return (
        f"Paciente: {patient_label(name)}. Quiere: no se sabe; tocó «No acepto» en el aviso de "
        "privacidad. Intentado: se le dijo que el asistente ya no le escribirá y que alguien del "
        "equipo le responderá por aquí. Contéstale sin pedirle datos de salud por WhatsApp; lo que "
        "escriba se borra a las 24 h de enviado."
    )
