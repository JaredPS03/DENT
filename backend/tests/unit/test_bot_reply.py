"""`responder`: what the AI hands over becomes one valid WhatsApp message, or is sent back to it
with the reason. Slots only by the ids buscar_disponibilidad returned (bot rule 4, in code)."""

from datetime import UTC, datetime
from typing import Any
from uuid import uuid4
from zoneinfo import ZoneInfo

import pytest

from app.bot.reply import (
    MAX_REPLY_TEXT,
    ConsentRequest,
    OfferedSlot,
    ReplyRejectedError,
    build_reply,
    consent_buttons,
    slot_button_id,
)

TZ = ZoneInfo("America/Mexico_City")
CLEANING = uuid4()
HOSTS = frozenset({"maps.app.goo.gl", "consultorio.example"})


def slot(key: str, hour: int, label: str, service: str = "Limpieza") -> OfferedSlot:
    return OfferedSlot(
        key=key,
        service_id=CLEANING,
        service_name=service,
        starts_at=datetime(2026, 11, 19, hour, 0, tzinfo=UTC),
        label=label,
    )


OFFERED = {
    s.key: s
    for s in [
        slot("aaaa2222", 16, "Jue 10:00 am"),
        slot("bbbb2222", 17, "Jue 11:00 am"),
        slot("cccc2222", 18, "Jue 12:00 pm"),
        slot("dddd2222", 22, "Jue 4:00 pm"),
        slot("eeee2222", 23, "Jue 5:00 pm"),
    ]
}


def reply(**arguments: Any) -> Any:
    return build_reply({"texto": "Hola", **arguments}, offered=OFFERED, tz=TZ, allowed_link_hosts=HOSTS)


def test_plain_text_goes_as_a_text_message() -> None:
    message = reply(texto="Con gusto. ¿Qué día te acomoda?")
    assert message.payload == {
        "type": "text",
        "text": {"body": "Con gusto. ¿Qué día te acomoda?", "preview_url": False},
    }
    assert message.content == "Con gusto. ¿Qué día te acomoda?"
    assert message.slot_keys == ()


def test_up_to_three_slots_become_buttons_with_label_and_exact_start() -> None:
    message = reply(texto="Tengo estos:", horarios=["aaaa2222", "dddd2222"])
    buttons = message.payload["interactive"]["action"]["buttons"]
    assert [b["reply"]["title"] for b in buttons] == ["Jue 10:00 am", "Jue 4:00 pm"]
    assert [b["reply"]["id"] for b in buttons] == ["2026-11-19T10:00:00-06:00", "2026-11-19T16:00:00-06:00"]
    assert message.slot_keys == (("aaaa2222", CLEANING), ("dddd2222", CLEANING))


def test_slots_and_plain_buttons_share_the_three_places_slots_first() -> None:
    message = reply(horarios=["aaaa2222", "bbbb2222"], botones=["Otro día"])
    buttons = message.payload["interactive"]["action"]["buttons"]
    assert [b["reply"]["title"] for b in buttons] == ["Jue 10:00 am", "Jue 11:00 am", "Otro día"]
    assert buttons[2]["reply"]["id"] == "opcion:1"


def test_more_than_three_slots_become_a_list_with_the_service() -> None:
    message = reply(horarios=list(OFFERED))
    interactive = message.payload["interactive"]
    assert interactive["type"] == "list"
    assert interactive["action"]["button"] == "Ver horarios"
    rows = interactive["action"]["sections"][0]["rows"]
    assert len(rows) == 5
    assert rows[0] == {"id": "2026-11-19T10:00:00-06:00", "title": "Jue 10:00 am", "description": "Limpieza"}


def test_a_list_of_plain_options() -> None:
    message = reply(lista=["Limpieza", "Valoración", "Resina", "Extracción"])
    action = message.payload["interactive"]["action"]
    assert action["button"] == "Ver opciones"
    assert [row["id"] for row in action["sections"][0]["rows"]] == [f"opcion:{n}" for n in range(1, 5)]


@pytest.mark.parametrize(
    ("arguments", "reason"),
    [
        ({"horarios": ["zzzz2222"]}, "buscar_disponibilidad"),
        ({"texto": "x" * (MAX_REPLY_TEXT + 1)}, "1024"),
        ({"texto": "   "}, "vacío"),
        ({"botones": ["Quiero agendar mi cita"]}, "20"),
        ({"botones": ["Uno", "Dos", "Tres", "Cuatro"]}, "lista"),
        ({"botones": ["Sí"], "lista": ["Uno", "Dos"]}, "lista"),
        ({"horarios": ["aaaa2222", "aaaa2222"]}, "repiten"),
        ({"texto": "Pásate a https://descuentos.example/promo"}, "enlace"),
        ({"texto": "Mira www.otro-sitio.example"}, "enlace"),
        ({"texto": "Paga tu anticipo en bit.ly/pago-sonrisa"}, "enlace"),
        ({"texto": "Entra a pagos-sonrisa.com/pagar"}, "enlace"),
        ({"texto": "Escríbenos a citas@otro-correo.mx"}, "enlace"),
    ],
)
def test_what_breaks_the_rules_goes_back_to_the_ai_with_the_reason(
    arguments: dict[str, Any], reason: str
) -> None:
    with pytest.raises(ReplyRejectedError, match=reason):
        reply(**arguments)


def test_links_of_the_clinic_are_allowed() -> None:
    message = reply(texto="Estamos aquí: https://maps.app.goo.gl/abc123")
    assert message.payload["type"] == "text"
    assert reply(texto="O búscanos como maps.app.goo.gl/abc123").payload["type"] == "text"


@pytest.mark.parametrize(
    "text",
    ["Te esperamos a las 9:00 a.m. con la Dra. Laura.", "Son 1.5 horas aprox.", "Abrimos de 4.30 pm a 7 pm."],
)
def test_ordinary_text_with_dots_is_not_taken_for_a_link(text: str) -> None:
    assert reply(texto=text).payload["type"] == "text"


def test_without_consent_the_notice_rides_on_the_same_message() -> None:
    message = build_reply(
        {"texto": "Abrimos de lunes a viernes de 9 a 7."},
        offered=OFFERED,
        tz=TZ,
        allowed_link_hosts=HOSTS,
        consent_request=ConsentRequest(
            "Para agendar necesito tu autorización: https://consultorio.example/aviso. ¿Aceptas?", "2026-10"
        ),
    )
    interactive = message.payload["interactive"]
    assert interactive["body"]["text"] == (
        "Abrimos de lunes a viernes de 9 a 7.\n\n"
        "Para agendar necesito tu autorización: https://consultorio.example/aviso. ¿Aceptas?"
    )
    assert [b["reply"]["id"] for b in interactive["action"]["buttons"]] == [
        o.id for o in consent_buttons("2026-10")
    ]


def test_without_consent_the_ai_cannot_offer_slots_or_options() -> None:
    with pytest.raises(ReplyRejectedError, match="aviso"):
        build_reply(
            {"texto": "Tengo estos:", "horarios": ["aaaa2222"]},
            offered=OFFERED,
            tz=TZ,
            allowed_link_hosts=HOSTS,
            consent_request=ConsentRequest("¿Aceptas?", "2026-10"),
        )


def test_a_slot_button_id_is_the_exact_start_in_the_clinic_zone() -> None:
    assert slot_button_id(OFFERED["eeee2222"], TZ) == "2026-11-19T17:00:00-06:00"
