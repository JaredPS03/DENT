import base64

import pytest

from app import cli
from app.core.config import Environment, SettingsError
from app.whatsapp.client import SentMessage
from tests.conftest import make_settings


def test_send_test_message_refuses_numbers_outside_the_allowlist(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(cli, "load_settings", make_settings)

    code = cli.main(["send-test-message", "--to", "+529510000999"])

    assert code == 2
    output = capsys.readouterr().out
    assert "No se envió" in output
    assert "+529510000999" not in output


def test_send_test_message_explains_missing_configuration(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    def broken() -> None:
        raise SettingsError("Configuración incompleta o inválida: WHATSAPP_ACCESS_TOKEN (missing)")

    monkeypatch.setattr(cli, "load_settings", broken)

    assert cli.main(["send-test-message", "--to", "+529510000101"]) == 2
    assert "WHATSAPP_ACCESS_TOKEN" in capsys.readouterr().out


def test_send_test_message_needs_a_sender_number(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(cli, "load_settings", lambda: make_settings(whatsapp_test_phone_number_id=None))

    assert cli.main(["send-test-message", "--to", "+529510000101"]) == 2
    assert "WHATSAPP_TEST_PHONE_NUMBER_ID" in capsys.readouterr().out


def test_send_test_message_never_runs_in_production(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(
        cli,
        "load_settings",
        lambda: make_settings(
            environment=Environment.PRODUCTION, database_url="postgresql://x/y?sslmode=require"
        ),
    )

    assert cli.main(["send-test-message", "--to", "+529510000101", "--text", "hola"]) == 2
    assert "producción" in capsys.readouterr().out


def _wamid_carrying(phone_digits: str) -> str:
    """Shaped like Meta's message ids: base64 of a small record that carries the phone number."""
    raw = b"\x1c\x18" + bytes([len(phone_digits)]) + phone_digits.encode() + b"\x15\x02\x00\x11\x18\x12"
    return "wamid." + base64.b64encode(raw + b"9A3CA5B3D4CD6E67E7\x00").decode()


def test_send_test_message_shows_only_the_tail_of_meta_s_id(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    wamid = _wamid_carrying("529510000101")  # fictitious number, as in the seed
    assert "529510000101" in base64.b64decode(wamid.removeprefix("wamid.")).decode("latin-1")

    class FakeClient:
        def __init__(self, *_: object) -> None:
            pass

        async def send_template(self, *_: object) -> SentMessage:
            return SentMessage(meta_message_id=wamid)

    monkeypatch.setattr(cli, "load_settings", make_settings)
    monkeypatch.setattr(cli, "WhatsAppClient", FakeClient)

    assert cli.main(["send-test-message", "--to", "+529510000101"]) == 0
    output = capsys.readouterr().out
    assert "***0101" in output
    assert wamid[-8:] in output
    assert wamid not in output
    assert wamid[6:20] not in output  # the part that encodes the phone number
