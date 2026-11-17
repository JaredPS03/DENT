"""What the AI sees of the conversation: the last messages, with the patient's words always
wrapped in tags of our own.

The patient's text is data, never instructions (bot rule 15). It goes inside <mensaje_paciente>
or <transcripcion>, and its angle brackets become look-alike characters, so no message can close
those tags or open one that imitates the prompt's. What the code adds (a tapped slot, a voice
note without transcription) goes outside the tags, in parentheses.
"""

from collections.abc import Mapping, Sequence
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from app.bot.reply import CONSENT_ACCEPT_PREFIX, CONSENT_DECLINE_ID, OfferedSlot, slot_button_id
from app.db.bot import HistoryRow

_LOOK_ALIKES = str.maketrans({"<": "‹", ">": "›"})


def as_patient_text(text: str, *, transcription: bool) -> str:
    tag = "transcripcion" if transcription else "mensaje_paciente"
    return f"<{tag}>{text.translate(_LOOK_ALIKES)}</{tag}>"


def build_messages(
    rows: Sequence[HistoryRow],
    slots_by_start: Mapping[datetime, OfferedSlot],
    tz: ZoneInfo,
    *,
    shown: Sequence[OfferedSlot] = (),
) -> list[dict[str, Any]]:
    """Messages API turns, oldest first, starting with the patient. Consecutive messages of the
    same side are joined, as the API would. `shown`: the slots the patient already saw, noted at
    the end so the AI can take a choice made by words or audio."""
    turns: list[tuple[str, str]] = []
    for row in rows:
        text: str | None
        if row.direction == "inbound":
            text = _patient_line(row, slots_by_start, tz)
            role = "user"
        else:
            text = _clinic_line(row)
            role = "assistant"
        if text is None:
            continue
        if turns and turns[-1][0] == role:
            turns[-1] = (role, f"{turns[-1][1]}\n{text}")
        else:
            turns.append((role, text))
    while turns and turns[0][0] != "user":
        turns.pop(0)
    while turns and turns[-1][0] != "user":  # the model answers the patient, never itself
        turns.pop()
    if turns and shown:
        listed = "; ".join(
            f"«{slot.label}» de {slot.service_name}, inicio exacto: {slot_button_id(slot, tz)}"
            for slot in shown
        )
        role, text = turns[-1]
        turns[-1] = (role, f"{text}\n(Horarios que ya le mostraste: {listed})")
    return [{"role": role, "content": text} for role, text in turns]


def _patient_line(  # noqa: PLR0911 - one line per kind of message
    row: HistoryRow, slots_by_start: Mapping[datetime, OfferedSlot], tz: ZoneInfo
) -> str:
    if row.reply_id and row.reply_id.startswith(CONSENT_ACCEPT_PREFIX):
        return "(Tocó «Acepto» en el aviso de privacidad)"
    if row.reply_id == CONSENT_DECLINE_ID:
        return "(Tocó «No acepto» en el aviso de privacidad)"
    if row.type == "audio":
        if row.transcription:
            return as_patient_text(row.transcription, transcription=True)
        return "(Nota de voz que no se pudo transcribir)"
    if row.type in {"image", "document"}:
        return "(Envió una imagen o un archivo; no lo interpretes)"
    if not row.content:
        return "(Mensaje sin texto)"
    line = as_patient_text(row.content, transcription=False)
    slot = _tapped_slot(row.reply_id, slots_by_start)
    if slot is not None:
        line += (
            f" (Tocó el horario «{slot.label}» de {slot.service_name}; "
            f"inicio exacto: {slot_button_id(slot, tz)})"
        )
    return line


def _clinic_line(row: HistoryRow) -> str | None:
    if not row.content:
        return None
    if row.sender == "human":
        return f"(Mensaje del equipo del consultorio) {row.content}"
    return row.content


def _tapped_slot(reply_id: str | None, slots_by_start: Mapping[datetime, OfferedSlot]) -> OfferedSlot | None:
    if not reply_id:
        return None
    try:
        starts_at = datetime.fromisoformat(reply_id)
    except ValueError:
        return None
    if starts_at.tzinfo is None:
        return None
    return slots_by_start.get(starts_at)
