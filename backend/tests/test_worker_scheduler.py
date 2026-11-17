import json
from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace
from uuid import uuid4

from sqlalchemy import create_engine
from sqlalchemy.orm import Session


def test_daily_match_ingestion_never_requests_odds(monkeypatch):
    from app.core.config import Settings
    from app.jobs.ingestion import daily_ingest

    calls = []

    class Provider:
        name = "goaloo"

    class Caller:
        provider = Provider()
        def call(self, method, *args):
            calls.append(method)
            return [{"id": "configured-league"}] if method == "get_leagues" else []

    class Database:
        def flush(self):
            pass
        def commit(self):
            pass

    monkeypatch.setattr("app.jobs.ingestion.get_settings", lambda: Settings())
    result = daily_ingest(Database(), Caller(), date(2026, 11, 17), enabled_leagues=("configured-league",))

    assert calls == ["get_leagues", "get_matches"]
    assert "get_odds" not in calls
    assert result["status"] == "SKIPPED"
    assert result["records_written"] == 0


def test_worker_cycle_uses_configured_intervals_and_separate_jobs(monkeypatch):
    import app.worker as worker

    calls = []
    monkeypatch.setattr(worker, "SessionLocal", None)
    monkeypatch.setattr(worker, "settings", SimpleNamespace(
        worker_match_interval_seconds=3600,
        worker_odds_interval_seconds=1800,
        worker_results_interval_seconds=1800,
        worker_scanner_interval_seconds=1800,
    ))
    monkeypatch.setattr(worker, "run_task", lambda name, key, ledger: calls.append((name, key)) or {"status": "SKIPPED"})
    now = datetime(2026, 11, 17, 10, 42, tzinfo=timezone.utc)

    outcomes = worker.cycle(now)

    assert [name for name, _ in calls] == ["collect_matches", "collect_odds", "process_results", "scheduled_scan"]
    assert len(outcomes) == 4
    assert calls[0][1].endswith("10:00:00+00:00")
    assert all(key.endswith("10:30:00+00:00") for _, key in calls[1:])


def test_scheduled_scanner_claims_one_durable_slot(monkeypatch):
    from app.jobs import tasks
    from app.jobs.ledger import SQLLedger
    from app.models import JobRun

    engine = create_engine("sqlite://")
    JobRun.__table__.create(engine)
    calls = []
    monkeypatch.setitem(tasks.HANDLERS, "scheduled_scan",
                        lambda slot, session, limit=50: calls.append((slot, limit)) or {
                            "status": "SUCCESS", "records_seen": 1, "records_written": 1,
                            "records_skipped": 0, "records_failed": 0,
                        })
    key = "scheduled_scan:2026-11-17T10:30:00+00:00"
    try:
        with Session(engine) as session:
            first = tasks.run_task("scheduled_scan", key, SQLLedger(session), limit=25)
            second = tasks.run_task("scheduled_scan", key, SQLLedger(session), limit=25)
        assert first["status"] == "SUCCESS"
        assert second["reason"] == "IDEMPOTENT_DUPLICATE"
        assert len(calls) == 1 and calls[0][1] == 25
    finally:
        engine.dispose()


def test_scanner_skips_a_match_already_analyzed_in_the_same_slot():
    import json
    from app.analysis.scanning import scan_upcoming_matches
    from app.models import ActiveMatch, MatchAnalysis

    engine = create_engine("sqlite://")
    ActiveMatch.__table__.create(engine)
    MatchAnalysis.__table__.create(engine)
    now = datetime.now(timezone.utc)
    match_id = uuid4()
    try:
        with Session(engine) as session:
            session.add(ActiveMatch(
                id=match_id, provider="test", provider_match_id="test-id", home_team="Home", away_team="Away",
                start_time=now + timedelta(hours=1), status="SCHEDULED", expires_at=now + timedelta(days=1),
            ))
            session.add(MatchAnalysis(
                id=uuid4(), match_id=match_id, analysis_timestamp=now - timedelta(minutes=2),
                analysis_status="DATA_MISSING", final_decision="NO BET", model_version="test",
                feature_version="test", analysis_output=json.dumps({"final": {"signal": "NO BET"}}),
            ))
            session.commit()
            result = scan_upcoming_matches(
                session, analyze_one=lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("reanalysis")),
                as_of=now, scheduled_slot_start=now - timedelta(minutes=5),
            )
        assert result["matches_scanned"] == 1
        assert result["matches_skipped"] == 1
        assert result["records_written"] == 0
    finally:
        engine.dispose()


def test_historical_ingest_does_not_take_an_unexpired_running_lease(monkeypatch):
    from app.core.config import Settings
    from app.jobs.ingestion import historical_ingest
    from app.models import ArchiveJob, HistoricalIngestDay
    from app.providers.registry import ProviderCaller
    from app.storage.r2 import InMemoryObjectStore

    engine = create_engine("sqlite://")
    ArchiveJob.__table__.create(engine)
    HistoricalIngestDay.__table__.create(engine)
    settings = Settings(enabled_leagues="league-1", enabled_seasons="2026-27", historical_ingest_lease_seconds=3600)
    monkeypatch.setattr("app.jobs.ingestion.get_settings", lambda: settings)
    calls = []

    class Adapter:
        name = "goaloo"
        def get_leagues(self): return [{"id": "league-1"}]
        def get_matches(self, day): calls.append(("matches", day)); return []
        def get_results(self, day): calls.append(("results", day)); return []

    day = date(2026, 11, 17)
    try:
        with Session(engine) as session:
            progress = HistoricalIngestDay(
                provider="goaloo", season="2026-27", league_id="league-1", ingest_date=day,
                status="RUNNING", attempt_count=1, lease_token="active-owner",
                lease_expires_at=datetime.now(timezone.utc) + timedelta(minutes=30),
            )
            session.add(progress)
            session.commit()
            result = historical_ingest(
                session, ProviderCaller(Adapter(), requests_per_minute=6000, max_retries=1),
                InMemoryObjectStore(), season="2026-27", league="league-1", start_date=day, end_date=day,
            )
            assert result["dates_skipped"] == 1
            assert progress.attempt_count == 1
        assert calls == []
    finally:
        engine.dispose()


def test_historical_ingest_reclaims_expired_lease_once_and_releases_it(monkeypatch):
    from app.core.config import Settings
    from app.jobs.ingestion import historical_ingest
    from app.models import ArchiveJob, HistoricalIngestDay
    from app.providers.registry import ProviderCaller
    from app.storage.r2 import InMemoryObjectStore

    engine = create_engine("sqlite://")
    ArchiveJob.__table__.create(engine)
    HistoricalIngestDay.__table__.create(engine)
    monkeypatch.setattr("app.jobs.ingestion.get_settings", lambda: Settings(
        enabled_leagues="league-1", enabled_seasons="2026-27", historical_ingest_lease_seconds=3600))
    calls = []

    class Adapter:
        name = "goaloo"
        def get_leagues(self): return [{"id": "league-1"}]
        def get_matches(self, day): calls.append(("matches", day)); return []
        def get_results(self, day): calls.append(("results", day)); return []

    day = date(2026, 11, 17)
    try:
        with Session(engine) as session:
            session.add(HistoricalIngestDay(
                provider="goaloo", season="2026-27", league_id="league-1", ingest_date=day,
                status="RUNNING", attempt_count=1, lease_token="expired-owner",
                lease_expires_at=datetime.now(timezone.utc) - timedelta(minutes=1),
            ))
            session.commit()
            result = historical_ingest(
                session, ProviderCaller(Adapter(), requests_per_minute=6000, max_retries=1),
                InMemoryObjectStore(), season="2026-27", league="league-1", start_date=day, end_date=day,
            )
            progress = session.query(HistoricalIngestDay).one()
        assert result["status"] == "SKIPPED"
        assert calls == [("matches", day), ("results", day)]
        assert progress.status == "EMPTY"
        assert progress.attempt_count == 2
        assert progress.lease_token is None and progress.lease_expires_at is None
    finally:
        engine.dispose()


def test_job_ledger_does_not_reclaim_a_recent_running_job(monkeypatch):
    from app.jobs.ledger import MemoryLedger

    monkeypatch.setattr("app.jobs.ledger.get_settings", lambda: SimpleNamespace(job_run_stale_after_seconds=21600))
    ledger = MemoryLedger()
    run, claimed = ledger.claim("long-task", "historical_ingest")
    assert claimed
    run.started_at = datetime.now(timezone.utc) - timedelta(hours=2)
    _, claimed = ledger.claim("long-task", "historical_ingest")
    assert not claimed
    run.started_at = datetime.now(timezone.utc) - timedelta(hours=7)
    _, claimed = ledger.claim("long-task", "historical_ingest")
    assert claimed


def test_sql_job_failure_rolls_back_and_can_be_retried():
    from app.jobs.ledger import SQLLedger, run_idempotent_job
    from app.models import JobRun

    engine = create_engine("sqlite://")
    JobRun.__table__.create(engine)
    try:
        with Session(engine) as session:
            ledger = SQLLedger(session)

            def fail_with_unique_violation():
                session.add(JobRun(idempotency_key="failed-slot", job_type="scheduled_scan", status="RUNNING"))
                session.commit()

            failed = run_idempotent_job(ledger, "failed-slot", "scheduled_scan", fail_with_unique_violation)
            assert failed["status"] == "FAILED"

            retried = run_idempotent_job(
                ledger, "failed-slot", "scheduled_scan",
                lambda: {"status": "SUCCESS", "records_written": 1},
            )
            assert retried["status"] == "SUCCESS"
            stored = session.query(JobRun).filter_by(idempotency_key="failed-slot").one()
            assert stored.status == "SUCCESS"
    finally:
        engine.dispose()


def test_daily_archive_cron_exit_status_and_json(monkeypatch, capsys):
    from app.jobs import daily_archive

    outcomes = iter([
        {"job": "process_results", "status": "SUCCESS"},
        {"job": "archive_data", "status": "SKIPPED", "reason": "NO_EXPIRED_ROWS"},
        {"job": "cleanup_database", "status": "SKIPPED", "reason": "NO_VERIFIED_ARCHIVE_CANDIDATES"},
        {"job": "calculate_daily_metrics", "status": "SUCCESS"},
    ])
    monkeypatch.setattr(daily_archive, "run_daily", lambda *_args: next(outcomes))
    assert daily_archive.main() == 0
    assert len(json.loads(capsys.readouterr().out)["jobs"]) == 4

    outcomes = iter([
        {"job": "process_results", "status": "SUCCESS"},
        {"job": "archive_data", "status": "PARTIAL"},
        {"job": "cleanup_database", "status": "SKIPPED"},
        {"job": "calculate_daily_metrics", "status": "SUCCESS"},
    ])
    monkeypatch.setattr(daily_archive, "run_daily", lambda *_args: next(outcomes))
    assert daily_archive.main() == 1
    assert json.loads(capsys.readouterr().out)["jobs"][1]["status"] == "PARTIAL"


def test_daily_archive_reports_unexpected_exception_as_json_failure(monkeypatch, capsys):
    from app.jobs import daily_archive

    def broken(*_args):
        raise RuntimeError("do not expose credentials")

    monkeypatch.setattr(daily_archive, "run_daily", broken)
    assert daily_archive.main() == 1
    payload = json.loads(capsys.readouterr().out)
    assert all(job["status"] == "FAILED" for job in payload["jobs"])
    assert all("error_code" in job and "credentials" not in job for job in payload["jobs"])
