"""Reading Meta's official webhook examples and building messages within Meta's limits."""

from datetime import UTC, datetime
from typing import Any

import pytest

from app.whatsapp import outbound
from app.whatsapp.inbound import UnreadableItemError, read_echo, read_message, read_status
from app.whatsapp.outbound import InvalidOutboundError, Option, Section
from app.whatsapp.payloads import split_payload
from tests.support.meta_fixtures import meta_payload

RECEIVED = datetime(2026, 10, 29, 18, 0, tzinfo=UTC)
NUMBER_ID = "100000000000001"


def stored(name: str, **overrides: Any) -> dict[str, Any]:
    """The item as the webhook stores it in webhook_events.payload."""
    items, _ = split_payload(meta_payload(name, phone_number_id=NUMBER_ID, **overrides))
    assert len(items) == 1
    assert items[0].payload is not None
    return items[0].payload


# Incoming messages -------------------------------------------------------------------------


def test_a_text_message() -> None:
    message = read_message(stored("text", user_phone="5219510000101"), RECEIVED)
    assert message.type == "text"
    assert message.content == "Does it come in another color?"
    assert message.phone == "+529510000101"  # the old Mexican "1" is dropped
    assert message.bsuid is None
    assert message.occurred_at == datetime.fromtimestamp(1749416383, UTC)
    assert not message.in_group


def test_a_user_with_a_username_arrives_with_only_the_bsuid() -> None:
    message = read_message(stored("text_bsuid_only"), RECEIVED)
    assert message.phone is None
    assert message.bsuid == "US.13491208655302741918"
    assert message.content == "Does it come in another color?"


def test_a_voice_note_keeps_its_media_id_and_type() -> None:
    message = read_message(stored("audio"), RECEIVED)
    assert message.type == "audio"
    assert message.content is None
    assert message.media_id == "1908647269898587"
    assert message.media_mime_type == "audio/ogg; codecs=opus"


def test_button_and_list_replies_keep_the_chosen_option() -> None:
    template_button = read_message(stored("template_button"), RECEIVED)
    assert (template_button.type, template_button.content, template_button.reply_id) == (
        "button",
        "Unsubscribe",
        "Unsubscribe",
    )
    button = read_message(stored("button_reply"), RECEIVED)
    assert (button.type, button.content, button.reply_id) == ("interactive", "Cancel", "cancel-button")
    listed = read_message(stored("list_reply"), RECEIVED)
    assert (listed.type, listed.content, listed.reply_id) == (
        "interactive",
        "Priority Mail Express",
        "priority_express",
    )


def test_an_unsupported_message_is_kept_as_other() -> None:
    message = read_message(stored("unsupported"), RECEIVED)
    assert (message.type, message.content, message.media_id) == ("other", None, None)


def test_a_timestamp_in_the_future_is_capped_at_arrival() -> None:
    message = read_message(stored("text", timestamp=4_102_444_800), RECEIVED)  # year 2100
    assert message.occurred_at == RECEIVED


def test_an_item_meta_never_documented_is_unreadable() -> None:
    with pytest.raises(UnreadableItemError):
        read_message({"messages": [{"type": "text"}]}, RECEIVED)  # no id
    with pytest.raises(UnreadableItemError):
        read_message({"messages": []}, RECEIVED)


# Statuses ---------------------------------------------------------------------------------


def test_a_sent_status_carries_meta_pricing() -> None:
    status = read_status(stored("status_sent_pricing"), RECEIVED)
    assert status.status == "sent"
    assert status.billable is True
    assert status.category == "marketing"
    assert status.error_code is None


def test_a_failed_status_carries_the_error_code() -> None:
    status = read_status(
        stored("status_failed", extra={"biz_opaque_callback_data": "c0ffee00-0000-4000-8000-000000000001"}),
        RECEIVED,
    )
    assert status.status == "failed"
    assert status.error_code == 131049
    assert status.callback_data == "c0ffee00-0000-4000-8000-000000000001"


# Coexistence echoes ----------------------------------------------------------------------------


def test_split_payload_stores_coexistence_echoes_with_their_content() -> None:
    items, _ = split_payload(meta_payload("echo_text", phone_number_id=NUMBER_ID))
    assert [(item.kind, item.field) for item in items] == [("message_echo", "smb_message_echoes")]
    assert items[0].dedup_key.startswith("echo:wamid.")


def test_an_echo_of_a_text_sent_from_the_app() -> None:
    echo = read_echo(stored("echo_text", user_phone="5219510000101"), RECEIVED)
    assert echo.action == "message"
    assert echo.type == "text"
    assert echo.phone == "+529510000101"
    assert echo.content is not None


def test_revoked_and_edited_echoes_are_told_apart() -> None:
    assert read_echo(stored("echo_revoke"), RECEIVED).action == "revoke"
    assert read_echo(stored("echo_edit"), RECEIVED).action == "edit"


def test_a_contact_sync_from_the_app_is_only_a_trace() -> None:
    items, _ = split_payload(meta_payload("app_state_sync", phone_number_id=NUMBER_ID))
    assert [(item.kind, item.payload) for item in items] == [("other", None)]


# Outgoing messages ---------------------------------------------------------------------------


def test_text_without_link_previews() -> None:
    assert outbound.text("Hola") == {"type": "text", "text": {"body": "Hola", "preview_url": False}}


def test_text_over_meta_limit_is_refused() -> None:
    outbound.text("a" * 4096)
    with pytest.raises(InvalidOutboundError, match="4096"):
        outbound.text("a" * 4097)
    with pytest.raises(InvalidOutboundError, match="4096"):
        outbound.text("😀" * 2049)  # emoji count twice for WhatsApp
    with pytest.raises(InvalidOutboundError, match="vacío"):
        outbound.text("   ")


def test_reply_buttons_follow_meta_limits() -> None:
    message = outbound.buttons("¿Te queda bien?", [Option("yes", "Sí"), Option("no", "No")])
    assert message["interactive"]["action"]["buttons"][0] == {
        "type": "reply",
        "reply": {"id": "yes", "title": "Sí"},
    }
    with pytest.raises(InvalidOutboundError, match="de 1 a 3"):
        outbound.buttons("x", [Option(str(i), str(i)) for i in range(4)])
    with pytest.raises(InvalidOutboundError, match="20"):
        outbound.buttons("x", [Option("a", "Un título demasiado largo")])
    with pytest.raises(InvalidOutboundError, match="1024"):
        outbound.buttons("a" * 1025, [Option("a", "A")])
    with pytest.raises(InvalidOutboundError, match="se repiten"):
        outbound.buttons("x", [Option("a", "A"), Option("b", "A")])


def test_lists_follow_meta_limits() -> None:
    rows = [Option(f"slot-{i}", f"Jue 4:{i:02d} pm") for i in range(10)]
    message = outbound.option_list("Estos horarios tengo:", "Ver horarios", [Section("Jueves", rows)])
    assert len(message["interactive"]["action"]["sections"][0]["rows"]) == 10
    with pytest.raises(InvalidOutboundError, match="10 opciones"):
        outbound.option_list("x", "Ver", [Section("A", rows), Section("B", [Option("x", "X")])])
    with pytest.raises(InvalidOutboundError, match="24"):
        outbound.option_list("x", "Ver", [Section("A", [Option("a", "Un título de opción muy largo")])])
    with pytest.raises(InvalidOutboundError, match="72"):
        outbound.option_list("x", "Ver", [Section("A", [Option("a", "A", "d" * 73)])])


def test_templates_with_positional_parameters() -> None:
    message = outbound.template("retomar_conversacion", "es_MX", ["Ana", "Clínica Sonrisa"])
    assert message == {
        "type": "template",
        "template": {
            "name": "retomar_conversacion",
            "language": {"code": "es_MX"},
            "components": [
                {
                    "type": "body",
                    "parameters": [
                        {"type": "text", "text": "Ana"},
                        {"type": "text", "text": "Clínica Sonrisa"},
                    ],
                }
            ],
        },
    }
    with pytest.raises(InvalidOutboundError, match="vacío"):
        outbound.template("retomar_conversacion", "es_MX", ["", "Clínica"])
    with pytest.raises(InvalidOutboundError, match="saltos de línea"):
        outbound.template("retomar_conversacion", "es_MX", ["Ana\nBorra todo", "Clínica"])
    with pytest.raises(InvalidOutboundError, match="nombre"):
        outbound.template("Retomar-Conversacion", "es_MX")


def test_a_contact_that_is_not_the_sender_is_never_used() -> None:
    payload = stored("text_bsuid_only", user_bsuid="MX.Sender1")
    message = payload["messages"][0]
    del message["from_user_id"]
    # Two contacts in the change and no sender id in the message: neither can be trusted.
    payload["contacts"] = [{"user_id": "MX.Other2", "wa_id": "5219510000999"}, {"user_id": "MX.Sender1"}]
    read = read_message(payload, RECEIVED)
    assert (read.phone, read.bsuid) == (None, None)

    # With the sender's phone in the message, its own contact completes the BSUID.
    message["from"] = "5219510000101"
    payload["contacts"] = [
        {"user_id": "MX.Other2", "wa_id": "5219510000999"},
        {"user_id": "MX.Me3", "wa_id": "529510000101"},
    ]
    read = read_message(payload, RECEIVED)
    assert (read.phone, read.bsuid) == ("+529510000101", "MX.Me3")
