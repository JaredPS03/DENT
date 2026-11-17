"""The functions the AI can call (spec, "Funciones que la IA puede llamar"), plus `responder`.

The AI decides, the code validates and executes (bot rule 14):
- The clinic and the patient come from the turn, never from the AI: no tool takes them.
- Every appointment id is checked against this patient's upcoming appointments.
- agendar_cita and reagendar_cita only accept a slot buscar_disponibilidad offered in an earlier
  turn of this conversation: the patient saw it and chose it, so the bot never books on its own.
- Without consent to the privacy notice, the agenda tools and registrar_paciente are refused.

Schemas are strict, with every property required (empty lists when unused). They carry no patient
data: the API caches them outside the protections of the conversation (seguridad.md).
A refusal goes back to the AI as an error tool result, in Spanish, saying what to do instead.
"""

import json
import logging
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import date, datetime, time, timedelta
from typing import Any, Literal
from uuid import UUID
from zoneinfo import ZoneInfo

from pydantic import BaseModel, ConfigDict, ValidationError

from app.bot.fixed import patient_label
from app.bot.prompt import clinic_block, opening_hours, spoken_moment
from app.bot.reply import ConsentRequest, OfferedSlot, Reply, ReplyRejectedError, build_reply
from app.bot.urgency import normalize
from app.db import bot as data
from app.db.connection import Database
from app.jobs.queue import TeamAlertJob, enqueue
from app.scheduling.availability import Slot, WeeklyHours
from app.scheduling.connector import (
    Actor,
    AgendaConnector,
    AgendaError,
    Booking,
    PatientBusyError,
    SlotUnavailableError,
)

logger = logging.getLogger(__name__)

BOT = Actor(type="bot")
MAX_SLOTS_PER_DAY = 3
MAX_SLOTS = 10
MAX_REASON = 200
MAX_SUMMARY = 600
MAX_NAME = 120
UNCONSENTED_URGENT_REASON = "posible urgencia (la detectó el asistente)"


def unconsented_summary(name: str | None) -> str:
    return (
        f"Paciente: {patient_label(name)}. Quiere: revisa la conversación; el asistente la pasó al "
        "equipo y, como no ha aceptado el aviso de privacidad, aquí no van detalles. Intentado: se "
        "le dijo que el equipo le responderá. Lo que escribió se borra a las 24 h."
    )


def unconsented_urgent_summary(name: str | None) -> str:
    return (
        f"Paciente: {patient_label(name)}. Quiere: atención por una posible urgencia que detectó el "
        "asistente. Intentado: se le mandó el mensaje de urgencia, con el 911 si le cuesta respirar "
        "o tragar. No aceptó el aviso de privacidad: aquí no van detalles y lo que escribió se borra "
        "a las 24 h. Revisa la conversación y llámale o escríbele ya."
    )


NO_CONSENT = (
    "El paciente no ha aceptado el aviso de privacidad: no puedes usar esta función. "
    "Responde solo dudas generales del consultorio; el sistema agrega el aviso a tu mensaje."
)


def _tool(name: str, description: str, properties: dict[str, Any]) -> dict[str, Any]:
    return {
        "name": name,
        "description": description,
        "strict": True,
        "input_schema": {
            "type": "object",
            "properties": properties,
            "required": list(properties),
            "additionalProperties": False,
        },
    }


_TEXT = {"type": "string"}
_TEXTS = {"type": "array", "items": {"type": "string"}}

TOOL_DEFINITIONS: list[dict[str, Any]] = [
    _tool(
        "buscar_disponibilidad",
        "Busca horarios libres de un servicio entre dos fechas. Úsala antes de ofrecer cualquier "
        "horario: solo puedes ofrecer los que devuelva. Devuelve hasta 10, cada uno con su id, su "
        "etiqueta y su inicio exacto. No la uses si el paciente no ha aceptado el aviso de privacidad.",
        {
            "servicio": {**_TEXT, "description": "Nombre del servicio, tal como aparece en <consultorio>."},
            "desde": {"type": "string", "format": "date", "description": "Primer día (AAAA-MM-DD)."},
            "hasta": {
                "type": "string",
                "format": "date",
                "description": "Último día (AAAA-MM-DD), hasta 31 días después de desde.",
            },
            "turno": {"type": "string", "enum": ["mañana", "tarde", "cualquiera"]},
        },
    ),
    _tool(
        "agendar_cita",
        "Agenda una cita en un horario que ofreciste en un mensaje anterior y que el paciente "
        "eligió. Nunca la uses sin que el paciente haya elegido el horario.",
        {
            "servicio": {**_TEXT, "description": "Nombre del servicio del horario elegido."},
            "inicio": {
                "type": "string",
                "format": "date-time",
                "description": "Inicio exacto del horario elegido, con su zona, como lo dio el sistema.",
            },
        },
    ),
    _tool(
        "reagendar_cita",
        "Mueve una cita próxima del paciente a un horario que ofreciste en un mensaje anterior y "
        "que el paciente eligió.",
        {
            "cita": {"type": "string", "format": "uuid", "description": "Id de la cita, de <paciente>."},
            "nuevo_inicio": {
                "type": "string",
                "format": "date-time",
                "description": "Inicio exacto del nuevo horario, con su zona.",
            },
        },
    ),
    _tool(
        "cancelar_cita",
        "Cancela una cita próxima del paciente cuando lo pide. Recuérdale la política de "
        "cancelación del consultorio si aplica.",
        {
            "cita": {"type": "string", "format": "uuid", "description": "Id de la cita, de <paciente>."},
            "motivo": {**_TEXT, "description": "Motivo en pocas palabras, sin detalles de salud."},
        },
    ),
    _tool(
        "consultar_mis_citas",
        "Lista las citas próximas del paciente que escribe. No sirve para ver citas de otras personas.",
        {},
    ),
    _tool(
        "info_consultorio",
        "Devuelve datos registrados del consultorio sobre un tema. Si un dato no está registrado, "
        "dilo y ofrece pasar la duda al consultorio.",
        {
            "tema": {
                "type": "string",
                "enum": ["horario", "direccion", "servicios", "precios", "pagos", "cancelacion"],
            }
        },
    ),
    _tool(
        "registrar_paciente",
        "Guarda el nombre del paciente cuando lo dice. El teléfono ya lo tiene el sistema: nunca lo pidas.",
        {"nombre": {**_TEXT, "description": "Nombre y apellido, como lo escribió el paciente."}},
    ),
    _tool(
        "escalar_a_humano",
        'Pasa la conversación al equipo del consultorio y lo alerta. Con prioridad "urgente" ante '
        "cualquier señal de urgencia de tus reglas (ante la duda, urgente): el sistema manda el "
        'mensaje de urgencia y termina el turno. Con prioridad "normal" si el paciente pide hablar '
        "con alguien, está molesto, tiene una queja o necesita algo que no puedes resolver; después "
        "usa responder para decirle que el equipo le responderá y, si el consultorio está cerrado, "
        "cuándo abre. Ante una duda clínica sin señales de urgencia, primero ofrece una valoración "
        "y úsala solo si el paciente prefiere que le conteste alguien del consultorio.",
        {
            "motivo": {**_TEXT, "description": "Por qué se escala, en pocas palabras."},
            "prioridad": {"type": "string", "enum": ["urgente", "normal"]},
            "resumen": {
                **_TEXT,
                "description": "Tres líneas: quién es, qué quiere y qué se intentó. Si el paciente no "
                "ha aceptado el aviso de privacidad, sin síntomas ni datos de salud.",
            },
        },
    ),
    _tool(
        "responder",
        "Entrega tu mensaje al paciente; es lo último del turno. Texto breve y, si el paciente debe "
        "elegir: botones (hasta 3, de máximo 20 caracteres), horarios (ids de buscar_disponibilidad) "
        "o lista (hasta 10 opciones). Entre botones y horarios, máximo 3; con más opciones, lista. "
        "Deja vacías las que no uses.",
        {"texto": _TEXT, "botones": _TEXTS, "horarios": _TEXTS, "lista": _TEXTS},
    ),
]
TOOL_NAMES = frozenset(tool["name"] for tool in TOOL_DEFINITIONS)


class ToolError(Exception):
    """A call the code refuses; the message tells the AI what to do instead."""


@dataclass(frozen=True, slots=True)
class Escalation:
    id: UUID
    priority: Literal["urgent", "normal"]


@dataclass
class TurnState:
    """What one turn knows and what its tools change. Built by the orchestrator."""

    clinic_id: UUID
    conversation_id: UUID
    patient_id: UUID
    clinic: data.ClinicProfile
    services: list[data.ServiceInfo]
    hours: list[WeeklyHours]
    profile: data.PatientProfile
    upcoming: list[data.UpcomingAppointment]
    # Slots offered in this conversation recently, by key; shown_at tells which the patient saw.
    offered: dict[str, data.StoredSlot]
    turn_started_at: datetime
    # "turn:<id of the batch's last message>": a retried turn finds its own escalations.
    turn_key: str
    escalation: Escalation | None = None
    reply: Reply | None = None
    allowed_link_hosts: frozenset[str] = field(default_factory=frozenset)
    consent_request: ConsentRequest | None = None  # set while the patient has not accepted

    @property
    def tz(self) -> ZoneInfo:
        return self.clinic.tz


class Tools:
    def __init__(
        self, db: Database, agenda: AgendaConnector, state: TurnState, clock: Callable[[], datetime]
    ) -> None:
        self._db = db
        self._agenda = agenda
        self.state = state
        self._clock = clock

    async def run(self, name: str, arguments: Mapping[str, Any]) -> str:
        handler = self._handlers().get(name)
        if handler is None:
            raise ToolError(f"La función {name[:40]!r} no existe.")
        result = await handler(dict(arguments))
        return json.dumps(result, ensure_ascii=False)

    def _handlers(self) -> dict[str, Callable[[dict[str, Any]], Any]]:
        return {
            "buscar_disponibilidad": self.buscar_disponibilidad,
            "agendar_cita": self.agendar_cita,
            "reagendar_cita": self.reagendar_cita,
            "cancelar_cita": self.cancelar_cita,
            "consultar_mis_citas": self.consultar_mis_citas,
            "info_consultorio": self.info_consultorio,
            "registrar_paciente": self.registrar_paciente,
            "escalar_a_humano": self.escalar_a_humano,
            "responder": self.responder,
        }

    # Agenda --------------------------------------------------------------------------------------

    async def buscar_disponibilidad(self, arguments: dict[str, Any]) -> dict[str, Any]:
        query = _parse(_Search, arguments)
        self._require_consent()
        service = self._service(query.servicio)
        today = self._clock().astimezone(self.state.tz).date()
        start_day, end_day = max(_date(query.desde), today), _date(query.hasta)
        if end_day < start_day:
            raise ToolError("La fecha hasta es anterior a desde (o ya pasó): revisa las fechas.")
        shift = {"mañana": "morning", "tarde": "afternoon"}.get(query.turno)
        slots = await self._agenda.find_slots(
            self.state.clinic_id,
            service.id,
            _local_midnight(start_day, self.state.tz),
            _local_midnight(end_day + timedelta(days=1), self.state.tz),
            actor=BOT,
            shift=shift,  # type: ignore[arg-type]
        )
        chosen = _spread(slots, self.state.tz)
        await self._remember(service, chosen)
        if not chosen:
            return {
                "horarios": [],
                "nota": "No hay horarios libres en esas fechas. Ofrece buscar otras fechas u otro turno, "
                "o pasar la conversación al consultorio.",
            }
        return {"horarios": [self._slot_out(slot) for slot in chosen]}

    async def agendar_cita(self, arguments: dict[str, Any]) -> dict[str, Any]:
        request = _parse(_Book, arguments)
        self._require_consent()
        service = self._service(request.servicio)
        slot = self._chosen_slot(service.id, _moment(request.inicio))
        try:
            appointment = await self._agenda.create(
                self.state.clinic_id,
                Booking(self.state.patient_id, service.id, slot.starts_at, provider_id=slot.provider_id),
                actor=BOT,
            )
        except SlotUnavailableError as taken:
            await self._remember(service, taken.alternatives)
            raise ToolError(self._taken(taken.alternatives)) from None
        except PatientBusyError:
            raise ToolError("El paciente ya tiene una cita a esa hora: revisa sus citas próximas.") from None
        except AgendaError as error:
            raise ToolError(str(error)) from None
        self.state.upcoming.append(
            data.UpcomingAppointment(appointment.id, service.id, service.name, appointment.starts_at)
        )
        return {"cita": {"servicio": service.name, "fecha": self._spoken(appointment.starts_at)}}

    async def reagendar_cita(self, arguments: dict[str, Any]) -> dict[str, Any]:
        request = _parse(_Move, arguments)
        self._require_consent()
        current = self._own_appointment(request.cita)
        slot = self._chosen_slot(current.service_id, _moment(request.nuevo_inicio))
        try:
            moved = await self._agenda.move(
                self.state.clinic_id, current.id, slot.starts_at, actor=BOT, provider_id=slot.provider_id
            )
        except SlotUnavailableError as taken:
            await self._remember_for(current.service_id, taken.alternatives)
            raise ToolError(self._taken(taken.alternatives)) from None
        except AgendaError as error:
            raise ToolError(str(error)) from None
        self.state.upcoming = [
            replace(a, starts_at=moved.starts_at) if a.id == current.id else a for a in self.state.upcoming
        ]
        return {"cita": {"servicio": current.service_name, "fecha": self._spoken(moved.starts_at)}}

    async def cancelar_cita(self, arguments: dict[str, Any]) -> dict[str, Any]:
        request = _parse(_Cancel, arguments)
        self._require_consent()
        current = self._own_appointment(request.cita)
        reason = _clip(request.motivo, MAX_REASON) or "Lo pidió el paciente por WhatsApp"
        try:
            await self._agenda.cancel(self.state.clinic_id, current.id, actor=BOT, reason=reason)
        except AgendaError as error:
            raise ToolError(str(error)) from None
        self.state.upcoming = [a for a in self.state.upcoming if a.id != current.id]
        return {"cancelada": {"servicio": current.service_name, "fecha": self._spoken(current.starts_at)}}

    async def consultar_mis_citas(self, arguments: dict[str, Any]) -> dict[str, Any]:
        _parse(_Nothing, arguments)
        self._require_consent()
        return {
            "citas": [
                {"cita": str(a.id), "servicio": a.service_name, "fecha": self._spoken(a.starts_at)}
                for a in self.state.upcoming
            ]
        }

    # Clinic and patient --------------------------------------------------------------------------

    async def info_consultorio(self, arguments: dict[str, Any]) -> dict[str, Any]:
        topic = _parse(_Topic, arguments).tema
        clinic = self.state.clinic
        answers: dict[str, str | None] = {
            "horario": opening_hours(self.state.hours),
            "direccion": clinic.address
            and (clinic.address + (f" ({clinic.maps_url})" if clinic.maps_url else "")),
            "servicios": clinic_block(clinic, self.state.services, self.state.hours),
            "precios": "; ".join(
                f"{s.name}: ${s.price_cents // 100:,} MXN de referencia"
                for s in self.state.services
                if s.price_cents is not None
            )
            or None,
            "pagos": clinic.payment_methods,
            "cancelacion": clinic.cancellation_policy,
        }
        answer = answers.get(topic)
        if not answer:
            return {"dato": None, "nota": "No está registrado: dilo y ofrece pasar la duda al consultorio."}
        return {"dato": answer}

    async def registrar_paciente(self, arguments: dict[str, Any]) -> dict[str, Any]:
        name = " ".join(_parse(_Name, arguments).nombre.split())
        self._require_consent()
        if not name or len(name) > MAX_NAME or not all(c.isalpha() or c in " .'-" for c in name):
            raise ToolError("Ese no parece un nombre: pídele al paciente su nombre y apellido.")
        async with self._db.clinic_transaction(self.state.clinic_id) as conn:
            await data.set_patient_name(conn, self.state.patient_id, name)
        self.state.profile = replace(self.state.profile, name=name)
        return {"registrado": True}

    async def escalar_a_humano(self, arguments: dict[str, Any]) -> dict[str, Any]:
        request = _parse(_Escalate, arguments)
        priority: Literal["urgent", "normal"] = "urgent" if request.prioridad == "urgente" else "normal"
        current = self.state.escalation
        # One per turn; an urgency found after a normal escalation still opens its own.
        if current is None or (current.priority == "normal" and priority == "urgent"):
            if self.state.profile.consented:
                reason = _clip(request.motivo, MAX_REASON) or "Pedido por el asistente"
                summary = _clip(request.resumen, MAX_SUMMARY) or "Sin resumen."
            elif priority == "urgent":
                # Without consent nothing the AI wrote about the patient is kept (no symptoms), but
                # the team must know it is an urgency.
                reason = UNCONSENTED_URGENT_REASON
                summary = unconsented_urgent_summary(self.state.profile.name)
            else:
                reason = "Pedido por el asistente"
                summary = unconsented_summary(self.state.profile.name)
            self.state.escalation = await escalate(
                self._db,
                clinic_id=self.state.clinic_id,
                conversation_id=self.state.conversation_id,
                reason=reason,
                priority=priority,
                summary=summary,
                turn_key=self.state.turn_key,
            )
        if priority == "urgent":
            return {"escalado": True}
        return {
            "escalado": True,
            "nota": "El equipo ya fue avisado. Usa responder para decirle al paciente que el equipo "
            "le responderá; si el consultorio está cerrado, dile cuándo abre.",
        }

    async def responder(self, arguments: dict[str, Any]) -> dict[str, Any]:
        offered = {
            key: OfferedSlot(slot.key, slot.service_id, slot.service_name, slot.starts_at, slot.label)
            for key, slot in self.state.offered.items()
        }
        try:
            self.state.reply = build_reply(
                arguments,
                offered=offered,
                tz=self.state.tz,
                allowed_link_hosts=self.state.allowed_link_hosts,
                consent_request=self.state.consent_request,
            )
        except ReplyRejectedError as rejected:
            raise ToolError(str(rejected)) from None
        return {"enviado": True}

    # Helpers -------------------------------------------------------------------------------------

    def _require_consent(self) -> None:
        if not self.state.profile.consented:
            raise ToolError(NO_CONSENT)

    def _service(self, name: str) -> data.ServiceInfo:
        wanted = normalize(name)
        for service in self.state.services:
            if normalize(service.name) == wanted:
                return service
        names = ", ".join(service.name for service in self.state.services) or "ninguno"
        raise ToolError(f"Ese servicio no existe en este consultorio. Servicios: {names}.")

    def _own_appointment(self, appointment_id: str) -> data.UpcomingAppointment:
        try:
            wanted = UUID(appointment_id)
        except ValueError:
            wanted = None
        for appointment in self.state.upcoming:
            if appointment.id == wanted:
                return appointment
        raise ToolError("Esa cita no es una cita próxima de este paciente: usa los ids de <paciente>.")

    def _chosen_slot(self, service_id: UUID, starts_at: datetime) -> data.StoredSlot:
        matches = [
            slot
            for slot in self.state.offered.values()
            if slot.service_id == service_id and slot.starts_at == starts_at
        ]
        seen = [s for s in matches if s.shown_at is not None and s.shown_at < self.state.turn_started_at]
        if seen:
            return seen[0]
        if matches:
            raise ToolError(
                "El paciente todavía no ha visto ese horario: ofrécelo con responder y espera a que lo elija."
            )
        raise ToolError(
            "Ese horario no lo ofreciste antes en esta conversación para ese servicio. Busca con "
            "buscar_disponibilidad y ofrécelo."
        )

    async def _remember(self, service: data.ServiceInfo, slots: Sequence[Slot]) -> None:
        await self._remember_for(service.id, slots, service.name)

    async def _remember_for(
        self, service_id: UUID, slots: Sequence[Slot], service_name: str | None = None
    ) -> None:
        if not slots:
            return
        name = service_name or next((s.name for s in self.state.services if s.id == service_id), "")
        async with self._db.clinic_transaction(self.state.clinic_id) as conn:
            offered_at = await data.save_offered_slots(
                conn,
                self.state.clinic_id,
                self.state.conversation_id,
                [data.NewSlot(s.id, service_id, s.provider_id, s.starts_at, s.label) for s in slots],
            )
        for s in slots:
            self.state.offered[s.id] = data.StoredSlot(
                s.id, service_id, name, s.provider_id, s.starts_at, s.label, offered_at
            )

    def _taken(self, alternatives: Sequence[Slot]) -> str:
        if not alternatives:
            return (
                "Ese horario se acaba de ocupar y no hay alternativas cercanas: discúlpate y ofrece "
                "buscar otra fecha o pasar con el equipo."
            )
        return "Ese horario se acaba de ocupar. Ofrece estas alternativas con responder: " + json.dumps(
            [self._slot_out(s) for s in alternatives], ensure_ascii=False
        )

    def _slot_out(self, slot: Slot) -> dict[str, str]:
        return {
            "id": slot.id,
            "etiqueta": slot.label,
            "inicio": slot.starts_at.astimezone(self.state.tz).isoformat(),
        }

    def _spoken(self, moment: datetime) -> str:
        local = moment.astimezone(self.state.tz)
        return spoken_moment(local, this_year=self.state.turn_started_at.astimezone(self.state.tz).year)


async def escalate(  # noqa: PLR0913 - one value per column
    db: Database,
    *,
    clinic_id: UUID,
    conversation_id: UUID,
    reason: str,
    priority: Literal["urgent", "normal"],
    summary: str,
    turn_key: str,
) -> Escalation:
    """Opens the escalation and queues the alert to the team in one transaction. `turn_key` makes
    it once per turn and priority: a retried turn finds it and alerts nobody again. The chat
    passes to a person when the turn ends, with the reply (orchestrator._finish), so a turn that
    fails after escalating is retried instead of leaving the patient without an answer."""
    async with db.clinic_transaction(clinic_id) as conn:
        opened = await data.insert_escalation(
            conn,
            clinic_id=clinic_id,
            conversation_id=conversation_id,
            reason=reason,
            priority=priority,
            summary=summary,
            turn_key=f"{turn_key}:{priority}",
        )
        if opened.created and await data.queue_team_alerts(conn, clinic_id, opened.id):
            await enqueue(conn, TeamAlertJob(clinic_id=clinic_id, escalation_id=opened.id))
    logger.info(
        "bot.escalated",
        extra={
            "conversation_id": str(conversation_id),
            "escalation_id": str(opened.id),
            "priority": priority,
            "again": not opened.created,
        },
    )
    return Escalation(opened.id, priority)


# Arguments ---------------------------------------------------------------------------------------


class _Args(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class _Search(_Args):
    servicio: str
    desde: str
    hasta: str
    turno: Literal["mañana", "tarde", "cualquiera"]


class _Book(_Args):
    servicio: str
    inicio: str


class _Move(_Args):
    cita: str
    nuevo_inicio: str


class _Cancel(_Args):
    cita: str
    motivo: str


class _Nothing(_Args):
    pass


class _Topic(_Args):
    tema: Literal["horario", "direccion", "servicios", "precios", "pagos", "cancelacion"]


class _Name(_Args):
    nombre: str


class _Escalate(_Args):
    motivo: str
    prioridad: Literal["urgente", "normal"]
    resumen: str


def _parse[T: _Args](model: type[T], arguments: dict[str, Any]) -> T:
    try:
        return model.model_validate(arguments)
    except ValidationError:
        raise ToolError("Los datos de la llamada no cumplen el esquema de la función.") from None


def _date(value: str) -> date:
    try:
        return date.fromisoformat(value)
    except ValueError:
        raise ToolError("Las fechas van como AAAA-MM-DD.") from None


def _moment(value: str) -> datetime:
    try:
        moment = datetime.fromisoformat(value)
    except ValueError:
        moment = None
    if moment is None or moment.tzinfo is None:
        raise ToolError("El inicio va con fecha, hora y zona, tal como lo dio el sistema.")
    return moment


def _local_midnight(day: date, tz: ZoneInfo) -> datetime:
    return datetime.combine(day, time(0), tzinfo=tz)


def _spread(slots: Sequence[Slot], tz: ZoneInfo) -> list[Slot]:
    """Up to 3 slots a day and 10 in all, in order, so a week does not end at the first morning."""
    chosen: list[Slot] = []
    per_day: dict[date, int] = {}
    for slot in slots:
        day = slot.starts_at.astimezone(tz).date()
        if per_day.get(day, 0) >= MAX_SLOTS_PER_DAY:
            continue
        per_day[day] = per_day.get(day, 0) + 1
        chosen.append(slot)
        if len(chosen) == MAX_SLOTS:
            break
    return chosen


def _clip(text: str, limit: int) -> str:
    text = " ".join(text.split())
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"
