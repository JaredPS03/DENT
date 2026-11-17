"""`responder`: the AI's answer becomes one WhatsApp message (bot rules 6 and 8).

The AI's output is untrusted: the text, buttons and links are checked here, and a slot is only an
id that buscar_disponibilidad returned in this conversation. The code puts the slot's label as the
button title and its exact start (ISO 8601 with the clinic's offset) as the button id, so the AI
never writes a date the patient could book by mistake. Whatever breaks a rule goes back to the AI
with the reason, in Spanish, so it can fix it in its next call.
"""

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from urllib.parse import urlsplit
from uuid import UUID
from zoneinfo import ZoneInfo

from pydantic import BaseModel, ConfigDict, ValidationError

from app.whatsapp import outbound
from app.whatsapp.outbound import InvalidOutboundError, Option, Section

# The body of a message with buttons takes 1024 characters; bot rule: one short message.
MAX_REPLY_TEXT = outbound.MAX_BUTTONS_BODY
# Acepto carries the notice version: a tap on an older notice never counts for the current one.
CONSENT_ACCEPT_PREFIX = "consent:accept:"
CONSENT_DECLINE_ID = "consent:decline"
OPTION_ID_PREFIX = "opcion:"

# Only URL characters and no query or fragment: what follows ("?u=https://...", "|otro.com") is
# checked again as text, so a link of the clinic cannot carry another domain.
_LINK = re.compile(r"(?i)\b(?:https?://|www\.)[a-z0-9\-._~:/@!$&()*+,;=%]+")
# A bare domain ("bit.ly/pago", "pagos-sonrisa.com"): WhatsApp turns it into a link too.
_HOST = re.compile(r"(?i)(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,24}\b")
# A title glued to a name ("Dra.Laura") is not a domain: capitals as written, no path after it.
_TITLE_AND_NAME = re.compile(r"\b(?:Dra?|Sra?|Srta|Lic|Ing|Mtra?|Profa?)\.[A-ZÁÉÍÓÚÑ][a-záéíóúñ]+\b(?![./])")


def consent_accept_id(version: str) -> str:
    return CONSENT_ACCEPT_PREFIX + version


def consent_buttons(version: str) -> tuple[Option, Option]:
    return (Option(consent_accept_id(version), "Acepto"), Option(CONSENT_DECLINE_ID, "No acepto"))


@dataclass(frozen=True, slots=True)
class ConsentRequest:
    """The privacy notice that rides under the AI's answer while the patient has not accepted."""

    text: str
    version: str


class ReplyRejectedError(ValueError):
    """The answer breaks a rule; the message tells the AI what to change."""


@dataclass(frozen=True, slots=True)
class OfferedSlot:
    key: str
    service_id: UUID
    service_name: str
    starts_at: datetime
    label: str


@dataclass(frozen=True, slots=True)
class Reply:
    content: str  # what the patient reads, stored as the message's content
    payload: dict[str, Any]  # Graph API body, without the recipient
    # The slots put in front of the patient, by key and service: only these can be booked later.
    slot_keys: tuple[tuple[str, UUID], ...]


class _Responder(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    texto: str
    botones: list[str] = []
    horarios: list[str] = []
    lista: list[str] = []


def slot_button_id(slot: OfferedSlot, tz: ZoneInfo) -> str:
    return slot.starts_at.astimezone(tz).isoformat()


def build_reply(
    arguments: Mapping[str, Any],
    *,
    offered: Mapping[str, OfferedSlot],
    tz: ZoneInfo,
    allowed_link_hosts: frozenset[str],
    consent_request: ConsentRequest | None = None,
) -> Reply:
    """The message for `responder`'s arguments. With `consent_request` (a patient who has not
    accepted the privacy notice) the AI may only write text: the notice and its buttons go along."""
    try:
        answer = _Responder.model_validate(dict(arguments))
    except ValidationError:
        raise ReplyRejectedError(
            "responder recibe texto y, si hace falta, botones, horarios o lista (listas de textos)."
        ) from None
    body = answer.texto.strip()
    if not body:
        raise ReplyRejectedError("El texto no puede ir vacío.")
    if outbound.length(body) > MAX_REPLY_TEXT:
        raise ReplyRejectedError(f"El texto pasa de {MAX_REPLY_TEXT} caracteres: acórtalo.")
    _check_links(body, allowed_link_hosts)

    if consent_request is not None:
        if answer.botones or answer.horarios or answer.lista:
            raise ReplyRejectedError(
                "El paciente no ha aceptado el aviso de privacidad: responde solo con texto. "
                "El sistema agrega el aviso y sus botones."
            )
        message = f"{body}\n\n{consent_request.text}"
        buttons = consent_buttons(consent_request.version)
        return Reply(message, _checked(lambda: outbound.buttons(message, buttons)), ())

    slots = [_offered(key, offered) for key in answer.horarios]
    slot_options = [Option(slot_button_id(slot, tz), slot.label, _short(slot.service_name)) for slot in slots]
    keys = tuple((slot.key, slot.service_id) for slot in slots)

    if answer.lista:
        if answer.botones:
            raise ReplyRejectedError("Usa botones o lista, no los dos.")
        rows = [*slot_options, *_plain_options(answer.lista)]
        return Reply(body, _list(body, rows, only_slots=False), keys)

    choices = len(slot_options) + len(answer.botones)
    if choices == 0:
        return Reply(body, _checked(lambda: outbound.text(body)), keys)
    if choices <= outbound.MAX_BUTTONS:
        options = [Option(o.id, o.title) for o in slot_options] + _plain_options(answer.botones)
        return Reply(body, _checked(lambda: outbound.buttons(body, options)), keys)
    if slot_options and choices <= outbound.MAX_LIST_ROWS:
        rows = [*slot_options, *_plain_options(answer.botones)]
        return Reply(body, _list(body, rows, only_slots=not answer.botones), keys)
    raise ReplyRejectedError(
        f"Con más de {outbound.MAX_BUTTONS} opciones usa lista (hasta {outbound.MAX_LIST_ROWS})."
    )


def _offered(key: str, offered: Mapping[str, OfferedSlot]) -> OfferedSlot:
    slot = offered.get(key)
    if slot is None:
        raise ReplyRejectedError(
            f"El horario {key[:16]!r} no salió de buscar_disponibilidad en esta conversación: "
            "ofrece solo los ids que devolvió."
        )
    return slot


def _plain_options(titles: Sequence[str]) -> list[Option]:
    return [Option(f"{OPTION_ID_PREFIX}{index}", title.strip()) for index, title in enumerate(titles, 1)]


def _list(body: str, rows: Sequence[Option], *, only_slots: bool) -> dict[str, Any]:
    label, title = ("Ver horarios", "Horarios") if only_slots else ("Ver opciones", "Opciones")
    return _checked(lambda: outbound.option_list(body, label, [Section(title, rows)]))


def _short(text: str) -> str:
    limit = outbound.MAX_ROW_DESCRIPTION
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def _checked(build: Any) -> dict[str, Any]:
    try:
        payload: dict[str, Any] = build()
    except InvalidOutboundError as error:
        raise ReplyRejectedError(f"WhatsApp no acepta ese mensaje: {error}.") from None
    return payload


def _check_links(body: str, allowed_hosts: frozenset[str]) -> None:
    hosts = {
        (urlsplit(link if "://" in link else f"https://{link}").hostname or "")
        for link in _LINK.findall(body)
    }
    # Bare domains, outside the links already read: "aviso.pdf" in a link's path is not a domain.
    rest = _TITLE_AND_NAME.sub(" ", _LINK.sub(" ", body))
    hosts |= {match.group(0) for match in _HOST.finditer(rest)}
    for host in hosts:
        if host.lower() not in allowed_hosts:
            raise ReplyRejectedError(
                "Ese enlace no es del consultorio: no incluyas enlaces ni dominios que no estén "
                "en <consultorio>."
            )
