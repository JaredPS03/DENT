"""Backend settings, read from environment variables (or backend/.env in development).

The app refuses to start when a required variable is missing or invalid. The error names the
variables but never shows their values: Pydantic's own errors echo every input value,
secrets included, so they are replaced by SettingsError.
"""

import re
from enum import StrEnum
from functools import lru_cache
from typing import Annotated, Literal
from urllib.parse import parse_qs, urlsplit

from pydantic import Field, SecretStr, ValidationError, ValidationInfo, field_validator
from pydantic_core import ErrorDetails
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

E164_PATTERN = re.compile(r"^\+[1-9][0-9]{7,14}$")
# Outside development the connection to Postgres must be encrypted.
TLS_SSLMODES = frozenset({"require", "verify-ca", "verify-full"})
# An empty or trivial secret would make the webhook reject everything, or accept a guessable token.
MIN_SECRET_LENGTH = 8


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

    sentry_dsn: SecretStr | None = None
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"

    @field_validator("database_url")
    @classmethod
    def _require_tls_outside_development(cls, value: SecretStr, info: ValidationInfo) -> SecretStr:
        if info.data.get("environment") in (None, Environment.DEVELOPMENT):
            return value
        query = parse_qs(urlsplit(value.get_secret_value()).query)
        if (query.get("sslmode") or [""])[-1] not in TLS_SSLMODES:
            raise ValueError("outside development it needs sslmode=require (or verify-ca / verify-full)")
        return value

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
                raise ValueError("every number must be in E.164 format, for example +5295...")
        return value

    @field_validator("whatsapp_test_phone_number_id", mode="before")
    @classmethod
    def _empty_as_none(cls, value: object) -> object:
        return None if value == "" else value

    @field_validator("sentry_dsn", mode="before")
    @classmethod
    def _empty_dsn_as_none(cls, value: object) -> object:
        return None if value == "" else value


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
    if error["type"] == "value_error":
        # Our own validators' text never contains the value.
        return f"{name} ({error['msg'].removeprefix('Value error, ')})"
    # Pydantic's other messages can reveal details such as a secret's length: only the error type.
    return f"{name} ({error['type']})"


@lru_cache
def get_settings() -> Settings:
    return load_settings()
