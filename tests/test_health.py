from types import SimpleNamespace
from unittest.mock import AsyncMock

from fastapi.testclient import TestClient

import app.main as main_module
from app.main import app


client = TestClient(app)


def test_health() -> None:
    response = client.get("/health")

    assert response.status_code == 200
    assert response.json()["status"] == "degraded"
    assert response.json()["database"] is False


def test_readiness_fails_when_database_is_not_configured() -> None:
    response = client.get("/ready")

    assert response.status_code == 503


def test_api_readiness_succeeds_when_database_probe_passes(monkeypatch) -> None:
    monkeypatch.setattr(
        main_module,
        "_database_is_healthy",
        AsyncMock(return_value=True),
    )
    monkeypatch.setattr(
        main_module,
        "get_settings",
        lambda: SimpleNamespace(service_role="api"),
    )

    response = client.get("/ready")

    assert response.status_code == 200
    assert response.json()["status"] == "ready"


def test_worker_readiness_fails_without_a_supervised_task(monkeypatch) -> None:
    monkeypatch.setattr(
        main_module,
        "_database_is_healthy",
        AsyncMock(return_value=True),
    )
    monkeypatch.setattr(
        main_module,
        "get_settings",
        lambda: SimpleNamespace(service_role="worker"),
    )
    if hasattr(app.state, "worker_runtime"):
        del app.state.worker_runtime

    response = client.get("/ready")

    assert response.status_code == 503
    assert response.json()["detail"] == "Ingestion worker task is not running"
