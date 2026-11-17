"""The templates Axon sends outside the 24 h window: their names, the text submitted to Meta and how
their parameters are filled.

Meta rejects parameters that are empty or carry line breaks or tabs, so every value goes through
`one_line`. Parameters never carry more patient data than the message needs: the first name, the
service and the time.
"""

from collections.abc import Sequence
from datetime import datetime

from app.bot.prompt import at_time, spoken_day

LANGUAGE = "es_MX"

# From each clinic's number (catalog in the templates table).
REMINDER = "recordatorio_cita"
REOPEN = "retomar_conversacion"
# From Axon's own number (configuration, not the templates table).
TEAM_ALERT = "alerta_equipo"
OPS_ALERT = "alerta_axon"

# recordatorio_cita as submitted to Meta (decision 008): one neutral text for "tú" and "usted",
# with three quick replies. Meta sends back a tap with the button's label, so the labels are what
# the code reads: changing one in Meta needs the same change in REMINDER_BUTTONS.
REMINDER_BODY = "Hola {1}, este es el recordatorio de la cita de {2} mañana {3} a las {4} en {5}."
REMINDER_BUTTONS = {"confirmo": "confirm", "cambiar": "change", "cancelar": "cancel"}

# alerta_axon as submitted to Meta: what happened, where and how many times.
OPS_ALERT_BODY = "Alerta de Axon: {1} en {2} ({3}). Revisa Sentry para el detalle."

# Used as {{1}} when the patient never told their name: "Hola de nuevo, te escribimos de…".
UNKNOWN_NAME_GREETING = "de nuevo"
_TITLES = frozenset({"sr", "sra", "srita", "dr", "dra", "don", "doña", "lic", "ing", "mtro", "mtra"})
MAX_PARAMETER = 60


def greeting_name(full_name: str | None) -> str:
    """The first name for "Hola {{1}}": without titles (Sra., Dr.) and without shouting if it was
    typed in capitals. "de nuevo" when the clinic does not know it yet."""
    words = [word for word in (full_name or "").split() if word.lower().rstrip(".") not in _TITLES]
    if not words:
        return UNKNOWN_NAME_GREETING
    first = words[0]
    return first.capitalize() if first.isupper() else first


def one_line(text: str | None, limit: int = MAX_PARAMETER) -> str | None:
    """A template parameter: no new lines, tabs or runs of spaces, and not too long."""
    if not text:
        return None
    single = " ".join(text.split())
    return single[:limit].rstrip() or None


def reminder_parameters(
    *, patient_name: str | None, service_name: str, local_start: datetime, clinic_name: str
) -> list[str]:
    """{{1}} to {{5}} of recordatorio_cita: "Ana", "limpieza", "martes 18 de noviembre", "10:00 am"
    and the clinic's name. `local_start` is in the clinic's time zone."""
    clock = at_time(local_start.time()).removeprefix("a las ").removeprefix("a la ")
    return [
        one_line(greeting_name(patient_name)) or UNKNOWN_NAME_GREETING,
        one_line(service_name) or "tu cita",
        spoken_day(local_start),
        clock,
        one_line(clinic_name) or "el consultorio",
    ]


def fill(body: str, parameters: Sequence[str]) -> str:
    """The text the patient reads, for the conversation in the inbox and the bot's history."""
    text = body
    for position, value in enumerate(parameters, start=1):
        text = text.replace(f"{{{position}}}", value)
    return text
