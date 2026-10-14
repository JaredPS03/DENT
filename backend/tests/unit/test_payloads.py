import pytest
from pydantic import ValidationError

from app.whatsapp.payloads import split_payload
from tests.conftest import message_change, status_change, webhook_payload


def test_a_text_message_becomes_one_item_with_its_original_json() -> None:
    raw = webhook_payload(message_change("111", "wamid.A1", text="Hola"))
    items, unroutable = split_payload(raw)

    assert unroutable == []
    assert len(items) == 1
    item = items[0]
    assert (item.phone_number_id, item.field, item.kind, item.meta_id) == (
        "111",
        "messages",
        "message",
        "wamid.A1",
    )
    assert item.dedup_key == "msg:wamid.A1"
    assert item.payload["messages"][0]["text"] == {"body": "Hola"}
    assert item.payload["metadata"]["phone_number_id"] == "111"
    assert item.payload["contacts"][0]["wa_id"] == "529510000101"


def test_each_status_of_a_message_is_its_own_item() -> None:
    raw = webhook_payload(
        status_change("111", "wamid.X", "sent"), status_change("111", "wamid.X", "delivered")
    )
    items, _ = split_payload(raw)
    assert [i.dedup_key for i in items] == ["status:wamid.X:sent", "status:wamid.X:delivered"]
    assert {i.kind for i in items} == {"status"}


def test_entries_of_different_numbers_are_split() -> None:
    raw = {
        "object": "whatsapp_business_account",
        "entry": [
            {"id": "waba-1", "changes": [message_change("111", "wamid.1")]},
            {
                "id": "waba-2",
                "changes": [message_change("222", "wamid.2"), status_change("222", "wamid.9", "read")],
            },
        ],
    }
    items, _ = split_payload(raw)
    assert [(i.phone_number_id, i.dedup_key) for i in items] == [
        ("111", "msg:wamid.1"),
        ("222", "msg:wamid.2"),
        ("222", "status:wamid.9:read"),
    ]


def test_a_batch_with_several_messages_keeps_each_one_alone() -> None:
    change = message_change("111", "wamid.1")
    second = dict(change["value"]["messages"][0], id="wamid.2")
    change["value"]["messages"].append(second)
    items, _ = split_payload(webhook_payload(change))
    assert [i.payload["messages"][0]["id"] for i in items] == ["wamid.1", "wamid.2"]


def test_account_level_changes_without_number_are_reported_not_routed() -> None:
    raw = webhook_payload(
        {
            "field": "message_template_status_update",
            "value": {"event": "APPROVED", "message_template_name": "x"},
        },
        waba_id="waba-9",
    )
    items, unroutable = split_payload(raw)
    assert items == []
    assert [(u.entry_id, u.field) for u in unroutable] == [("waba-9", "message_template_status_update")]


def test_unknown_change_for_a_known_number_is_kept_whole() -> None:
    change = {"field": "something_new", "value": {"metadata": {"phone_number_id": "111"}, "data": [1, 2]}}
    items, _ = split_payload(webhook_payload(change))
    assert len(items) == 1
    assert items[0].kind == "other"
    assert items[0].meta_id is None
    assert items[0].dedup_key.startswith("other:something_new:")
    assert items[0].payload == change["value"]
    # Same content, same key: a retry is deduplicated.
    again, _ = split_payload(webhook_payload(change))
    assert again[0].dedup_key == items[0].dedup_key


@pytest.mark.parametrize(
    "raw",
    [
        {"object": "page", "entry": []},
        {"entry": []},
        {"object": "whatsapp_business_account", "entry": "no"},
        webhook_payload({"field": "messages", "value": {"metadata": {"phone_number_id": 111}}}),
        webhook_payload(
            {"field": "messages", "value": {"metadata": {"phone_number_id": "111"}, "messages": [{}]}}
        ),
    ],
)
def test_payloads_that_are_not_whatsapp_webhooks_are_rejected(raw: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        split_payload(raw)
