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

# recordatorio_cita as submitted to Meta (decision 008, wording by revisor-clinico): one neutral
# text for "tú" and "usted", not ending in a variable, with three quick replies. Meta sends back a
# tap with the button's label only, so the labels are what the code reads: changing one in Meta
# needs the same change here. The short labels of the specification are read too.
REMINDER_BODY = "Hola {1}, un recordatorio: la cita de {2} en {3} es mañana {4}. ¿Confirmamos?"
REMINDER_BUTTON_LABELS = ("Confirmo", "Cambiar horario", "Cancelar cita")
REMINDER_BUTTONS = {
    "confirmo": "confirm",
    "cambiarhorario": "change",
    "cancelarcita": "cancel",
    "cambiar": "change",
    "cancelar": "cancel",
}

# alerta_axon as submitted to Meta: what happened, where and how many cases.
OPS_ALERT_BODY = "Alerta de Axon: {1} en {2}. Casos: {3}. Revisa Sentry para el detalle."

# Used as {{1}} when the patient never told their name: "Hola de nuevo, te escribimos de…".
UNKNOWN_NAME_GREETING = "de nuevo"
_TITLES = frozenset({"sr", "sra", "srita", "dr", "dra", "don", "doña", "lic", "ing", "mtro", "mtra"})
MAX_PARAMETER = 60


def greeting_name(full_name: str | None) -> str:
    """The first name for "Hola {{1}}": without titles (Sra., Dr.) and with its capital, whether it
    was typed shouting or all in lowercase. "de nuevo" when the clinic does not know it yet."""
    words = [word for word in (full_name or "").split() if word.lower().rstrip(".") not in _TITLES]
    if not words:
        return UNKNOWN_NAME_GREETING
    first = words[0]
    return first.capitalize() if first.isupper() or first.islower() else first


def service_phrase(name: str | None) -> str:
    """The service in the middle of a sentence: "la cita de limpieza", but "LED" stays as it is."""
    service = one_line(name)
    if not service:
        return "consulta"
    if len(service) > 1 and service[0].isupper() and service[1].islower():
        return service[0].lower() + service[1:]
    return service


def one_line(text: str | None, limit: int = MAX_PARAMETER) -> str | None:
    """A template parameter: no new lines, tabs or runs of spaces, and not too long."""
    if not text:
        return None
    single = " ".join(text.split())
    return single[:limit].rstrip() or None


def reminder_parameters(
    *, patient_name: str | None, service_name: str, local_start: datetime, clinic_name: str
) -> list[str]:
    """{{1}} to {{4}} of recordatorio_cita: "Ana", "limpieza", the clinic's name and "miércoles 18 de
    noviembre a las 10:00 am" (or "a la 1:30 pm"). `local_start` is in the clinic's time zone."""
    return [
        one_line(greeting_name(patient_name)) or UNKNOWN_NAME_GREETING,
        service_phrase(service_name),
        one_line(clinic_name) or "el consultorio",
        f"{spoken_day(local_start)} {at_time(local_start.time())}",
    ]


def fill(body: str, parameters: Sequence[str]) -> str:
    """The text the patient reads, for the conversation in the inbox and the bot's history."""
    text = body
    for position, value in enumerate(parameters, start=1):
        text = text.replace(f"{{{position}}}", value)
    return text
