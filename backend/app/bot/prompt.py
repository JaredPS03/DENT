"""The system prompt of a turn, from the versioned copy of docs/prompt-del-bot.md.

Order for the prompt cache (bot-ia.md): the tools, then everything fixed for the clinic (role,
rules, examples and its <consultorio> block) with the cache mark, and last the time and the
patient, which change every turn. Nothing that changes per turn goes in the first part.
"""

import re
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, time
from functools import cache
from pathlib import Path
from typing import Any

from app.bot.fixed import TEAM
from app.bot.urgency import active_signals
from app.db.bot import ClinicProfile, PatientProfile, ServiceInfo, UpcomingAppointment
from app.scheduling.availability import WeeklyHours

PROMPTS_DIR = Path(__file__).parent / "prompts"
_VARIABLE = re.compile(r"\{\{([a-z_.]+)\}\}")
_MAX_FIRST_NAME = 30
_SPLIT = "<contexto>"

WEEKDAYS = ("lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo")
MONTHS = (
    "enero", "febrero", "marzo", "abril", "mayo", "junio",
    "julio", "agosto", "septiembre", "octubre", "noviembre", "diciembre",
)  # fmt: skip


@cache
def template(version: str) -> str:
    path = PROMPTS_DIR / f"v{version}.md"
    text = path.read_text(encoding="utf-8")
    if text.count(_SPLIT) != 1:
        raise ValueError(f"prompt {version} must have one {_SPLIT} block, at the end")
    return text


@dataclass(frozen=True, slots=True)
class PatientContext:
    profile: PatientProfile
    upcoming: Sequence[UpcomingAppointment]


def system_blocks(  # noqa: PLR0913 - the parts of one prompt
    version: str,
    *,
    clinic: ClinicProfile,
    services: Sequence[ServiceInfo],
    hours: Sequence[WeeklyHours],
    now: datetime,
    patient: PatientContext,
) -> list[dict[str, Any]]:
    fixed_part, turn_part = template(version).split(_SPLIT)
    fixed_text = _fill(
        fixed_part,
        {
            "consultorio.nombre": clinic.name,
            "consultorio.ciudad": clinic.city or "México",
            "consultorio.doctor": clinic.doctor or TEAM,
            "consultorio.trato": "usted" if clinic.formal else "tú",
            "consultorio.senales_urgencia": "; ".join(active_signals(clinic.urgency_signals)),
            "consultorio.bloque": clinic_block(clinic, services, hours),
        },
    )
    turn_text = _fill(
        _SPLIT + turn_part,
        {
            "ahora": spoken_moment(now.astimezone(clinic.tz)),
            "paciente.bloque": patient_block(patient, clinic, this_year=now.astimezone(clinic.tz).year),
        },
    )
    return [
        {"type": "text", "text": fixed_text.strip(), "cache_control": {"type": "ephemeral"}},
        {"type": "text", "text": turn_text.strip()},
    ]


def clinic_block(clinic: ClinicProfile, services: Sequence[ServiceInfo], hours: Sequence[WeeklyHours]) -> str:
    lines = []
    if clinic.address:
        lines.append(f"Dirección: {clinic.address}" + (f" ({clinic.maps_url})" if clinic.maps_url else ""))
    lines.append(f"Horario: {opening_hours(hours)}")
    if services:
        lines.append("Servicios:")
        lines.extend(f"- {_service_line(service)}" for service in services)
    else:
        lines.append("Servicios: no registrados")
    lines.append(f"Formas de pago: {clinic.payment_methods or 'no registradas'}")
    lines.append(f"Política de cancelación: {clinic.cancellation_policy or 'no registrada'}")
    return "\n".join(lines)


def patient_block(patient: PatientContext, clinic: ClinicProfile, *, this_year: int) -> str:
    year = this_year
    profile = patient.profile
    lines = [
        f"Nombre: {_first_name(profile.name) or 'desconocido'}",
        f"Aceptó el aviso de privacidad: {'sí' if profile.consented else 'no'}",
        f"Paciente: {'ya ha venido antes' if profile.returning else 'nuevo'}",
    ]
    if patient.upcoming:
        lines.append("Citas próximas:")
        lines.extend(
            f"- cita {appointment.id}: {appointment.service_name}, "
            f"{spoken_moment(appointment.starts_at.astimezone(clinic.tz), this_year=year)}"
            for appointment in patient.upcoming
        )
    else:
        lines.append("Citas próximas: ninguna")
    return "\n".join(lines)


def spoken_moment(moment: datetime, *, this_year: int | None = None) -> str:
    """'martes 17 de noviembre de 2026, 4:35 pm', as people say it; without the year when it is
    `this_year` ('jueves 19 de noviembre, 4:00 pm')."""
    day = f"{WEEKDAYS[moment.weekday()]} {moment.day} de {MONTHS[moment.month - 1]}"
    if moment.year != this_year:
        day += f" de {moment.year}"
    return f"{day}, {_clock(moment.time())}"


def opening_hours(hours: Sequence[WeeklyHours]) -> str:
    """'lunes a viernes de 9:00 am a 7:00 pm (cerrado de 2:00 pm a 4:00 pm); sábado de 9:00 am a 2:00 pm;
    domingo cerrado', joining the providers' hours of each day."""
    by_day: dict[int, str] = {}
    for weekday in range(1, 8):
        rows = [row for row in hours if row.weekday == weekday]
        if not rows:
            by_day[weekday] = "cerrado"
            continue
        start, end = min(row.start for row in rows), max(row.end for row in rows)
        text = f"de {_clock(start)} a {_clock(end)}"
        breaks = {(row.break_start, row.break_end) for row in rows}
        if len(breaks) == 1:
            break_start, break_end = next(iter(breaks))
            if break_start and break_end:
                text += f" (cerrado de {_clock(break_start)} a {_clock(break_end)})"
        by_day[weekday] = text
    parts: list[str] = []
    day = 1
    while day <= 7:
        last = day
        while last < 7 and by_day[last + 1] == by_day[day]:
            last += 1
        days = WEEKDAYS[day - 1] if last == day else f"{WEEKDAYS[day - 1]} a {WEEKDAYS[last - 1]}"
        parts.append(f"{days} {by_day[day]}")
        day = last + 1
    return "; ".join(parts)


def _service_line(service: ServiceInfo) -> str:
    line = f"{service.name} ({service.duration_minutes} min"
    if service.price_cents is not None:
        line += f", precio de referencia ${service.price_cents // 100:,} MXN"
    return line + ")"


def _clock(moment: time) -> str:
    hour = moment.hour % 12 or 12
    return f"{hour}:{moment.minute:02d} {'am' if moment.hour < 12 else 'pm'}"


def _first_name(name: str | None) -> str | None:
    """Only the first name goes to the AI (seguridad.md, "A la IA mándale solo lo necesario"),
    and only if it looks like one: the name may come from the patient's WhatsApp profile."""
    first = name.split()[0] if name and name.split() else ""
    if not first or len(first) > _MAX_FIRST_NAME or not all(c.isalpha() or c in "'-" for c in first):
        return None
    return first


def _fill(text: str, values: dict[str, str]) -> str:
    """One pass over the template's own variables: a value that happens to contain "{{" (a
    clinic's name, its policies) is never read as a variable."""
    missing = set(_VARIABLE.findall(text)) - values.keys()
    if missing:
        raise ValueError(f"the prompt has variables without a value: {sorted(missing)}")
    return _VARIABLE.sub(lambda match: values[match.group(1)], text)
