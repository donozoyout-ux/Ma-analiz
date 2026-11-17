from types import SimpleNamespace

from fastapi.testclient import TestClient

from app.api import routes
from app.main import app
from app.core.config import Settings


def test_liveness_does_not_call_dependency_health_checks(monkeypatch):
    monkeypatch.setattr(routes, "health", lambda: (_ for _ in ()).throw(AssertionError("diagnostics called")))
    monkeypatch.setattr(routes, "provider_health", lambda: (_ for _ in ()).throw(AssertionError("provider called")))

    response = TestClient(app).get("/health/live")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_readiness_does_not_report_ok_when_database_is_unavailable(monkeypatch):
    settings = Settings(_env_file=None, database_url="postgresql://user:pass@example.invalid/db")
    monkeypatch.setattr(routes, "get_settings", lambda: settings)
    monkeypatch.setattr(routes, "engine", SimpleNamespace(connect=lambda: (_ for _ in ()).throw(RuntimeError("offline"))))
    monkeypatch.setattr(routes, "provider_health", lambda: {
        "provider": "UNKNOWN", "status": "PROVIDER_NOT_CONFIGURED", "coverage": {},
    })

    response = TestClient(app).get("/api/v1/health")
    payload = response.json()

    assert response.status_code == 200
    assert payload["database"] == "UNAVAILABLE"
    assert payload["status"] == "degraded"
