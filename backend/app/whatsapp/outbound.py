"""Messages Axon sends, as Graph API bodies (without the recipient), validated against Meta's
limits before they are queued: a message Meta would reject is refused here, with a reason.

Limits from Meta's text, reply buttons and list messages references.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

MAX_TEXT = 4096
MAX_BUTTONS = 3
MAX_BUTTONS_BODY = 1024
MAX_BUTTON_TITLE = 20
MAX_BUTTON_ID = 256
MAX_LIST_BODY = 4096
MAX_LIST_BUTTON = 20
MAX_LIST_ROWS = 10
MAX_LIST_SECTIONS = 10
MAX_SECTION_TITLE = 24
MAX_ROW_TITLE = 24
MAX_ROW_DESCRIPTION = 72
MAX_ROW_ID = 200
MAX_TEMPLATE_PARAMETER = 1024


class InvalidOutboundError(ValueError):
    """The message breaks one of Meta's limits."""


@dataclass(frozen=True, slots=True)
class Option:
    id: str
    title: str
    description: str | None = None


@dataclass(frozen=True, slots=True)
class Section:
    title: str
    rows: Sequence[Option]


def text(body: str) -> dict[str, Any]:
    _check_length("el texto", body, MAX_TEXT)
    # No link previews: they fetch whatever URL a message contains.
    return {"type": "text", "text": {"body": body, "preview_url": False}}


def buttons(body: str, options: Sequence[Option]) -> dict[str, Any]:
    _check_length("el texto de un mensaje con botones", body, MAX_BUTTONS_BODY)
    if not 1 <= len(options) <= MAX_BUTTONS:
        raise InvalidOutboundError(f"un mensaje lleva de 1 a {MAX_BUTTONS} botones")
    _check_unique([option.title for option in options], "títulos de botón")
    _check_unique([option.id for option in options], "ids de botón")
    for option in options:
        _check_length("el título de un botón", option.title, MAX_BUTTON_TITLE)
        _check_length("el id de un botón", option.id, MAX_BUTTON_ID)
    return {
        "type": "interactive",
        "interactive": {
            "type": "button",
            "body": {"text": body},
            "action": {
                "buttons": [{"type": "reply", "reply": {"id": o.id, "title": o.title}} for o in options]
            },
        },
    }


def option_list(body: str, button_label: str, sections: Sequence[Section]) -> dict[str, Any]:
    _check_length("el texto de una lista", body, MAX_LIST_BODY)
    _check_length("el botón de una lista", button_label, MAX_LIST_BUTTON)
    if not 1 <= len(sections) <= MAX_LIST_SECTIONS:
        raise InvalidOutboundError(f"una lista lleva de 1 a {MAX_LIST_SECTIONS} secciones")
    rows = [row for section in sections for row in section.rows]
    if not 1 <= len(rows) <= MAX_LIST_ROWS:
        raise InvalidOutboundError(f"una lista lleva de 1 a {MAX_LIST_ROWS} opciones en total")
    _check_unique([row.id for row in rows], "ids de opción")
    for section in sections:
        _check_length("el título de una sección", section.title, MAX_SECTION_TITLE)
    for row in rows:
        _check_length("el título de una opción", row.title, MAX_ROW_TITLE)
        _check_length("el id de una opción", row.id, MAX_ROW_ID)
        if row.description is not None:
            _check_length("la descripción de una opción", row.description, MAX_ROW_DESCRIPTION)
    return {
        "type": "interactive",
        "interactive": {
            "type": "list",
            "body": {"text": body},
            "action": {
                "button": button_label,
                "sections": [
                    {
                        "title": section.title,
                        "rows": [_row(row) for row in section.rows],
                    }
                    for section in sections
                ],
            },
        },
    }


def template(name: str, language: str, body_parameters: Sequence[str] = ()) -> dict[str, Any]:
    if not name or not name.replace("_", "").isalnum() or not name.isascii() or name != name.lower():
        raise InvalidOutboundError("el nombre de la plantilla no es válido")
    message: dict[str, Any] = {"name": name, "language": {"code": language}}
    if body_parameters:
        for value in body_parameters:
            # Meta rejects empty parameters, and new lines or tabs inside them.
            if not value.strip() or any(char in value for char in "\n\t"):
                raise InvalidOutboundError(
                    "un parámetro de plantilla no puede ir vacío ni con saltos de línea"
                )
            _check_length("un parámetro de plantilla", value, MAX_TEMPLATE_PARAMETER)
        message["components"] = [
            {"type": "body", "parameters": [{"type": "text", "text": value} for value in body_parameters]}
        ]
    return {"type": "template", "template": message}


def _row(row: Option) -> dict[str, str]:
    data = {"id": row.id, "title": row.title}
    if row.description is not None:
        data["description"] = row.description
    return data


def _check_length(what: str, value: str, limit: int) -> None:
    if not value or not value.strip():
        raise InvalidOutboundError(f"{what} no puede ir vacío")
    if _length(value) > limit:
        raise InvalidOutboundError(f"{what} pasa de {limit} caracteres")


def _length(value: str) -> int:
    """Length as WhatsApp's clients count it (UTF-16 units): an emoji may count as two."""
    return len(value.encode("utf-16-le")) // 2


def _check_unique(values: list[str], what: str) -> None:
    if len(set(values)) != len(values):
        raise InvalidOutboundError(f"los {what} se repiten")
