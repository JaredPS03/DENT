"""Integration tests run against the local Supabase database (`pnpm db:start` at the repo root)."""

from collections.abc import Iterator
from typing import Any
from uuid import UUID

import psycopg
import pytest
from fastapi.testclient import TestClient

from app.application import create_app
from tests.conftest import LOCAL_DATABASE_URL, make_settings


@pytest.fixture(scope="session")
def database_url() -> str:
    url = make_settings().database_url.get_secret_value()
    try:
        psycopg.connect(url, connect_timeout=3).close()
    except psycopg.OperationalError:
        hint = (
            ""
            if url != LOCAL_DATABASE_URL
            else " En la raíz del repo corre `pnpm db:start` y vuelve a intentar."
        )
        pytest.fail("La base de datos de pruebas no responde." + hint, pytrace=False)
    return url


@pytest.fixture
def client(database_url: str) -> Iterator[TestClient]:
    app = create_app(make_settings(database_url=database_url))
    with TestClient(app) as test_client:
        yield test_client


def rows_as_clinic(
    database_url: str, clinic_id: UUID, sql: str, params: tuple[Any, ...] = ()
) -> list[tuple[Any, ...]]:
    """Reads like the backend does: as axon_app, with the clinic fixed in the transaction."""
    with psycopg.connect(database_url, autocommit=True) as conn, conn.transaction():
        conn.execute("select set_config('app.clinic_id', %s, true)", (str(clinic_id),))
        return conn.execute(sql, params).fetchall()
