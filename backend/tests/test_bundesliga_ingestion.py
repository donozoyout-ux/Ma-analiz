"""Isolated unit and integration tests for Bundesliga OpenLigaDB ingestion pipeline.

Uses:
- SQLite in-memory (:memory:)
- InMemoryObjectStore
- Mock HTTP responses (zero production calls during tests)
"""
from datetime import date, datetime, timedelta, timezone
import json
from unittest.mock import MagicMock
from uuid import UUID, uuid4

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.jobs.bundesliga_dryrun import run_bundesliga_dryrun
from app.jobs.bundesliga_pipeline import (
    PipelineSafetyError,
    check_pipeline_safety_gates,
    run_bundesliga_ingest_pipeline,
)
from app.jobs.openligadb_probe import probe_openligadb
from app.models import ActiveMatch, ArchiveJob, MatchResult, SystemSetting
from app.notifications.events import (
    MatchEventType,
    compute_event_id,
    create_match_event,
)
from app.providers.base import (
    ProviderAccessRestricted,
    ProviderInvalidResponse,
    ProviderRateLimited,
    ProviderUnavailable,
)
from app.providers.openligadb import (
    ATTRIBUTION,
    LICENCE,
    LICENCE_URL,
    OpenLigaDBProvider,
)
from app.providers.registry import create_provider
from app.storage.quota import QuotaExceededError, QuotaTracker
from app.storage.r2 import InMemoryObjectStore


@compiles(JSONB, "sqlite")
def _test_jsonb(element, compiler, **kw):
    return "JSON"


@pytest.fixture
def mock_openligadb_matches():
    return [
        {
            "matchID": 83237,
            "matchDateTime": "2026-11-20T20:30:00",
            "timeZoneID": "W. Europe Standard Time",
            "leagueId": 4937,
            "leagueName": "1. Fußball-Bundesliga 2026/2027",
            "leagueSeason": 2026,
            "leagueShortcut": "bl1",
            "matchDateTimeUTC": "2026-11-20T19:30:00Z",
            "group": {
                "groupName": "10. Spieltag",
                "groupOrderID": 10,
                "groupID": 50642,
            },
            "team1": {
                "teamId": 40,
                "teamName": "FC Bayern München",
                "shortName": "Bayern",
            },
            "team2": {
                "teamId": 65,
                "teamName": "1. FC Köln",
                "shortName": "Köln",
            },
            "lastUpdateDateTime": "2026-11-21T18:21:38.077",
            "matchIsFinished": True,
            "matchResults": [
                {
                    "resultID": 131533,
                    "resultName": "Halbzeit",
                    "pointsTeam1": 1,
                    "pointsTeam2": 1,
                    "resultOrderID": 1,
                    "resultTypeID": 1,
                },
                {
                    "resultID": 131534,
                    "resultName": "Endergebnis",
                    "pointsTeam1": 1,
                    "pointsTeam2": 1,
                    "resultOrderID": 2,
                    "resultTypeID": 2,
                },
            ],
        },
        {
            "matchID": 83238,
            "matchDateTime": "2026-11-21T15:30:00",
            "timeZoneID": "W. Europe Standard Time",
            "leagueId": 4937,
            "leagueName": "1. Fußball-Bundesliga 2026/2027",
            "leagueSeason": 2026,
            "leagueShortcut": "bl1",
            "matchDateTimeUTC": "2026-11-21T14:30:00Z",
            "group": {
                "groupName": "10. Spieltag",
                "groupOrderID": 10,
                "groupID": 50642,
            },
            "team1": {
                "teamId": 7,
                "teamName": "Borussia Dortmund",
                "shortName": "BVB",
            },
            "team2": {
                "teamId": 16,
                "teamName": "VfB Stuttgart",
                "shortName": "Stuttgart",
            },
            "lastUpdateDateTime": "2026-11-21T18:21:38.077",
            "matchIsFinished": False,
            "matchResults": [],
        },
    ]


@pytest.fixture
def session():
    engine = create_engine("sqlite://")
    for model in (ActiveMatch, MatchResult, SystemSetting, ArchiveJob):
        model.__table__.create(engine)
    with Session(engine, expire_on_commit=False) as sql:
        yield sql
    engine.dispose()


@pytest.fixture
def store():
    return InMemoryObjectStore()


# --- PHASE A: PROVIDER & PROBE TESTS ---


def test_openligadb_requires_https():
    with pytest.raises(ValueError, match="OpenLigaDB requires HTTPS endpoint"):
        OpenLigaDBProvider(base_url="http://insecure.example.com")


def test_openligadb_registry_factory():
    settings = Settings(
        provider_name="openligadb",
        openligadb_base_url="https://api.openligadb.de",
        _env_file=None,
    )
    provider = create_provider(settings)
    assert provider.name == "openligadb"
    assert provider.attribution == ATTRIBUTION


def test_openligadb_provider_health_check_mock():
    mock_http = MagicMock()
    mock_http.get.return_value.status_code = 200
    mock_http.get.return_value.json.return_value = {
        "groupOrderID": 10,
        "groupName": "10. Spieltag",
        "groupID": 50642,
    }

    provider = OpenLigaDBProvider(client=mock_http, requests_per_minute=60)
    health = provider.health_check()
    assert health["status"] == "PROVIDER_OK"
    assert health["group_order_id"] == 10
    assert health["attribution"] == ATTRIBUTION


def test_openligadb_error_handling_429():
    mock_http = MagicMock()
    mock_http.get.return_value.status_code = 429
    mock_http.get.return_value.headers = {"Retry-After": "0.01"}

    provider = OpenLigaDBProvider(client=mock_http, max_retries=2, requests_per_minute=60)
    with pytest.raises(ProviderRateLimited):
        provider.get_matchdata_by_group("bl1", 2026, 10)


def test_openligadb_error_handling_403():
    mock_http = MagicMock()
    mock_http.get.return_value.status_code = 403

    provider = OpenLigaDBProvider(client=mock_http, max_retries=1, requests_per_minute=60)
    with pytest.raises(ProviderAccessRestricted):
        provider.get_matchdata_by_group("bl1", 2026, 10)


def test_openligadb_error_handling_500():
    mock_http = MagicMock()
    mock_http.get.return_value.status_code = 500

    provider = OpenLigaDBProvider(client=mock_http, max_retries=1, requests_per_minute=60)
    with pytest.raises(ProviderUnavailable):
        provider.get_matchdata_by_group("bl1", 2026, 10)


def test_openligadb_odds_and_stats_never_fabricated(mock_openligadb_matches):
    mock_http = MagicMock()
    provider = OpenLigaDBProvider(client=mock_http, requests_per_minute=60)

    # Must return empty lists, never fabricate
    assert provider.get_odds("83237") == []
    assert provider.get_statistics("83237") == []


def test_openligadb_phase_a_probe_success(mock_openligadb_matches):
    mock_http = MagicMock()
    mock_http.get.side_effect = [
        MagicMock(status_code=200, json=lambda: {"groupOrderID": 10, "groupName": "10. Spieltag"}),
        MagicMock(status_code=200, json=lambda: mock_openligadb_matches),
    ]

    provider = OpenLigaDBProvider(client=mock_http, requests_per_minute=60)
    report = probe_openligadb(provider=provider, season_year=2026, matchday=10)

    assert report["status"] == "OPENLIGADB_PROBE_OK"
    assert report["league"] == "de.bl1"
    assert len(report["sample_fixtures"]) == 2
    assert report["attribution"] == ATTRIBUTION
    assert report["licence"] == LICENCE


# --- PHASE B: DRY-RUN TESTS ---


def test_bundesliga_dryrun_executes_zero_writes(session, mock_openligadb_matches):
    mock_http = MagicMock()
    mock_http.get.return_value.status_code = 200
    mock_http.get.return_value.json.return_value = mock_openligadb_matches

    provider = OpenLigaDBProvider(client=mock_http, requests_per_minute=60)
    report = run_bundesliga_dryrun(
        target_day=date(2026, 11, 20),
        matchday=10,
        session=session,
        provider=provider,
    )

    assert report["status"] == "COMPLETED"
    assert report["dry_run"] is True
    assert report["writes_executed"] == 0
    assert report["matches_found"] == 2
    assert len(report["candidates"]) == 2
    assert len(report["verified_finals"]) == 1

    # Verify database was NOT written to
    count = len(session.scalars(select(ActiveMatch.id)).all())
    assert count == 0


# --- QUOTA & COST PROTECTION TESTS ---


def test_quota_tracker_blocks_excessive_requests():
    settings = Settings(
        bundesliga_daily_max_requests=2,
        _env_file=None,
    )
    tracker = QuotaTracker(settings)
    tracker.check_request_quota(1)
    tracker.record_request(1)
    tracker.check_request_quota(1)
    tracker.record_request(1)

    with pytest.raises(QuotaExceededError) as exc_info:
        tracker.check_request_quota(1)
    assert "daily_requests reached 3/2" in str(exc_info.value)


def test_quota_tracker_blocks_excessive_r2_writes():
    settings = Settings(
        bundesliga_daily_max_r2_writes=1,
        _env_file=None,
    )
    tracker = QuotaTracker(settings)
    tracker.record_r2_write(1, 100)

    with pytest.raises(QuotaExceededError) as exc_info:
        tracker.check_r2_write_quota(1, 100)
    assert "daily_r2_writes reached 2/1" in str(exc_info.value)


def test_quota_tracker_blocks_excessive_r2_bytes():
    settings = Settings(
        bundesliga_daily_max_archive_mb=1,  # 1 MB
        _env_file=None,
    )
    tracker = QuotaTracker(settings)
    with pytest.raises(QuotaExceededError) as exc_info:
        tracker.check_r2_write_quota(1, 2 * 1024 * 1024)
    assert "daily_r2_bytes" in str(exc_info.value)


# --- PHASE D: SAFETY GATES & PIPELINE CONTROLS ---


def test_pipeline_refuses_when_delete_enabled():
    bad_settings_1 = Settings(auto_retention_delete_enabled=True, _env_file=None)
    with pytest.raises(PipelineSafetyError, match="auto_retention_delete_enabled must be False"):
        check_pipeline_safety_gates(bad_settings_1)

    bad_settings_2 = Settings(operational_delete_enabled=True, _env_file=None)
    with pytest.raises(PipelineSafetyError, match="operational_delete_enabled must be False"):
        check_pipeline_safety_gates(bad_settings_2)


def test_pipeline_refuses_live_write_when_disabled(session):
    settings = Settings(
        bundesliga_ingest_enabled=False,
        bundesliga_ingest_dry_run=False,
        _env_file=None,
    )
    with pytest.raises(PipelineSafetyError, match="PIPELINE_DISABLED"):
        run_bundesliga_ingest_pipeline(
            target_day=date(2026, 11, 20),
            dry_run=False,
            settings=settings,
            session=session,
        )


def test_live_ingest_rejects_invalid_matchday_before_io(session, store):
    settings = Settings(
        bundesliga_ingest_enabled=True,
        bundesliga_ingest_dry_run=False,
        _env_file=None,
    )
    with pytest.raises(PipelineSafetyError, match="INVALID_MATCHDAY"):
        run_bundesliga_ingest_pipeline(
            target_day=date(2026, 11, 24),
            matchday=99,
            dry_run=False,
            settings=settings,
            session=session,
            store=store,
        )
    assert session.scalars(select(ActiveMatch)).all() == []


def test_daily_live_mode_uses_date_not_hardcoded_matchday(session, store, mock_openligadb_matches):
    mock_http = MagicMock()
    mock_http.get.return_value.status_code = 200
    mock_http.get.return_value.json.return_value = mock_openligadb_matches
    provider = OpenLigaDBProvider(client=mock_http, requests_per_minute=60)
    settings = Settings(
        bundesliga_ingest_enabled=True,
        bundesliga_ingest_dry_run=False,
        _env_file=None,
    )
    report = run_bundesliga_ingest_pipeline(
        target_day=date(2026, 11, 20),
        dry_run=False,
        settings=settings,
        session=session,
        store=store,
        provider=provider,
    )
    assert report["status"] == "COMPLETED"
    assert report["scope_mode"] == "DAILY"
    assert report["inserted_matches"] == 1
    assert "day20261120" in report["archive_receipt"]["key"]
    mock_http.get.assert_called_once()
    assert "getmatchdata/bl1/2026" in mock_http.get.call_args.args[0]


def test_live_ingest_requires_archive_store_before_io(session):
    settings = Settings(
        bundesliga_ingest_enabled=True,
        bundesliga_ingest_dry_run=False,
        _env_file=None,
    )
    with pytest.raises(PipelineSafetyError, match="ARCHIVE_STORE_REQUIRED"):
        run_bundesliga_ingest_pipeline(
            target_day=date(2026, 11, 24),
            matchday=11,
            dry_run=False,
            settings=settings,
            session=session,
        )
    assert session.scalars(select(ActiveMatch)).all() == []


def test_daily_command_is_read_only_by_default(monkeypatch, capsys):
    from app.jobs import bundesliga_daily

    class FakeProvider:
        attribution = ATTRIBUTION

        def __init__(self, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def get_matches(self, day):
            assert day == date(2026, 11, 24)
            return []

    monkeypatch.setattr(bundesliga_daily, "OpenLigaDBProvider", FakeProvider)
    assert bundesliga_daily.main(["--date", "2026-11-24"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == "DRY_RUN"
    assert payload["sql_writes"] == 0 and payload["r2_writes"] == 0


def test_daily_command_live_mode_requires_config_gate(monkeypatch, capsys):
    from app.jobs import bundesliga_daily

    monkeypatch.setattr(bundesliga_daily, "get_settings", lambda: Settings(
        bundesliga_ingest_enabled=False,
        bundesliga_ingest_dry_run=True,
        _env_file=None,
    ))
    assert bundesliga_daily.main(["--date", "2026-11-24", "--live"]) == 2
    assert "LIVE_OPERATOR_GATE_DISABLED" in capsys.readouterr().out


# --- PHASE C: ISOLATED PIPELINE & RECORD TESTS ---


def test_pipeline_inserts_matches_and_unverified_single_source_results(session, store, mock_openligadb_matches):
    mock_http = MagicMock()
    mock_http.get.return_value.status_code = 200
    mock_http.get.return_value.json.return_value = mock_openligadb_matches

    provider = OpenLigaDBProvider(client=mock_http, requests_per_minute=60)
    settings = Settings(
        bundesliga_ingest_enabled=True,
        bundesliga_ingest_dry_run=False,
        _env_file=None,
    )

    report = run_bundesliga_ingest_pipeline(
        target_day=date(2026, 11, 20),
        matchday=10,
        dry_run=False,
        settings=settings,
        session=session,
        store=store,
        provider=provider,
    )

    assert report["status"] == "COMPLETED"
    assert report["inserted_matches"] == 2
    assert report["persisted_results"] == 1
    assert len(report["events_generated"]) == 2  # Scheduled events only; no fake verification
    assert report["archive_receipt"] is not None
    assert report["archive_receipt"]["row_count"] == 2

    # Check database state
    matches = session.scalars(select(ActiveMatch)).all()
    assert len(matches) == 2
    bayern = next(m for m in matches if m.provider_match_id == "83237")
    assert bayern.home_team == "FC Bayern München"
    assert bayern.away_team == "1. FC Köln"
    assert bayern.status == "FINISHED"

    result = session.scalars(select(MatchResult).where(MatchResult.match_id == bayern.id)).one()
    assert result.home_score == 1
    assert result.away_score == 1
    assert result.status == "FINAL"
    assert result.verified_at is None  # Single source alone cannot certify a final score
    assert "OpenLigaDB" in result.result_source


def test_pipeline_preserves_uuid_on_subsequent_runs(session, store, mock_openligadb_matches):
    mock_http = MagicMock()
    mock_http.get.return_value.status_code = 200
    mock_http.get.return_value.json.return_value = mock_openligadb_matches

    provider = OpenLigaDBProvider(client=mock_http, requests_per_minute=60)
    settings = Settings(
        bundesliga_ingest_enabled=True,
        bundesliga_ingest_dry_run=False,
        _env_file=None,
    )

    # First run: inserts
    run_bundesliga_ingest_pipeline(
        target_day=date(2026, 11, 20),
        matchday=10,
        dry_run=False,
        settings=settings,
        session=session,
        store=store,
        provider=provider,
    )

    matches_run1 = session.scalars(select(ActiveMatch)).all()
    uuid_map_1 = {m.provider_match_id: m.id for m in matches_run1}

    # Second run: must update, preserving the exact same UUIDs
    report2 = run_bundesliga_ingest_pipeline(
        target_day=date(2026, 11, 20),
        matchday=10,
        dry_run=False,
        settings=settings,
        session=session,
        store=store,
        provider=provider,
    )

    assert report2["inserted_matches"] == 0
    assert report2["updated_matches"] == 2

    matches_run2 = session.scalars(select(ActiveMatch)).all()
    uuid_map_2 = {m.provider_match_id: m.id for m in matches_run2}

    assert uuid_map_1 == uuid_map_2


def test_r2_failure_preserves_sql_and_next_attempt_repairs_archive(session, store, mock_openligadb_matches, monkeypatch):
    mock_http = MagicMock()
    mock_http.get.return_value.status_code = 200
    mock_http.get.return_value.json.return_value = mock_openligadb_matches
    provider = OpenLigaDBProvider(client=mock_http, requests_per_minute=60)
    settings = Settings(
        bundesliga_ingest_enabled=True,
        bundesliga_ingest_dry_run=False,
        _env_file=None,
    )
    real_put = store.put_object_if_absent

    def fail_upload(*args, **kwargs):
        raise IOError("Synthetic R2 outage")

    monkeypatch.setattr(store, "put_object_if_absent", fail_upload)
    with pytest.raises(RuntimeError, match="Archive failed"):
        run_bundesliga_ingest_pipeline(
            target_day=date(2026, 11, 20), matchday=10, dry_run=False,
            settings=settings, session=session, store=store, provider=provider,
        )
    persisted = session.scalars(select(ActiveMatch)).all()
    assert len(persisted) == 2
    ids = {m.provider_match_id: m.id for m in persisted}
    assert len(session.scalars(select(MatchResult)).all()) == 1

    monkeypatch.setattr(store, "put_object_if_absent", real_put)
    repaired = run_bundesliga_ingest_pipeline(
        target_day=date(2026, 11, 20), matchday=10, dry_run=False,
        settings=settings, session=session, store=store, provider=provider,
    )
    assert repaired["status"] == "COMPLETED"
    assert repaired["archive_receipt"]["row_count"] == 2
    assert {m.provider_match_id: m.id for m in session.scalars(select(ActiveMatch)).all()} == ids
    archive = session.scalars(select(ArchiveJob).where(ArchiveJob.r2_key == repaired["archive_receipt"]["key"])).one()
    assert archive.status == "VERIFIED"


# --- NOTIFICATION EVENT CONTRACT TESTS ---


def test_notification_event_contract_determinism():
    match_id = uuid4()
    event1 = create_match_event(
        event_type=MatchEventType.RESULT_VERIFIED,
        match_id=match_id,
        provider_match_id="83237",
        home_team="FC Bayern München",
        away_team="1. FC Köln",
        start_time="2026-11-20T19:30:00Z",
        status="FINAL",
        home_score=1,
        away_score=1,
        occurred_at="2026-11-20T21:30:00Z",
    )

    event2 = create_match_event(
        event_type=MatchEventType.RESULT_VERIFIED,
        match_id=match_id,
        provider_match_id="83237",
        home_team="FC Bayern München",
        away_team="1. FC Köln",
        start_time="2026-11-20T19:30:00Z",
        status="FINAL",
        home_score=1,
        away_score=1,
        occurred_at="2026-11-20T21:35:00Z",  # later timestamp
    )

    # Deterministic event ID ensures deduplication
    assert event1.event_id == event2.event_id
    payload = event1.model_dump()
    assert payload["score"] == {"home": 1, "away": 1}
    assert payload["league_id"] == "de.bl1"
    assert "OpenLigaDB" in payload["attribution"]
