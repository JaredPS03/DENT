import asyncio
import hashlib
import hmac
import json
import os
import sys
from pathlib import Path
from typing import Any
from uuid import UUID

import pytest

from app.core.config import Environment, Settings

if sys.platform == "win32":
    # psycopg's async mode does not work with Windows' default Proactor event loop.
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())  # type: ignore[attr-defined,unused-ignore]

# Local Supabase started with `pnpm db:start`; the password is the local-only one from supabase/seed.sql.
LOCAL_DATABASE_URL = "postgresql://axon_app:axon_app_local@127.0.0.1:54322/postgres"

TEST_APP_SECRET = "test-app-secret"
TEST_VERIFY_TOKEN = "test-verify-token"
ALLOWED_TEST_NUMBER = "+529510000101"

# Fixed ids from supabase/seed.sql.
CLINIC_A = UUID("a0000000-0000-4000-8000-000000000001")
CLINIC_B = UUID("b0000000-0000-4000-8000-000000000001")
PHONE_NUMBER_ID_A = "100000000000001"
PHONE_NUMBER_ID_B = "200000000000002"

INTEGRATION_DIR = Path(__file__).parent / "integration"


def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    for item in items:
        if INTEGRATION_DIR in Path(str(item.fspath)).parents:
            item.add_marker(pytest.mark.integration)


def make_settings(**overrides: Any) -> Settings:
    values: dict[str, Any] = {
        "environment": Environment.DEVELOPMENT,
        "database_url": os.environ.get("TEST_DATABASE_URL", LOCAL_DATABASE_URL),
        "whatsapp_app_secret": TEST_APP_SECRET,
        "whatsapp_verify_token": TEST_VERIFY_TOKEN,
        "whatsapp_access_token": "test-access-token",
        "meta_graph_version": "v24.0",
        "whatsapp_allowed_test_numbers": (ALLOWED_TEST_NUMBER,),
        "whatsapp_test_phone_number_id": "123456789012345",
        "supabase_url": "https://axon-test.supabase.co",
        "sentry_dsn": None,
    }
    values.update(overrides)
    if values["environment"] != Environment.DEVELOPMENT and "database_url" not in overrides:
        # Outside development the connection must be encrypted.
        values["database_url"] += "?sslmode=require"
    return Settings(_env_file=None, **values)


def sign(body: bytes, secret: str = TEST_APP_SECRET) -> str:
    return "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


def message_change(phone_number_id: str, wamid: str, text: str = "Hola, quiero una cita") -> dict[str, Any]:
    """One change with a text message, shaped like Meta's documented webhook example."""
    return {
        "value": {
            "messaging_product": "whatsapp",
            "metadata": {"display_phone_number": "529510000001", "phone_number_id": phone_number_id},
            "contacts": [{"profile": {"name": "Paciente Prueba"}, "wa_id": "529510000101"}],
            "messages": [
                {
                    "from": "529510000101",
                    "id": wamid,
                    "timestamp": "1760400000",
                    "text": {"body": text},
                    "type": "text",
                }
            ],
        },
        "field": "messages",
    }


def status_change(phone_number_id: str, wamid: str, status: str) -> dict[str, Any]:
    return {
        "value": {
            "messaging_product": "whatsapp",
            "metadata": {"display_phone_number": "529510000001", "phone_number_id": phone_number_id},
            "statuses": [
                {"id": wamid, "status": status, "timestamp": "1760400000", "recipient_id": "529510000101"}
            ],
        },
        "field": "messages",
    }


def webhook_payload(*changes: dict[str, Any], waba_id: str = "900000000000001") -> dict[str, Any]:
    return {"object": "whatsapp_business_account", "entry": [{"id": waba_id, "changes": list(changes)}]}


def to_body(payload: dict[str, Any]) -> bytes:
    return json.dumps(payload).encode()


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture
def settings() -> Settings:
    return make_settings()
