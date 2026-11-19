from types import SimpleNamespace

from fastapi.testclient import TestClient

from app.jobs import production_readiness as ops
from app.main import app


client = TestClient(app)
TOKEN = "test-ops-token-do-not-use-in-production"


def _report(*, postgres="PASS", alembic="PASS", r2="PASS", goaloo="PASS",
            alembic_detail=None, secret=None):
    return {
        "postgres": postgres,
        "alembic": alembic,
        "r2": r2,
        "goaloo": goaloo,
        "alembic_details": {"status": alembic_detail or alembic},
        "postgres_details": {"password": secret},
        "r2_details": {"secret_access_key": secret},
        "goaloo_details": {"api_key": secret},
    }


def _post(monkeypatch, report=None):
    monkeypatch.setenv("PRODUCTION_OPS_TOKEN", TOKEN)
    monkeypatch.setattr(ops, "run_process_environment_readiness",
                        lambda: report or _report())
    return client.post("/api/v1/admin/production-readiness",
                       headers={"X-Production-Ops-Token": TOKEN})


def test_missing_configured_token_returns_503_and_does_not_run(monkeypatch):
    monkeypatch.delenv("PRODUCTION_OPS_TOKEN", raising=False)
    monkeypatch.setattr(ops, "run_process_environment_readiness",
                        lambda: (_ for _ in ()).throw(AssertionError("must not run")))
    response = client.post("/api/v1/admin/production-readiness")
    assert response.status_code == 503
    assert "PRODUCTION_OPS_TOKEN" not in response.text


def test_missing_or_wrong_request_token_returns_401(monkeypatch):
    monkeypatch.setenv("PRODUCTION_OPS_TOKEN", TOKEN)
    monkeypatch.setattr(ops, "run_process_environment_readiness",
                        lambda: (_ for _ in ()).throw(AssertionError("must not run")))
    missing = client.post("/api/v1/admin/production-readiness")
    wrong = client.post("/api/v1/admin/production-readiness",
                        headers={"X-Production-Ops-Token": "wrong"})
    assert missing.status_code == wrong.status_code == 401
    assert TOKEN not in missing.text + wrong.text


def test_endpoint_is_not_mounted_on_legacy_api_alias(monkeypatch):
    monkeypatch.setenv("PRODUCTION_OPS_TOKEN", TOKEN)
    response = client.post("/api/admin/production-readiness",
                           headers={"X-Production-Ops-Token": TOKEN})
    assert response.status_code == 404


def test_missing_postgres_returns_fail(monkeypatch):
    response = _post(monkeypatch, _report(postgres="FAIL"))
    assert response.status_code == 200
    assert response.json()["postgresql"] == "FAIL"
    assert response.json()["all_gates_pass"] is False


def test_missing_r2_returns_fail(monkeypatch):
    response = _post(monkeypatch, _report(r2="FAIL"))
    assert response.json()["r2"] == "FAIL"
    assert response.json()["all_gates_pass"] is False


def test_goaloo_failure_returns_fail(monkeypatch):
    response = _post(monkeypatch, _report(goaloo="FAIL"))
    assert response.json()["goaloo"] == "FAIL"
    assert response.json()["all_gates_pass"] is False


def test_migration_mismatch_returns_needs_migration(monkeypatch):
    response = _post(monkeypatch, _report(alembic="FAIL",
                                          alembic_detail="NEEDS_MIGRATION"))
    assert response.json()["alembic"] == "NEEDS_MIGRATION"
    assert response.json()["all_gates_pass"] is False


def test_all_gates_pass_does_not_allow_backfill(monkeypatch):
    response = _post(monkeypatch, _report())
    assert response.json() == {
        "postgresql": "PASS", "alembic": "PASS", "r2": "PASS", "goaloo": "PASS",
        "all_gates_pass": True, "backfill_allowed": False,
    }


def test_response_and_logs_never_include_secrets(monkeypatch, caplog):
    secret = "never-return-this-secret-value"
    response = _post(monkeypatch, _report(secret=secret))
    assert secret not in response.text
    assert secret not in caplog.text


def test_unexpected_gate_exception_is_sanitized(monkeypatch, caplog):
    secret = "driver-exception-secret"
    monkeypatch.setenv("PRODUCTION_OPS_TOKEN", TOKEN)
    monkeypatch.setattr(ops, "run_process_environment_readiness",
                        lambda: (_ for _ in ()).throw(RuntimeError(secret)))
    response = client.post("/api/v1/admin/production-readiness",
                           headers={"X-Production-Ops-Token": TOKEN})
    assert response.status_code == 200
    assert response.json()["all_gates_pass"] is False
    assert secret not in response.text
    assert secret not in caplog.text


def test_endpoint_calls_shared_readiness_without_migration_or_backfill(monkeypatch):
    called = []

    def shared_readiness(**kwargs):
        called.append(kwargs)
        return _report()

    monkeypatch.setenv("PRODUCTION_OPS_TOKEN", TOKEN)
    monkeypatch.setattr(ops, "Settings", lambda **kwargs: SimpleNamespace(
        _env_file=kwargs.get("_env_file"), database_url=None, database_url_valid=False))
    monkeypatch.setattr(ops, "run_readiness", shared_readiness)
    monkeypatch.setattr(ops, "execute",
                        lambda **_: (_ for _ in ()).throw(AssertionError("CLI execute must not run")))
    monkeypatch.setattr(ops, "main",
                        lambda *_: (_ for _ in ()).throw(AssertionError("CLI main must not run")))

    response = client.post("/api/v1/admin/production-readiness",
                           headers={"X-Production-Ops-Token": TOKEN})
    assert response.status_code == 200
    assert len(called) == 1
    assert called[0]["settings"]._env_file is None
    assert called[0]["db_engine"] is None
    assert called[0]["health_check"]() == {}
