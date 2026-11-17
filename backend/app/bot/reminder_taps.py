"""The buttons of recordatorio_cita (milestone 1D): Confirmo, Cambiar and Cancelar, and the slots
offered after Cambiar. Code, not the AI: the patient's choice is already structured, so it is
applied the same way every time, with or without a model, and tested without spending.

A tap on a template button comes back from Meta with the button's label only. It is tied to its
reminder by the message it answers (context id), and only within the patient's own conversation.

Confirmo and Cancelar touch nothing but the appointment, so they work before the privacy notice
is accepted and also while the team has the chat; then nothing is answered (Fase 1: the bot does
not write while a person attends the chat), and Cambiar and the slots stay for the team.
"""

import logging
import unicodedata
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Literal
from uuid import UUID
from zoneinfo import ZoneInfo

from app.bot import fixed
from app.bot.fixed import Voice
from app.bot.prompt import at_time, spoken_day
from app.db import bot as data
from app.db import reminders as db
from app.db.conversations import PendingMessage
from app.jobs.context import WorkerContext
from app.scheduling.availability import Slot
from app.scheduling.connector import Actor, AgendaError, SlotUnavailableError
from app.whatsapp import outbound
from app.whatsapp.outbound import Option
from app.whatsapp.templates import REMINDER_BUTTONS

logger = logging.getLogger(__name__)

# Reply ids of the slots offered after "Cambiar": mover:<reminder id>:<slot key>.
MOVE_PREFIX = "mover:"
# A tap whose template's Meta id is unknown is tied to the latest reminder sent this long ago.
TAP_LOOKBACK = timedelta(hours=48)
# "Cambiar" offers one free slot a day, the first days that have one in this window.
OPTIONS_WINDOW = timedelta(days=7)
MAX_OPTIONS = 3
SOURCE = "reminder"
CANCEL_REASON = "Lo canceló el paciente con el botón del recordatorio"
BOT = Actor(type="bot")

Action = Literal["confirm", "change", "cancel", "move"]


@dataclass(frozen=True, slots=True)
class Tap:
    message: PendingMessage
    action: Action
    # Only for "move": the reminder behind the slot and the slot's key.
    reminder_id: UUID | None = None
    slot_key: str | None = None


@dataclass(frozen=True, slots=True)
class TapReply:
    content: str
    payload: dict[str, object]
    # Slots (key, service) the reply shows, so they can be chosen from the next turn on.
    slot_keys: tuple[tuple[str, UUID], ...] = ()


@dataclass(frozen=True, slots=True)
class TapResult:
    # The answer to the last tap; None when nothing is answered or no tap was a reminder's.
    reply: TapReply | None
    # Whether the batch held nothing but these taps: then the code's answer is the whole turn.
    only_taps: bool


def read_taps(batch: Sequence[PendingMessage]) -> list[Tap]:
    taps: list[Tap] = []
    for message in batch:
        if message.type == "button":
            action = REMINDER_BUTTONS.get(_label(message.reply_id or message.content))
            if action is not None:
                taps.append(Tap(message, action))  # type: ignore[arg-type]
        elif message.type == "interactive" and message.reply_id and message.reply_id.startswith(MOVE_PREFIX):
            parsed = _move_id(message.reply_id)
            if parsed is not None:
                taps.append(Tap(message, "move", *parsed))
    return taps


async def handle_taps(  # noqa: PLR0913 - what a tap needs
    ctx: WorkerContext,
    *,
    clinic_id: UUID,
    conversation_id: UUID,
    batch: Sequence[PendingMessage],
    now: datetime,
    voice: Voice,
    tz: ZoneInfo,
    answering: bool,
) -> TapResult | None:
    """Applies the reminder taps of the batch, in order. None if there are none."""
    taps = read_taps(batch)
    if not taps:
        return None
    turn = _Turn(ctx, clinic_id, conversation_id, now, voice, tz)
    reply: TapReply | None = None
    for tap in taps:
        if not answering and tap.action in {"change", "move"}:
            continue
        answer = await _apply(turn, tap)
        if answer is not None:
            reply = answer
    return TapResult(reply if answering else None, only_taps=len(taps) == len(batch))


def when_phrase(moment: datetime, now: datetime, tz: ZoneInfo) -> str:
    """'mañana martes 18 de noviembre a las 10:00 am', 'hoy a la 1:00 pm' or 'el jueves 20 de
    noviembre a las 9:00 am'."""
    local = moment.astimezone(tz)
    today = now.astimezone(tz).date()
    clock = at_time(local.time())
    if local.date() == today:
        return f"hoy {clock}"
    if local.date() == today + timedelta(days=1):
        return f"mañana {spoken_day(local)} {clock}"
    return f"el {spoken_day(local)} {clock}"


def first_per_day(slots: Sequence[Slot], tz: ZoneInfo, limit: int) -> list[Slot]:
    """The first slot of each day, for the first `limit` days that have one."""
    chosen: list[Slot] = []
    days: set[date] = set()
    for slot in slots:
        day = slot.starts_at.astimezone(tz).date()
        if day in days:
            continue
        days.add(day)
        chosen.append(slot)
        if len(chosen) == limit:
            break
    return chosen


# Internals ----------------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class _Turn:
    ctx: WorkerContext
    clinic_id: UUID
    conversation_id: UUID
    now: datetime
    voice: Voice
    tz: ZoneInfo
    # Appointments a tap of this same batch cancelled ("Cancelar" twice in one burst).
    cancelled: set[UUID] = field(default_factory=set)

    def when(self, moment: datetime) -> str:
        return when_phrase(moment, self.now, self.tz)


async def _apply(turn: _Turn, tap: Tap) -> TapReply | None:  # noqa: PLR0911 - one exit per state
    async with turn.ctx.db.clinic_transaction(turn.clinic_id) as conn:
        if tap.action == "move":
            assert tap.reminder_id is not None  # noqa: S101 - set by read_taps for "move"
            reminder = await db.reminder_in_conversation(conn, turn.conversation_id, tap.reminder_id)
        else:
            reminder = await db.reminder_answered(
                conn, turn.conversation_id, tap.message.context_meta_message_id, turn.now - TAP_LOOKBACK
            )
    if reminder is None:
        # Not a reminder's button after all (another template with a button of the same name):
        # the batch goes on to the AI like any message.
        logger.info("reminders.tap_without_reminder", extra={"message_id": str(tap.message.id)})
        return None

    voice, service = turn.voice, reminder.service_name
    if reminder.status == "cancelled":
        if tap.action == "cancel" and reminder.appointment_id in turn.cancelled:
            return _text(fixed.reminder_cancelled(voice, service, turn.when(reminder.starts_at)))
        return _text(fixed.reminder_already_cancelled(voice))
    if reminder.status not in {"pending", "confirmed"} or reminder.starts_at <= turn.now:
        return _text(fixed.reminder_already_past(voice))
    if tap.action == "move":
        return await _move(turn, reminder, tap)
    if reminder.starts_at != reminder.reminded_at:
        return _text(fixed.reminder_already_moved(voice, turn.when(reminder.starts_at)))
    if tap.action == "change":
        return await _options(turn, reminder)

    agenda = turn.ctx.agenda_connector()
    try:
        if tap.action == "confirm":
            await agenda.confirm(turn.clinic_id, reminder.appointment_id, actor=BOT, source=SOURCE)
            reply = fixed.reminder_confirmed(voice, service, turn.when(reminder.starts_at))
        else:
            await agenda.cancel(
                turn.clinic_id, reminder.appointment_id, actor=BOT, reason=CANCEL_REASON, source=SOURCE
            )
            turn.cancelled.add(reminder.appointment_id)
            reply = fixed.reminder_cancelled(voice, service, turn.when(reminder.starts_at))
    except AgendaError:
        # Changed in the same instant by someone else: the AI or the team takes it from the history.
        logger.warning("reminders.tap_refused", extra={"appointment_id": str(reminder.appointment_id)})
        return None
    _log(tap, reminder)
    return _text(reply)


async def _options(turn: _Turn, reminder: db.TappedReminder) -> TapReply:
    """Cambiar: free slots of the same service, one a day, as buttons. The appointment stays."""
    slots = await turn.ctx.agenda_connector().find_slots(
        turn.clinic_id, reminder.service_id, turn.now, turn.now + OPTIONS_WINDOW, actor=BOT
    )
    chosen = first_per_day([s for s in slots if s.starts_at != reminder.starts_at], turn.tz, MAX_OPTIONS)
    if not chosen:
        return _text(fixed.reminder_no_options(turn.voice))
    return await _offer(turn, reminder, chosen, fixed.reminder_options(turn.voice, reminder.service_name))


async def _move(turn: _Turn, reminder: db.TappedReminder, tap: Tap) -> TapReply | None:
    assert tap.slot_key is not None  # noqa: S101 - set by read_taps for "move"
    async with turn.ctx.db.clinic_transaction(turn.clinic_id) as conn:
        slot = await db.shown_slot(conn, turn.conversation_id, tap.slot_key, reminder.service_id)
    if slot is None:
        logger.warning("reminders.unknown_slot", extra={"message_id": str(tap.message.id)})
        return None
    if reminder.starts_at != slot.starts_at:
        try:
            await turn.ctx.agenda_connector().move(
                turn.clinic_id,
                reminder.appointment_id,
                slot.starts_at,
                actor=BOT,
                provider_id=slot.provider_id,
                source=SOURCE,
            )
        except SlotUnavailableError as taken:
            others = first_per_day(taken.alternatives, turn.tz, MAX_OPTIONS)
            text = fixed.reminder_slot_taken(turn.voice, alternatives=bool(others))
            return await _offer(turn, reminder, others, text) if others else _text(text)
        except AgendaError:
            return _text(fixed.reminder_not_moved(turn.voice))
        _log(tap, reminder)
    return _text(fixed.reminder_moved(turn.voice, reminder.service_name, turn.when(slot.starts_at)))


async def _offer(turn: _Turn, reminder: db.TappedReminder, slots: Sequence[Slot], text: str) -> TapReply:
    async with turn.ctx.db.clinic_transaction(turn.clinic_id) as conn:
        await data.save_offered_slots(
            conn,
            turn.clinic_id,
            turn.conversation_id,
            [data.NewSlot(s.id, reminder.service_id, s.provider_id, s.starts_at, s.label) for s in slots],
        )
    options = [Option(id=f"{MOVE_PREFIX}{reminder.id}:{s.id}", title=s.label) for s in slots]
    return TapReply(text, outbound.buttons(text, options), tuple((s.id, reminder.service_id) for s in slots))


def _text(content: str) -> TapReply:
    return TapReply(content, outbound.text(content))


def _label(text: str | None) -> str:
    """'Confirmo', 'CONFIRMO ' or 'confirmó' read the same."""
    decomposed = unicodedata.normalize("NFKD", (text or "").casefold())
    return "".join(char for char in decomposed if char.isalnum())


def _move_id(reply_id: str) -> tuple[UUID, str] | None:
    reminder, _, slot_key = reply_id.removeprefix(MOVE_PREFIX).partition(":")
    try:
        reminder_id = UUID(reminder)
    except ValueError:
        return None
    if not slot_key or len(slot_key) > 16 or not slot_key.isalnum():
        return None
    return reminder_id, slot_key


def _log(tap: Tap, reminder: db.TappedReminder) -> None:
    logger.info(
        "reminders.tap_applied",
        extra={"action": tap.action, "appointment_id": str(reminder.appointment_id)},
    )
