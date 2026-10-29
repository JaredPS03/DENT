"""Backend settings, read from environment variables (or backend/.env in development).

The app refuses to start when a required variable is missing or invalid. The error names the
variables but never shows their values: Pydantic's own errors, and those of the parsers our
validators call, echo the input, secrets included, so they are replaced by SettingsError. Only
text written in this module (raised with `_problem`) is shown.
"""

import re
from enum import StrEnum
from functools import lru_cache
from typing import Annotated, Literal
from urllib.parse import urlsplit

import psycopg
from psycopg.conninfo import conninfo_to_dict
from pydantic import Field, SecretStr, ValidationError, ValidationInfo, field_validator
from pydantic_core import ErrorDetails, PydanticCustomError
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

E164_PATTERN = re.compile(r"^\+[1-9][0-9]{7,14}$")
# Outside development the connection to Postgres must be encrypted.
TLS_SSLMODES = frozenset({"require", "verify-ca", "verify-full"})
# An empty or trivial secret would make the webhook reject everything, or accept a guessable token.
MIN_SECRET_LENGTH = 8
# A port, or one per host: "5432" or "5432,6543".
PORT_PATTERN = re.compile(r"^[0-9]+(,[0-9]+)*$")
MALFORMED_DATABASE_URL = "it is not a valid Postgres URL; percent-encode special characters in the password"
# Error type of the problems described in this module's own words.
_OWN_PROBLEM = "axon_config"


def _problem(message: str) -> PydanticCustomError:
    """A configuration problem in our own words: the only error text SettingsError shows."""
    return PydanticCustomError(_OWN_PROBLEM, message)


class Environment(StrEnum):
    DEVELOPMENT = "development"
    STAGING = "staging"
    PRODUCTION = "production"


class SettingsError(RuntimeError):
    """Missing or invalid configuration. The message never contains values."""


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        frozen=True,
    )

    environment: Environment

    # Postgres as the axon_app role. In the cloud, through the Supabase pooler with the user
    # axon_app.<project-ref>; locally, the database started by `pnpm db:start`.
    database_url: SecretStr = Field(min_length=1)
    db_pool_max_size: int = Field(default=10, ge=1, le=50)

    # Meta app secret, used to validate X-Hub-Signature-256 on every webhook.
    whatsapp_app_secret: SecretStr = Field(min_length=MIN_SECRET_LENGTH)
    # Token Meta echoes when verifying the webhook subscription.
    whatsapp_verify_token: SecretStr = Field(min_length=MIN_SECRET_LENGTH)
    # System user access token for the Graph API.
    whatsapp_access_token: SecretStr = Field(min_length=MIN_SECRET_LENGTH)
    # Graph API version, for example v24.0. No default: Meta retires versions, so it is set on purpose.
    meta_graph_version: str = Field(pattern=r"^v\d+\.\d+$")

    # Outside production, messages can only be sent to these numbers (E.164, comma separated).
    whatsapp_allowed_test_numbers: Annotated[tuple[str, ...], NoDecode] = ()
    # Phone number id that `app.cli send-test-message` sends from (Meta's test number).
    whatsapp_test_phone_number_id: str | None = None

    # Supabase project URL, for the public keys that verify panel sessions (JWKS). Locally,
    # the one `pnpm db:start` prints (http://127.0.0.1:54321).
    supabase_url: str = Field(min_length=1)

    # Temporary echo bot of milestone 1B: answers "Eco: …" to test the round trip with Meta's test
    # number. Never in production.
    echo_bot_enabled: bool = False

    # Voice notes to text (OpenAI gpt-transcribe, which does not train on the audio nor keep it;
    # decision 006). Without a key, audios are stored but not transcribed.
    transcription_api_key: SecretStr | None = None
    transcription_model: str = Field(default="gpt-transcribe", pattern=r"^[a-z0-9][a-z0-9.-]{0,63}$")

    # Supabase Storage through its S3 protocol, for the private bucket of audios and photos. These
    # keys reach Storage only, not the database (decision 006). Locally, `pnpm supabase status`
    # prints them.
    storage_s3_endpoint: str | None = None
    storage_s3_region: str = Field(default="local", pattern=r"^[a-z0-9-]{1,32}$")
    storage_s3_access_key_id: SecretStr | None = None
    storage_s3_secret_access_key: SecretStr | None = None

    # Jobs one worker handles at once.
    worker_concurrency: int = Field(default=4, ge=1, le=32)

    sentry_dsn: SecretStr | None = None
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"

    @field_validator("database_url")
    @classmethod
    def _validate_database_url(cls, value: SecretStr, info: ValidationInfo) -> SecretStr:
        # Read with libpq's own parser, so the check sees exactly what the pool will use. Its
        # errors quote the URL, password included: a malformed URL that reached the pool would
        # be logged on every connection attempt.
        try:
            params = conninfo_to_dict(value.get_secret_value())
        except (psycopg.Error, UnicodeError):
            raise _problem(MALFORMED_DATABASE_URL) from None
        # Unencoded symbols in the password parse "fine" but wrong, and the pool then logs the
        # misplaced piece on every attempt: after a / libpq reads a port (and, when it is a number,
        # a database name), after a second @ a host.
        port = params.get("port")
        if port is not None and not PORT_PATTERN.fullmatch(str(port)):
            raise _problem(MALFORMED_DATABASE_URL)
        if any("@" in str(params.get(key) or "") for key in ("host", "hostaddr", "dbname")):
            raise _problem(MALFORMED_DATABASE_URL)
        environment = info.data.get("environment")
        if environment not in (None, Environment.DEVELOPMENT) and params.get("sslmode") not in TLS_SSLMODES:
            raise _problem("outside development it needs sslmode=require (or verify-ca / verify-full)")
        return value

    @field_validator("supabase_url")
    @classmethod
    def _validate_supabase_url(cls, value: str, info: ValidationInfo) -> str:
        parts = urlsplit(value)
        if parts.scheme not in ("http", "https") or not parts.hostname or parts.query or parts.fragment:
            raise _problem("it must look like https://<project-ref>.supabase.co")
        if info.data.get("environment") not in (None, Environment.DEVELOPMENT) and parts.scheme != "https":
            raise _problem("outside development it must use https")
        return value.rstrip("/")

    @field_validator("whatsapp_allowed_test_numbers", mode="before")
    @classmethod
    def _split_numbers(cls, value: object) -> object:
        if isinstance(value, str):
            return tuple(part.strip() for part in value.split(",") if part.strip())
        return value

    @field_validator("whatsapp_allowed_test_numbers")
    @classmethod
    def _validate_numbers(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        for number in value:
            if not E164_PATTERN.fullmatch(number):
                # The number itself is not echoed back.
                raise _problem("every number must be in E.164 format, for example +5295...")
        return value

    @field_validator("whatsapp_test_phone_number_id", mode="before")
    @classmethod
    def _empty_as_none(cls, value: object) -> object:
        return None if value == "" else value

    @field_validator(
        "sentry_dsn",
        "transcription_api_key",
        "storage_s3_endpoint",
        "storage_s3_access_key_id",
        "storage_s3_secret_access_key",
        mode="before",
    )
    @classmethod
    def _empty_as_unset(cls, value: object) -> object:
        return None if value == "" else value

    @field_validator("echo_bot_enabled")
    @classmethod
    def _no_echo_in_production(cls, value: bool, info: ValidationInfo) -> bool:
        if value and info.data.get("environment") is Environment.PRODUCTION:
            raise _problem("the echo bot is for testing; it cannot run in production")
        return value

    @field_validator("storage_s3_endpoint")
    @classmethod
    def _validate_storage_endpoint(cls, value: str | None, info: ValidationInfo) -> str | None:
        if value is None:
            return None
        parts = urlsplit(value)
        if parts.scheme not in ("http", "https") or not parts.hostname or parts.query or parts.fragment:
            raise _problem("it must look like https://<project-ref>.storage.supabase.co/storage/v1/s3")
        if info.data.get("environment") not in (None, Environment.DEVELOPMENT) and parts.scheme != "https":
            raise _problem("outside development it must use https")
        return value.rstrip("/")

    @property
    def media_storage_configured(self) -> bool:
        return bool(
            self.storage_s3_endpoint and self.storage_s3_access_key_id and self.storage_s3_secret_access_key
        )


def load_settings(env_file: str | None = ".env") -> Settings:
    """Builds the settings or raises SettingsError listing the problems without any value."""
    try:
        return Settings(_env_file=env_file)
    except ValidationError as exc:
        problems = sorted({_describe(error) for error in exc.errors(include_input=False, include_url=False)})
        raise SettingsError("Configuración incompleta o inválida: " + ", ".join(problems)) from None


def _describe(error: ErrorDetails) -> str:
    name = ".".join(str(part) for part in error["loc"]).upper()
    if error["type"] == "missing":
        return f"{name} (falta)"
    if error["type"] == _OWN_PROBLEM:
        return f"{name} ({error['msg']})"
    # Any other text (Pydantic's, or a library's ValueError inside a validator) can quote the value
    # or reveal details such as a secret's length: only the error type.
    return f"{name} ({error['type']})"


@lru_cache
def get_settings() -> Settings:
    return load_settings()
