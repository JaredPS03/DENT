"""/api/citas: the panel books, moves, cancels and marks appointments with the bot's own rules.

The clinic comes from the session; patient, service and appointment ids from the body only count
if RLS finds them in that clinic. Every change leaves its event and its audit_log row.
"""

from datetime import datetime
from typing import Annotated, Literal, cast
from uuid import UUID

from fastapi import APIRouter, Body, Depends, HTTPException, Request, status
from fastapi.responses import JSONResponse
from pydantic import AwareDatetime, BaseModel, ConfigDict, Field

from app.api.auth import KeySource, PanelSession, RateLimiter, client_key, resolve_session
from app.dependencies import get_db, get_settings_from_app
from app.scheduling.availability import Slot
from app.scheduling.axon import AxonAgenda
from app.scheduling.connector import (
    Actor,
    AgendaError,
    Appointment,
    Booking,
    InvalidChangeError,
    NotFoundError,
    PatientBusyError,
    SlotUnavailableError,
)

router = APIRouter(prefix="/api/citas", tags=["citas"])

# Every minute: requests per signed-in user, and rejected attempts per address.
USER_REQUESTS_PER_MINUTE = 60
IP_REQUESTS_PER_MINUTE = 120


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class NewAppointment(_Strict):
    patient_id: UUID
    service_id: UUID
    starts_at: AwareDatetime
    provider_id: UUID | None = None


class Reschedule(_Strict):
    action: Literal["reschedule"]
    starts_at: AwareDatetime
    provider_id: UUID | None = None


class Cancel(_Strict):
    action: Literal["cancel"]
    reason: str | None = Field(default=None, max_length=500)


class Outcome(_Strict):
    action: Literal["attended", "no_show"]


Change = Annotated[Reschedule | Cancel | Outcome, Field(discriminator="action")]


class AppointmentOut(BaseModel):
    id: UUID
    patient_id: UUID
    service_id: UUID
    provider_id: UUID
    starts_at: datetime
    ends_at: datetime
    status: str

    @classmethod
    def of(cls, appointment: Appointment) -> "AppointmentOut":
        return cls(
            id=appointment.id,
            patient_id=appointment.patient_id,
            service_id=appointment.service_id,
            provider_id=appointment.provider_id,
            starts_at=appointment.starts_at,
            ends_at=appointment.ends_at,
            status=appointment.status,
        )


class SlotOut(BaseModel):
    id: str
    provider_id: UUID
    starts_at: datetime
    ends_at: datetime
    label: str

    @classmethod
    def of(cls, slot: Slot) -> "SlotOut":
        return cls(
            id=slot.id,
            provider_id=slot.provider_id,
            starts_at=slot.starts_at,
            ends_at=slot.ends_at,
            label=slot.label,
        )


async def panel_session(request: Request) -> PanelSession:
    """The verified session, within the limits: per user for every validly signed session, and per
    address only for rejected attempts. Behind the hosting's proxy every request may share one address, so a
    valid session never spends that address's budget."""
    settings = get_settings_from_app(request)
    limiters = cast(dict[str, RateLimiter], request.app.state.rate_limiters)
    try:
        session = await resolve_session(
            request,
            get_db(request),
            cast(KeySource, request.app.state.jwks),
            issuer=f"{settings.supabase_url}/auth/v1",
            user_limiter=limiters["user"],
        )
    except HTTPException as rejected:
        address = client_key(request.client.host if request.client else None)
        if rejected.status_code == status.HTTP_401_UNAUTHORIZED and not limiters["ip"].allow(address):
            raise _too_many() from None
        raise
    return session


def _too_many() -> HTTPException:
    return HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, "Demasiadas solicitudes; espera un minuto.")


SessionDep = Annotated[PanelSession, Depends(panel_session)]


def get_agenda(request: Request) -> AxonAgenda:
    return cast(AxonAgenda, request.app.state.agenda)


AgendaDep = Annotated[AxonAgenda, Depends(get_agenda)]


@router.post("", status_code=status.HTTP_201_CREATED, response_model=AppointmentOut)
async def create_appointment(
    body: NewAppointment, session: SessionDep, agenda: AgendaDep
) -> AppointmentOut | JSONResponse:
    booking = Booking(
        patient_id=body.patient_id,
        service_id=body.service_id,
        starts_at=body.starts_at,
        provider_id=body.provider_id,
    )
    try:
        appointment = await agenda.create(session.clinic_id, booking, actor=_actor(session))
    except AgendaError as refusal:
        return _refused(refusal)
    return AppointmentOut.of(appointment)


@router.patch("/{appointment_id}", response_model=AppointmentOut)
async def change_appointment(
    appointment_id: UUID, change: Annotated[Change, Body()], session: SessionDep, agenda: AgendaDep
) -> AppointmentOut | JSONResponse:
    actor = _actor(session)
    try:
        if isinstance(change, Reschedule):
            appointment = await agenda.move(
                session.clinic_id,
                appointment_id,
                change.starts_at,
                actor=actor,
                provider_id=change.provider_id,
            )
        elif isinstance(change, Cancel):
            appointment = await agenda.cancel(
                session.clinic_id, appointment_id, actor=actor, reason=change.reason
            )
        else:
            appointment = await agenda.record_outcome(
                session.clinic_id, appointment_id, attended=change.action == "attended", actor=actor
            )
    except AgendaError as refusal:
        return _refused(refusal)
    return AppointmentOut.of(appointment)


def _actor(session: PanelSession) -> Actor:
    return Actor("user", session.user_id)


def _refused(refusal: AgendaError) -> JSONResponse:
    if isinstance(refusal, SlotUnavailableError):
        alternatives = [SlotOut.of(slot).model_dump(mode="json") for slot in refusal.alternatives]
        return JSONResponse(
            {"detail": str(refusal), "alternatives": alternatives}, status_code=status.HTTP_409_CONFLICT
        )
    if isinstance(refusal, PatientBusyError):
        return JSONResponse(
            {"detail": str(refusal), "alternatives": []}, status_code=status.HTTP_409_CONFLICT
        )
    if isinstance(refusal, NotFoundError):
        return JSONResponse({"detail": str(refusal)}, status_code=status.HTTP_404_NOT_FOUND)
    if isinstance(refusal, InvalidChangeError):
        return JSONResponse({"detail": str(refusal)}, status_code=status.HTTP_422_UNPROCESSABLE_CONTENT)
    raise refusal
