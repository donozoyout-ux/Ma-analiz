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


def test_worker_checks_job_history_pruning_once_per_utc_day(monkeypatch):
    from contextlib import nullcontext
    import app.worker as worker

    prune_claims = []
    monkeypatch.setattr(worker, "_last_prune_check_slot", None)
    monkeypatch.setattr(worker, "SessionLocal", lambda: nullcontext(object()))
    monkeypatch.setattr(worker, "run_task", lambda *_args: {"status": "SKIPPED"})
    monkeypatch.setattr(worker, "run_idempotent_job", lambda _ledger, key, *_args, **_kwargs:
                        prune_claims.append(key) or {"status": "SUCCESS"})

    worker.cycle(datetime(2026, 11, 17, 10, 0, tzinfo=timezone.utc))
    worker.cycle(datetime(2026, 11, 17, 10, 5, tzinfo=timezone.utc))
    worker.cycle(datetime(2026, 11, 18, 10, 0, tzinfo=timezone.utc))

    assert prune_claims == [
        "prune_job_history:2026-11-17T00:00:00+00:00",
        "prune_job_history:2026-11-18T00:00:00+00:00",
    ]


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


def test_scanner_skips_a_match_already_analyzed_in_the_same_slot(monkeypatch):
    import json
    from app.analysis.scanning import scan_upcoming_matches
    from app.core.config import Settings
    from app.models import ActiveMatch, MatchAnalysis

    engine = create_engine("sqlite://")
    ActiveMatch.__table__.create(engine)
    MatchAnalysis.__table__.create(engine)
    from app.models import ActiveOdds
    ActiveOdds.__table__.create(engine)
    now = datetime.now(timezone.utc)
    match_id = uuid4()
    monkeypatch.setattr("app.analysis.scanning.get_settings", lambda: Settings(
        match_provider="test", enabled_leagues="league-1", enabled_seasons="2026-2027",
        worker_scanner_interval_seconds=300, scanner_lookahead_minutes=1440,
        scanner_max_matches_per_run=50,
    ))
    try:
        with Session(engine) as session:
            session.add(ActiveMatch(
                id=match_id, provider="test", provider_match_id="test-id", home_team="Home", away_team="Away",
                league_id="league-1", season="2026-2027",
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


def test_archive_data_processes_every_configured_season_and_league(monkeypatch):
    from contextlib import nullcontext
    from app.core.config import Settings
    from app.jobs import tasks

    settings = Settings(enabled_seasons="2024-2025,2025-2026,2026-2027", enabled_leagues="36")
    calls = []
    monkeypatch.setattr(tasks, "get_settings", lambda: settings)
    monkeypatch.setattr(tasks, "R2ObjectStore", lambda: object())
    monkeypatch.setattr(tasks, "SessionLocal", lambda: nullcontext(object()))
    monkeypatch.setattr(tasks, "_caller", lambda: object())

    def archive_active(_session, _store):
        return {"odds": 0, "results": 0, "predictions": 0}

    def ingest(_session, _caller, _store, *, season, league, start_date, end_date):
        calls.append((season, league, start_date, end_date))
        return {"status": "SUCCESS", "records_written": 1, "records_failed": 0, "records_skipped": 0}

    monkeypatch.setattr("app.jobs.ingestion.archive_expired_active_state", archive_active)
    monkeypatch.setattr("app.jobs.ingestion.historical_ingest", ingest)
    result = tasks.archive_data(date(2026, 11, 17))

    assert [row[0] for row in calls] == ["2024-2025", "2025-2026", "2026-2027"]
    assert all(row[1] == "36" and row[2] == row[3] == date(2026, 11, 16) for row in calls)
    assert result["records_written"] == 3


def test_historical_backfill_dry_run_plans_only_enabled_scope(monkeypatch, capsys):
    from app.core.config import Settings
    from app.jobs import historical_backfill

    monkeypatch.setattr(historical_backfill, "get_settings", lambda: Settings(
        enabled_leagues="36,30", enabled_seasons="2021-2022,2024-2025",
        historical_start_date=date(2021, 7, 1)))
    result = historical_backfill.main([
        "--league", "36", "--season", "2021-2022", "--start-date", "2021-08-01",
        "--end-date", "2021-08-05", "--batch-size", "2", "--dry-run",
    ])
    payload = json.loads(capsys.readouterr().out)
    assert result == 0
    assert payload["status"] == "DRY_RUN" and payload["writes"] is False
    assert len(payload["scopes"]) == 1
    assert payload["scopes"][0]["league"] == "36"
    assert payload["scopes"][0]["dates_requested"] == 5
    assert payload["batch_size"] == 2


def test_historical_backfill_totals_include_matches_seen():
    from app.jobs.historical_backfill import _add_totals, empty_totals

    totals = empty_totals()
    _add_totals(totals, {"records_seen": 8, "matches_seen": 7, "dates_requested": 2,
                         "dates_completed": 1, "dates_failed": 1, "matches_written": 4})
    assert totals["records_seen"] == 8
    assert totals["matches_seen"] == 7
    assert totals["dates_requested"] == 2
    assert totals["failed_dates"] == 1
    assert totals["matches_written"] == 4
