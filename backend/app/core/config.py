"""Backend settings, read from environment variables (or backend/.env in development).

The app refuses to start when a required variable is missing or invalid. The error names the
variables but never shows their values: Pydantic's own errors echo every input value,
secrets included, so they are replaced by SettingsError.
"""

import re
from enum import StrEnum
from functools import lru_cache
from typing import Annotated, Literal

from pydantic import Field, SecretStr, ValidationError, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

E164_PATTERN = re.compile(r"^\+[1-9][0-9]{7,14}$")


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
    database_url: SecretStr
    db_pool_max_size: int = Field(default=10, ge=1, le=50)

    # Meta app secret, used to validate X-Hub-Signature-256 on every webhook.
    whatsapp_app_secret: SecretStr
    # Token Meta echoes when verifying the webhook subscription.
    whatsapp_verify_token: SecretStr
    # System user access token for the Graph API.
    whatsapp_access_token: SecretStr
    # Graph API version, for example v24.0. No default: Meta retires versions, so it is set on purpose.
    meta_graph_version: str = Field(pattern=r"^v\d+\.\d+$")

    # Outside production, messages can only be sent to these numbers (E.164, comma separated).
    whatsapp_allowed_test_numbers: Annotated[tuple[str, ...], NoDecode] = ()
    # Phone number id that `app.cli send-test-message` sends from (Meta's test number).
    whatsapp_test_phone_number_id: str | None = None

    sentry_dsn: SecretStr | None = None
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"

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
        problems = sorted(
            {
                f"{'.'.join(str(part) for part in error['loc']).upper()} ({error['type']})"
                for error in exc.errors(include_input=False, include_url=False)
            }
        )
        raise SettingsError("Configuración incompleta o inválida: " + ", ".join(problems)) from None


@lru_cache
def get_settings() -> Settings:
    return load_settings()
