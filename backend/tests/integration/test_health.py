import time

import pytest
from fastapi.testclient import TestClient

from app import health
from app.application import create_app
from tests.conftest import make_settings
from tests.integration.conftest import FreezableProxy


def test_health_reports_database_and_queue(client: TestClient) -> None:
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "checks": {"database": "ok", "queue": "ok"}}


def test_health_is_503_when_the_database_is_down() -> None:
    settings = make_settings(database_url="postgresql://axon_app:x@127.0.0.1:1/postgres?connect_timeout=1")
    with TestClient(create_app(settings)) as client:
        response = client.get("/health")
    assert response.status_code == 503
    assert response.json() == {"status": "degraded", "checks": {"database": "error", "queue": "error"}}
    assert "127.0.0.1" not in response.text


def test_health_answers_on_time_when_the_database_goes_silent(
    freezable_db: tuple[FreezableProxy, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    proxy, url = freezable_db
    monkeypatch.setattr(health, "CHECK_TIMEOUT_SECONDS", 1.0)
    with TestClient(create_app(make_settings(database_url=url))) as client:
        assert client.get("/health").status_code == 200
        proxy.frozen.set()
        started = time.monotonic()
        response = client.get("/health")
        elapsed = time.monotonic() - started
        proxy.close()
    assert response.status_code == 503
    assert elapsed < 2.0
