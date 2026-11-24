import json
import threading
import time
import urllib.error
import urllib.request

import pytest

from app.core.config import Settings
from app.jobs import archive_diagnostic_service as diagnostic


def configured():
    return Settings(_env_file=None, database_url="postgresql://u:DO_NOT_LOG@db/test",
                    r2_endpoint="https://example.invalid", r2_bucket="test-bucket",
                    r2_access_key_id="DO_NOT_LOG", r2_secret_access_key="DO_NOT_LOG",
                    enabled_leagues="36", enabled_seasons="2025-2026")


def test_missing_environment_never_connects_or_prints_values(monkeypatch, capsys):
    monkeypatch.setattr("app.core.config.get_settings", lambda: Settings(_env_file=None))
    monkeypatch.setattr("app.jobs.archive_preflight.check_postgres", lambda *_: pytest.fail("must not connect"))
    result = diagnostic.run_diagnostic("connections")
    assert result["status"] == "NOT_CONFIGURED"
    assert "DATABASE_URL" in result["missing_env"]
    assert capsys.readouterr().out == ""


def test_connections_cannot_migrate_backfill_or_clean(monkeypatch, capsys):
    monkeypatch.setattr("app.core.config.get_settings", configured)
    monkeypatch.setattr("app.jobs.archive_preflight.check_postgres", lambda *_: {"connection": "PASS", "schema": "NEEDS_MIGRATION"})
    def r2(**kwargs):
        print("DO_NOT_LOG")
        return {"status": "CONNECTED", "write": "PASS", "checksum": "PASS", "object_key": "DO_NOT_LOG"}
    monkeypatch.setattr("app.jobs.r2_smoke_test.run_smoke", r2)
    monkeypatch.setattr("app.jobs.archive_preflight.main", lambda *_: pytest.fail("not smoke mode"))
    result = diagnostic.run_diagnostic("connections")
    assert result["status"] == "CONNECTIONS_PASS"
    assert result["ready_for_smoke"] is False
    assert "DO_NOT_LOG" not in json.dumps(result) + capsys.readouterr().out
    assert result["bulk"] == result["migration"] == result["postgres_cleanup"] == "NOT_RUN"


def test_database_failure_prevents_r2_writes(monkeypatch):
    monkeypatch.setattr("app.core.config.get_settings", configured)
    monkeypatch.setattr("app.jobs.archive_preflight.check_postgres", lambda *_: {"connection": "FAILED"})
    monkeypatch.setattr("app.jobs.r2_smoke_test.run_smoke", lambda **_: pytest.fail("no R2 allowed"))
    assert diagnostic.run_diagnostic("connections")["status"] == "FAILED"


def test_smoke_uses_fixed_scope_and_drops_untrusted_output(monkeypatch, capsys):
    monkeypatch.setattr("app.core.config.get_settings", configured)
    calls = []
    def preflight(args):
        calls.append(args)
        print("DO_NOT_LOG")
        print(json.dumps({"status": "SMOKE_PASS", "postgres": {"connection": "PASS", "schema": "PASS"},
                          "r2": {"status": "CONNECTED", "write": "DO_NOT_LOG"},
                          "verified_r2_rows": {"matches": 1, "results": 1, "statistics": 32, "secret": "DO_NOT_LOG"},
                          "error_code": "DO_NOT_LOG", "environment": "DO_NOT_LOG"}))
        return 0
    monkeypatch.setattr("app.jobs.archive_preflight.main", preflight)
    result = diagnostic.run_diagnostic("smoke")
    assert calls == [["--smoke", "--league", "36", "--season", "2025-2026", "--date", "2025-08-15"]]
    assert result["status"] == "SMOKE_PASS" and result["verified_r2_rows"]["statistics"] == 32
    assert "DO_NOT_LOG" not in json.dumps(result) + capsys.readouterr().out


def test_exception_messages_and_validation_input_never_reach_logs(monkeypatch, capsys):
    def fail(): raise ValueError("DO_NOT_LOG")
    monkeypatch.setattr("app.core.config.get_settings", fail)
    result = diagnostic.run_diagnostic("smoke")
    captured = capsys.readouterr()
    assert result["status"] == "FAILED"
    assert "DO_NOT_LOG" not in json.dumps(result) + captured.out + captured.err


def test_http_health_is_available_during_work_and_requests_do_not_retrigger(capsys):
    entered, finish = threading.Event(), threading.Event()
    calls = []
    def work(mode):
        calls.append(mode)
        entered.set()
        finish.wait(5)
        return diagnostic._summary({"status": "FAILED"}, mode)
    state = diagnostic.DiagnosticState("connections", runner=work)
    server = diagnostic.make_server(0, state, host="127.0.0.1")
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        assert state.start_once() and not state.start_once()
        assert entered.wait(2)
        base = f"http://127.0.0.1:{server.server_port}"
        for _ in range(3):
            with urllib.request.urlopen(base + "/health/live", timeout=2) as response:
                assert json.load(response) == {"status": "ok"}
        with urllib.request.urlopen(base + "/archive-diagnostic", timeout=2) as response:
            assert json.load(response)["state"] == "running"
        with pytest.raises(urllib.error.HTTPError) as error:
            urllib.request.urlopen(base + "/run", timeout=2)
        assert error.value.code == 404
        finish.set()
        deadline = time.monotonic() + 2
        while state.result is None and time.monotonic() < deadline:
            time.sleep(0.01)
        assert state.result["status"] == "FAILED"
        with urllib.request.urlopen(base + "/health/live", timeout=2) as response:
            assert response.status == 200
        with urllib.request.urlopen(base + "/archive-diagnostic", timeout=2) as response:
            assert json.load(response)["state"] == "finished"
        assert calls == ["connections"]
    finally:
        finish.set()
        server.shutdown()
        server.server_close()
        thread.join(2)


def test_render_manifest_does_not_auto_migrate_or_replace_normal_start():
    from pathlib import Path
    text = (Path(__file__).resolve().parents[2] / "render.yaml").read_text(encoding="utf-8")
    assert "preDeployCommand:" not in text
    assert "startCommand: node frontend/scripts/run-single-service.mjs" in text


def test_main_uses_render_port_without_running_external_diagnostics(monkeypatch):
    calls = []
    class State:
        def __init__(self, mode): calls.append(("mode", mode))
        def start_once(self): calls.append(("start_once",))
    class Server:
        def serve_forever(self, **kwargs): raise KeyboardInterrupt
        def server_close(self): calls.append(("closed",))
    def server(port, state):
        calls.append(("port", port))
        return Server()
    monkeypatch.setenv("PORT", "18765")
    monkeypatch.setattr(diagnostic, "DiagnosticState", State)
    monkeypatch.setattr(diagnostic, "make_server", server)
    assert diagnostic.main(["--mode", "connections"]) == 0
    assert calls == [("mode", "connections"), ("port", 18765), ("start_once",), ("closed",)]
