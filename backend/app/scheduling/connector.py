"""The agenda behind one interface, so the bot and the panel never care where it lives.

Phase 1 has Axon's own agenda in Postgres (AxonAgenda). Google Calendar and Dentalink arrive in
Phase 2 as other implementations of the same four operations.
"""

from dataclasses import dataclass
from datetime import datetime
from typing import Literal, Protocol
from uuid import UUID

from app.scheduling.availability import Slot

ActorType = Literal["user", "bot", "system"]
Shift = Literal["morning", "afternoon"]
AppointmentStatus = Literal["pending", "confirmed", "attended", "no_show", "cancelled"]


@dataclass(frozen=True, slots=True)
class Actor:
    """Who asks for the change; it goes to audit_log. The bot books as created_by 'bot'."""

    type: ActorType
    user_id: UUID | None = None


@dataclass(frozen=True, slots=True)
class Booking:
    patient_id: UUID
    service_id: UUID
    starts_at: datetime
    provider_id: UUID | None = None  # None: whichever provider has that slot free


@dataclass(frozen=True, slots=True)
class Appointment:
    id: UUID
    patient_id: UUID
    service_id: UUID
    provider_id: UUID
    starts_at: datetime
    ends_at: datetime
    status: AppointmentStatus


class AgendaError(Exception):
    """Base of the agenda's expected refusals. Messages are for people and carry no data."""


class SlotUnavailableError(AgendaError):
    """The slot is taken or not on the agenda. Carries the nearest free alternatives."""

    def __init__(self, alternatives: list[Slot]) -> None:
        super().__init__("Ese horario ya no está disponible.")
        self.alternatives = alternatives


class NotFoundError(AgendaError):
    """The appointment, patient, service or provider does not exist in this clinic."""


class InvalidChangeError(AgendaError):
    """The change is not allowed: an inactive service, or the appointment's current state."""


class AgendaConnector(Protocol):
    async def find_slots(  # noqa: PLR0913 - window, service and options of one search
        self,
        clinic_id: UUID,
        service_id: UUID,
        window_start: datetime,
        window_end: datetime,
        *,
        actor: Actor,
        shift: Shift | None = None,
    ) -> list[Slot]: ...

    async def create(self, clinic_id: UUID, booking: Booking, *, actor: Actor) -> Appointment: ...

    async def move(
        self,
        clinic_id: UUID,
        appointment_id: UUID,
        starts_at: datetime,
        *,
        actor: Actor,
        provider_id: UUID | None = None,
    ) -> Appointment: ...

    async def cancel(
        self, clinic_id: UUID, appointment_id: UUID, *, actor: Actor, reason: str | None = None
    ) -> Appointment: ...
