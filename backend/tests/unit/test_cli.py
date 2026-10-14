import pytest

from app import cli
from app.core.config import SettingsError
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
