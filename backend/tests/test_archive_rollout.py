import json

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.jobs import archive_preflight
from app.jobs.ingestion import historical_ingest
from app.models import ArchiveJob, HistoricalIngestDay
from app.providers.registry import ProviderCaller
from app.storage.r2 import InMemoryObjectStore
from test_historical_archive_safety import Adapter, DAY


def settings(**changes):
    return Settings(_env_file=None, enabled_leagues="36", enabled_seasons="2025-2026", **changes)


@pytest.mark.parametrize("use_environment", [False, True])
def test_match_archive_is_independent_of_odds_and_full_mode_can_resume(monkeypatch, use_environment):
    cfg = settings(historical_collect_odds=not use_environment)
    monkeypatch.setattr("app.jobs.ingestion.get_settings", lambda: cfg)
    class NoOdds(Adapter):
        def get_odds(self, identity): raise AssertionError("Odds must not be requested")
    engine = create_engine("sqlite://")
    for model in (ArchiveJob, HistoricalIngestDay): model.__table__.create(engine)
    store = InMemoryObjectStore()
    caller = ProviderCaller(NoOdds(), requests_per_minute=1000000)
    args = dict(season="2025-2026", league="36", start_date=DAY, end_date=DAY,
                include_odds=None if use_environment else False)
    with Session(engine) as session:
        first = historical_ingest(session, caller, store, **args)
        assert first["status"] == "SUCCESS" and first["statistics_written"] == 2
        assert first["odds_history"] == "NOT_REQUESTED" and first["odds_written"] == 0
        assert session.query(HistoricalIngestDay).one().coverage["odds_requested"] is False
        original = {obj.key: store.get_object(obj.key) for obj in store.list_objects()}
        assert historical_ingest(session, caller, store, **args)["records_written"] == 0
        full = Adapter()
        full.fail_second = False
        result = historical_ingest(session, ProviderCaller(full, requests_per_minute=1000000), store,
                                   **{**args, "include_odds": True})
        assert result["status"] == "SUCCESS" and result["odds_written"] == 2
        assert result["matches_written"] == result["statistics_written"] == result["results_written"] == 0
        assert session.query(HistoricalIngestDay).one().coverage["odds_requested"] is True
        assert all(store.get_object(key) == payload for key, payload in original.items())
    engine.dispose()


def ready_settings():
    return settings(database_url="postgresql://user:SECRET_MARKER@localhost/test",
                    r2_endpoint="https://example.invalid", r2_access_key_id="SECRET_MARKER",
                    r2_secret_access_key="SECRET_MARKER", r2_bucket="test-bucket")


SMOKE_ARGS = ["--smoke", "--league", "36", "--season", "2025-2026", "--date", "2025-08-15"]


def test_missing_config_blocks_all_connections_and_writes(monkeypatch, capsys):
    monkeypatch.setattr(archive_preflight, "get_settings", lambda: settings())
    monkeypatch.setattr(archive_preflight, "check_postgres", lambda *_: pytest.fail("no connection allowed"))
    assert archive_preflight.main(SMOKE_ARGS) == 1
    report = json.loads(capsys.readouterr().out)
    assert report["status"] == "NOT_CONFIGURED"
    assert "DATABASE_URL" in report["missing_env"]
    assert report["r2"]["status"] == report["archive"] == report["cleanup"] == report["bulk"] == "NOT_RUN"


def test_database_failure_blocks_r2_smoke_and_redacts_credentials(monkeypatch, capsys):
    monkeypatch.setattr(archive_preflight, "get_settings", ready_settings)
    monkeypatch.setattr(archive_preflight, "check_postgres", lambda *_: {"connection": "FAILED", "schema": "NOT_VERIFIED"})
    monkeypatch.setattr("app.jobs.r2_smoke_test.run_smoke", lambda **_: pytest.fail("must not write R2"))
    assert archive_preflight.main(SMOKE_ARGS) == 1
    output = capsys.readouterr().out
    assert "SECRET_MARKER" not in output
    assert json.loads(output)["error_code"] == "POSTGRES_NOT_READY"


def test_smoke_refuses_multiple_leagues_before_connecting(monkeypatch, capsys):
    monkeypatch.setattr(archive_preflight, "get_settings", ready_settings)
    monkeypatch.setattr(archive_preflight, "check_postgres", lambda *_: pytest.fail("no connection allowed"))
    assert archive_preflight.main([*SMOKE_ARGS[:2], "36,30", *SMOKE_ARGS[3:]]) == 2
    assert json.loads(capsys.readouterr().out)["archive"] == "NOT_RUN"


def test_r2_checksum_failure_never_starts_real_archive(monkeypatch, capsys):
    monkeypatch.setattr(archive_preflight, "get_settings", ready_settings)
    monkeypatch.setattr(archive_preflight, "check_postgres", lambda *_: {"connection": "PASS", "schema": "PASS"})
    monkeypatch.setattr("app.jobs.r2_smoke_test.run_smoke", lambda **_: {"status": "FAILED", "checksum": "FAIL"})
    monkeypatch.setattr("app.jobs.historical_backfill.main", lambda *_: pytest.fail("must not archive"))
    assert archive_preflight.main(SMOKE_ARGS) == 1
    report = json.loads(capsys.readouterr().out)
    assert report["archive"] == report["cleanup"] == report["bulk"] == "NOT_RUN"


@pytest.mark.parametrize("verification_ok", [False, True])
def test_smoke_requires_real_partition_reverification_and_never_cleans(monkeypatch, capsys, verification_ok):
    monkeypatch.setattr(archive_preflight, "get_settings", ready_settings)
    monkeypatch.setattr(archive_preflight, "check_postgres", lambda *_: {"connection": "PASS", "schema": "PASS"})
    monkeypatch.setattr("app.jobs.r2_smoke_test.run_smoke", lambda **_: {"status": "CONNECTED"})
    calls = []
    def backfill(args):
        calls.append(args)
        print(json.dumps({"status": "SUCCESS", "matches_written": 1}))
        return 0
    def verify(*args):
        if not verification_ok: raise IOError("checksum mismatch")
        return {"matches": 1, "results": 1, "statistics": 32}
    monkeypatch.setattr("app.jobs.historical_backfill.main", backfill)
    monkeypatch.setattr(archive_preflight, "verify_scope_archives", verify)
    monkeypatch.setattr("app.jobs.ingestion.cleanup_verified_archives", lambda *_: pytest.fail("cleanup prohibited"))
    assert archive_preflight.main(SMOKE_ARGS) == (0 if verification_ok else 1)
    report = json.loads(capsys.readouterr().out)
    assert report["status"] == ("SMOKE_PASS" if verification_ok else "FAILED")
    assert report["cleanup"] == report["bulk"] == "NOT_RUN"
    assert calls == [["--league", "36", "--season", "2025-2026", "--start-date", "2025-08-15",
                      "--end-date", "2025-08-15", "--batch-size", "1", "--skip-odds"]]
