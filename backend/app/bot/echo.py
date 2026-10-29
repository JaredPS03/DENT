"""Temporary echo bot of milestone 1B (ECHO_BOT_ENABLED, never in production).

It answers each turn with what the patient sent, in one message, so the round trip can be tested
with Meta's test number: a burst of five messages gets one answer, and a voice note comes back
as its transcription. The AI bot of milestone 1C replaces it.
"""

from collections.abc import Sequence

from app.db.conversations import PendingMessage
from app.whatsapp.outbound import MAX_TEXT

_LABELS = {
    "audio": "nota de voz",
    "image": "foto",
    "document": "documento",
}


# Says plainly that it is a test, in case someone sees it on a phone during a demo.
PREFIX = "Prueba de Axon (no es respuesta del consultorio)."


def echo_reply(batch: Sequence[PendingMessage]) -> str:
    parts = [_describe(message) for message in batch]
    heading = f"{PREFIX} Recibí:" if len(parts) == 1 else f"{PREFIX} Recibí {len(parts)} mensajes:"
    reply = f"{heading} " + " / ".join(parts)
    return reply if len(reply) <= MAX_TEXT // 2 else reply[: MAX_TEXT // 2 - 1] + "…"


def _describe(message: PendingMessage) -> str:
    if message.type == "audio":
        return (
            f"(nota de voz) {message.transcription}"
            if message.transcription
            else "(nota de voz sin transcribir)"
        )
    if message.content:
        label = _LABELS.get(message.type)
        return f"({label}) {message.content}" if label else message.content
    return f"({_LABELS.get(message.type, 'mensaje que todavía no leo')})"
