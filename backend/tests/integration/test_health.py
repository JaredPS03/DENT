from fastapi.testclient import TestClient

from app.application import create_app
from tests.conftest import make_settings


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
