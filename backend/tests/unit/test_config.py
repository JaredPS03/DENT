import pytest

from app.core.config import Environment, SettingsError, load_settings
from tests.conftest import make_settings

REQUIRED_ENV = {
    "ENVIRONMENT": "development",
    "DATABASE_URL": "postgresql://axon_app:super-secreto-db@127.0.0.1:54322/postgres",
    "WHATSAPP_APP_SECRET": "super-secreto-app",
    "WHATSAPP_VERIFY_TOKEN": "super-secreto-verify",
    "WHATSAPP_ACCESS_TOKEN": "super-secreto-token",
    "META_GRAPH_VERSION": "v24.0",
}


@pytest.fixture
def clean_env(monkeypatch: pytest.MonkeyPatch) -> pytest.MonkeyPatch:
    for name in (
        *REQUIRED_ENV,
        "WHATSAPP_ALLOWED_TEST_NUMBERS",
        "WHATSAPP_TEST_PHONE_NUMBER_ID",
        "SENTRY_DSN",
    ):
        monkeypatch.delenv(name, raising=False)
    return monkeypatch


def test_settings_load_from_the_environment(clean_env: pytest.MonkeyPatch) -> None:
    for name, value in REQUIRED_ENV.items():
        clean_env.setenv(name, value)
    clean_env.setenv("WHATSAPP_ALLOWED_TEST_NUMBERS", "+529510000101, +529510000102")
    clean_env.setenv("SENTRY_DSN", "")

    settings = load_settings(env_file=None)

    assert settings.environment is Environment.DEVELOPMENT
    assert settings.whatsapp_allowed_test_numbers == ("+529510000101", "+529510000102")
    assert settings.sentry_dsn is None


def test_missing_variables_stop_startup_naming_them_without_values(clean_env: pytest.MonkeyPatch) -> None:
    for name, value in REQUIRED_ENV.items():
        if name != "WHATSAPP_APP_SECRET":
            clean_env.setenv(name, value)

    with pytest.raises(SettingsError) as error:
        load_settings(env_file=None)

    message = str(error.value)
    assert "WHATSAPP_APP_SECRET" in message
    assert "super-secreto" not in message
    assert error.value.__cause__ is None
    assert error.value.__suppress_context__


def test_invalid_values_are_reported_without_echoing_them(clean_env: pytest.MonkeyPatch) -> None:
    for name, value in REQUIRED_ENV.items():
        clean_env.setenv(name, value)
    clean_env.setenv("META_GRAPH_VERSION", "24")
    clean_env.setenv("WHATSAPP_ALLOWED_TEST_NUMBERS", "9511234567")

    with pytest.raises(SettingsError) as error:
        load_settings(env_file=None)

    message = str(error.value)
    assert "META_GRAPH_VERSION" in message
    assert "WHATSAPP_ALLOWED_TEST_NUMBERS" in message
    assert "9511234567" not in message


def test_secrets_never_show_in_repr() -> None:
    settings = make_settings(whatsapp_app_secret="muy-secreto")
    assert "muy-secreto" not in repr(settings)
    assert "muy-secreto" not in str(settings.model_dump())


@pytest.mark.parametrize("name", ["WHATSAPP_APP_SECRET", "WHATSAPP_VERIFY_TOKEN", "WHATSAPP_ACCESS_TOKEN"])
def test_empty_or_trivial_secrets_stop_startup(clean_env: pytest.MonkeyPatch, name: str) -> None:
    for env_name, value in REQUIRED_ENV.items():
        clean_env.setenv(env_name, value)
    clean_env.setenv(name, "abc")

    with pytest.raises(SettingsError) as error:
        load_settings(env_file=None)

    assert name in str(error.value)
    assert "3" not in str(error.value)  # not even the secret's length


@pytest.mark.parametrize("environment", ["staging", "production"])
def test_outside_development_the_database_connection_must_be_encrypted(
    clean_env: pytest.MonkeyPatch, environment: str
) -> None:
    for name, value in REQUIRED_ENV.items():
        clean_env.setenv(name, value)
    clean_env.setenv("ENVIRONMENT", environment)

    with pytest.raises(SettingsError) as error:
        load_settings(env_file=None)
    assert "DATABASE_URL" in str(error.value)
    assert "sslmode=require" in str(error.value)
    assert "super-secreto" not in str(error.value)

    clean_env.setenv("DATABASE_URL", REQUIRED_ENV["DATABASE_URL"] + "?sslmode=require")
    assert load_settings(env_file=None).environment.value == environment


def test_development_does_not_require_tls(clean_env: pytest.MonkeyPatch) -> None:
    for name, value in REQUIRED_ENV.items():
        clean_env.setenv(name, value)
    assert load_settings(env_file=None).environment.value == "development"
