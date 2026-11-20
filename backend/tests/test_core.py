import asyncio
import hashlib
from datetime import date, datetime, timezone
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app.analysis.coupons import build_coupon
from app.analysis.evidence import EvidenceStatus, evidence_confidence
from app.analysis.lessons import generate_lessons, propose_parameter_change
from app.analysis.prediction import make_prediction
from app.historical.repository import HistoricalRepository
from app.main import app
from app.notifications import telegram
from app.providers.mock import EmptyMockProvider
from app.providers.normalize import normalize_match, normalize_matches, normalize_odds
from app.storage.archive import (
    ArchiveStatus, InvalidArchiveTransition, archive_partition,
    cleanup_database_rows_after_verified, validate_transition,
)
from app.storage.monitor import StorageMonitor, postgres_status
from app.storage.r2 import InMemoryObjectStore, R2NotConfigured, R2ObjectStore, parquet_bytes, parquet_row_count


class CorruptStore(InMemoryObjectStore):
    corrupt_reads = False
    put_count = 0

    def put_object(self, key, body, *, content_type, metadata=None):
        self.put_count += 1
        super().put_object(key, body, content_type=content_type, metadata=metadata)

    def get_object(self, key):
        body = super().get_object(key)
        return body + b"corrupt" if self.corrupt_reads else body


class MemoryTracker:
    def __init__(self):
        self.state = {}
        self.transitions = []

    def get_status(self, key):
        return self.state.get(key)

    def set_status(self, *, key, day, dataset, league, status, rows=None, size=None, checksum=None):
        validate_transition(self.state.get(key), status)
        self.state[key] = status
        self.transitions.append(status)


def test_mock_r2_parquet_round_trip_metadata_checksum_and_delete():
    store = InMemoryObjectStore()
    rows = [{"id": 1, "name": "A"}, {"id": 2, "name": "B"}]
    receipt = store.put_parquet("matches/test.parquet", rows)
    info = store.head_object(receipt.key)
    assert info is not None and info.size == receipt.size
    assert info.metadata["sha256"] == receipt.checksum
    assert info.metadata["row-count"] == "2"
    assert parquet_row_count(store.get_object(receipt.key)) == 2
    assert store.read_parquet(receipt.key) == rows
    assert store.object_exists(receipt.key)
    assert len(store.list_objects("matches/")) == 1
    store.delete_object(receipt.key)
    assert not store.object_exists(receipt.key)


def test_mock_r2_idempotent_parquet_does_not_duplicate_or_rewrite():
    store = CorruptStore()
    rows = [{"id": 1}]
    first = store.put_parquet("metrics/date=2026-01-01.parquet", rows)
    puts_after_first = store.put_count
    second = store.put_parquet("metrics/date=2026-01-01.parquet", rows)
    assert first == second
    assert store.put_count == puts_after_first == 1


def test_archive_state_machine_allows_retry_but_rejects_skips_and_resurrection():
    validate_transition(None, ArchiveStatus.PENDING)
    validate_transition(ArchiveStatus.PENDING, ArchiveStatus.UPLOADING)
    validate_transition(ArchiveStatus.FAILED, ArchiveStatus.PENDING)
    validate_transition(ArchiveStatus.VERIFIED, ArchiveStatus.DELETED)
    with pytest.raises(InvalidArchiveTransition):
        validate_transition(ArchiveStatus.PENDING, ArchiveStatus.VERIFIED)
    with pytest.raises(InvalidArchiveTransition):
        validate_transition(ArchiveStatus.DELETED, ArchiveStatus.UPLOADING)


def test_archive_pipeline_verifies_and_idempotently_reuses_partition():
    store, tracker = CorruptStore(), MemoryTracker()
    rows = [{"start_time": datetime(2026, 1, 1, tzinfo=timezone.utc), "home_team": "A"}]
    kwargs = dict(dataset="matches", season="2025-26", league="league-1", day=date(2026, 1, 1), rows=rows, sleep=lambda _: None, tracker=tracker)
    receipt = archive_partition(store, **kwargs)
    assert receipt.status == ArchiveStatus.VERIFIED
    assert tracker.transitions == [ArchiveStatus.PENDING, ArchiveStatus.UPLOADING, ArchiveStatus.UPLOADED, ArchiveStatus.VERIFIED]
    puts = store.put_count
    assert archive_partition(store, **kwargs) == receipt
    assert store.put_count == puts
    assert receipt.key == "matches/season=2025-26/league=league-1/date=2026-01-01.parquet"


def test_archive_conflicting_existing_object_is_never_overwritten():
    store, tracker = InMemoryObjectStore(), MemoryTracker()
    key = "matches/season=2025-26/league=league-1/date=2026-01-01.parquet"
    original = parquet_bytes([{"id": "original"}])
    store.put_object(key, original, content_type="application/vnd.apache.parquet",
                     metadata={"sha256": "old", "row-count": "1", "archive-key": key})

    with pytest.raises(RuntimeError, match="refusing to overwrite"):
        archive_partition(store, dataset="matches", season="2025-26", league="league-1",
                          day=date(2026, 1, 1), rows=[{"id": "new"}], retries=1,
                          sleep=lambda _: None, tracker=tracker)

    assert store.get_object(key) == original
    assert tracker.state[key] == ArchiveStatus.FAILED


def test_matching_existing_object_without_ledger_is_verified_without_reupload():
    store, tracker = InMemoryObjectStore(), MemoryTracker()
    rows = [{"id": "same"}]
    key = "matches/season=2025-26/league=league-1/date=2026-01-01.parquet"
    payload = parquet_bytes(rows)
    checksum = hashlib.sha256(payload).hexdigest()
    store.put_object(key, payload, content_type="application/vnd.apache.parquet",
                     metadata={"sha256": checksum, "row-count": "1", "archive-key": key})
    receipt = archive_partition(store, dataset="matches", season="2025-26", league="league-1",
                                day=date(2026, 1, 1), rows=rows, tracker=tracker)
    assert receipt.key == key
    assert tracker.state[key] == ArchiveStatus.VERIFIED
    assert tracker.transitions == [ArchiveStatus.PENDING, ArchiveStatus.UPLOADING,
                                   ArchiveStatus.UPLOADED, ArchiveStatus.VERIFIED]


def test_upload_failure_marks_failed_and_never_deletes_database_rows():
    store, tracker, deleted = CorruptStore(), MemoryTracker(), []
    store.corrupt_reads = True
    rows = [{"start_time": datetime(2020, 1, 1, tzinfo=timezone.utc)}]
    with pytest.raises(RuntimeError, match="source records must be retained"):
        archive_partition(store, dataset="matches", season="2020", league="l1", day=date(2020, 1, 1), rows=rows, retries=1, sleep=lambda _: None, tracker=tracker)
    assert tracker.state[next(iter(tracker.state))] == ArchiveStatus.FAILED
    job = SimpleNamespace(status="FAILED", checksum=None, verified_at=None, row_count=1, archive_date=date(2020, 1, 1))
    with pytest.raises(RuntimeError, match="refusing database cleanup"):
        cleanup_database_rows_after_verified(FakeSession(job), key="matches/x", delete_rows=lambda *_: deleted.append(True))
    assert not deleted


def test_persisted_verified_archive_allows_old_partition_cleanup_only():
    store = InMemoryObjectStore()
    receipt = archive_partition(store, dataset="matches", season="2020", league="l", day=date(2020, 1, 1), rows=[{"id": 1}])
    old_job = SimpleNamespace(status="VERIFIED", checksum=receipt.checksum, verified_at=datetime.now(timezone.utc), row_count=1,
                              file_size_bytes=receipt.size, archive_date=date(2020, 1, 1), deleted_at=None)
    session, deleted = FakeSession(old_job), []
    cleanup_database_rows_after_verified(session, key=receipt.key, store=store, delete_rows=lambda db, job: deleted.append((db, job)))
    assert len(deleted) == 1 and old_job.status == "DELETED" and session.commits == 1
    recent = SimpleNamespace(status="VERIFIED", checksum="sha256", verified_at=datetime.now(timezone.utc), row_count=3, archive_date=date.today(), deleted_at=None)
    recent_session, recent_deleted = FakeSession(recent), []
    with pytest.raises(RuntimeError, match="safety buffer"):
        cleanup_database_rows_after_verified(recent_session, key="matches/y", delete_rows=lambda *_: recent_deleted.append(True))
    assert recent_deleted == [] and recent_session.rollbacks == 1


class FakeSession:
    def __init__(self, job):
        self.job = job
        self.commits = 0
        self.rollbacks = 0
    def query(self, model): return self
    def filter_by(self, **kwargs): return self
    def with_for_update(self): return self
    def one_or_none(self): return self.job
    def commit(self): self.commits += 1
    def rollback(self): self.rollbacks += 1


def test_r2_adapter_refuses_missing_credentials(monkeypatch):
    monkeypatch.setattr("app.storage.r2.get_settings", lambda: SimpleNamespace(
        r2_endpoint=None, r2_access_key_id=None, r2_secret_access_key=None, r2_bucket="",
        r2_configured=False))
    with pytest.raises(R2NotConfigured, match="R2_NOT_CONFIGURED"):
        R2ObjectStore()


def test_storage_thresholds_and_unconfigured_sizes_are_honest():
    settings = SimpleNamespace(postgres_normal_mb=300, postgres_warning_mb=500, postgres_high_mb=750, postgres_critical_mb=750)
    mib = 1024 * 1024
    assert postgres_status(299 * mib, settings) == "healthy"
    assert postgres_status(400 * mib, settings) == "warning"
    assert postgres_status(600 * mib, settings) == "high"
    assert postgres_status(749 * mib, settings) == "high"
    assert postgres_status(750 * mib, settings) == "critical"
    snapshot = StorageMonitor(None, settings=SimpleNamespace(
        postgres_normal_mb=300, postgres_warning_mb=500, postgres_high_mb=750, postgres_critical_mb=750,
        r2_endpoint=None, r2_access_key_id=None, r2_secret_access_key=None, r2_bucket="",
        r2_configured=False, r2_missing_env=("R2_ENDPOINT",), r2_invalid_env=(),
    )).snapshot()
    assert snapshot["postgres"]["bytes"] is None
    assert snapshot["r2"]["bytes"] is None and snapshot["r2"]["object_count"] is None


def test_historical_repository_excludes_rows_after_kickoff():
    store = InMemoryObjectStore()
    key = "matches/date=test.parquet"
    store.put_object(key, parquet_bytes([
        {"start_time": datetime(2026, 2, 1, 10, tzinfo=timezone.utc), "home_team": "A", "away_team": "B"},
        {"start_time": datetime(2026, 2, 1, 12, tzinfo=timezone.utc), "home_team": "A", "away_team": "C"},
    ]), content_type="application/vnd.apache.parquet")
    rows = HistoricalRepository(store).get_team_matches([key], before=datetime(2026, 2, 1, 11, tzinfo=timezone.utc), team="A")
    assert len(rows) == 1 and rows[0]["away_team"] == "B"


def test_small_sample_caps_confidence_and_gates_prediction():
    assert evidence_confidence(4, 0.99) <= 0.2
    assert make_prediction(market="1X2", selection="HOME", probability=.8, raw_confidence=.99, sample_size=4, decimal_odds=1.5) is None
    prediction = make_prediction(market="1X2", selection="HOME", probability=.7, raw_confidence=.99, sample_size=12, decimal_odds=1.5)
    assert prediction and prediction.evidence_status == EvidenceStatus.MODERATE and prediction.confidence < .99


def test_coupon_engine_returns_no_bet_without_value():
    prediction = make_prediction(market="1X2", selection="HOME", probability=.52, raw_confidence=.9, sample_size=30, decimal_odds=1.9)
    assert prediction is not None and build_coupon([("match-1", 1.9, prediction)], kind="SAFE") is None


def test_canonical_odds_normalizes_split_lines_and_preserves_raw_values():
    from app.providers.normalize import normalize_line
    assert normalize_line("0.5/1") == Decimal("0.750")
    assert normalize_line("-0.5/-1") == Decimal("-0.750")
    assert normalize_line("3/3.5") == Decimal("3.250")
    row = normalize_odds({"bookmaker": "Book A", "market": "Asian Handicap", "selection": "Home",
                          "handicap": "-0.5/-1", "odds": "2.05", "captured_at": "2026-11-13T11:00:00Z",
                          "period": "PREMATCH", "source": "provider"}, match_id="m-1")
    assert row["market"] == "AH" and row["selection"] == "HOME"
    assert row["line"] == Decimal("-0.750") and row["raw_line"] == "-0.5/-1"
    assert row["price"] == row["odds"] == Decimal("2.05")
    assert row["period"] == "PREMATCH" and row["source"] == "provider"
    contradictory_live = normalize_odds({"bookmaker": "Book A", "market": "1X2", "selection": "Home",
                                         "odds": "1.5", "captured_at": "2026-11-13T11:00:00Z",
                                         "period": "PREMATCH", "status": "r"}, match_id="m-1")
    assert contradictory_live["period"] == "LIVE"


def test_live_odds_cannot_be_closing():
    from app.analysis.odds import movement_percent, project_odds_snapshots
    kickoff = datetime(2026, 11, 13, 20, tzinfo=timezone.utc)
    as_of = datetime(2026, 11, 13, 21, tzinfo=timezone.utc)
    rows = [
        SimpleNamespace(bookmaker="Bet365", market="1X2", selection="HOME", line=None, line_key="",
                        odds=Decimal("2.1"), captured_at=kickoff.replace(hour=19), period="PREMATCH", status="AVAILABLE", source="SPORTMONKS"),
        SimpleNamespace(bookmaker="Bet365", market="1X2", selection="HOME", line=None, line_key="",
                        odds=Decimal("1.95"), captured_at=kickoff.replace(hour=19, minute=59), period="PREMATCH", status="AVAILABLE", source="SPORTMONKS"),
        SimpleNamespace(bookmaker="Bet365", market="1X2", selection="HOME", line=None, line_key="",
                        odds=Decimal("1.8"), captured_at=kickoff.replace(minute=5), period="LIVE", status="AVAILABLE", source="SPORTMONKS"),
        SimpleNamespace(bookmaker="Crown", market="1X2", selection="HOME", line=None, line_key="",
                        odds=Decimal("2.2"), captured_at=kickoff.replace(hour=19, minute=30), period="PREMATCH", status="AVAILABLE", source="SPORTMONKS"),
        SimpleNamespace(bookmaker="Bet365", market="1X2", selection="AWAY", line=None, line_key="",
                        odds=Decimal("3.0"), captured_at=as_of.replace(minute=30), period="PREMATCH", status="AVAILABLE", source="SPORTMONKS"),
    ]
    rows[2].captured_at = kickoff.replace(minute=5)
    projected, live = project_odds_snapshots(rows, kickoff=kickoff, match_status="FINISHED", as_of=as_of)
    home = next(row for row in projected if row["bookmaker"] == "Bet365" and row["selection"] == "HOME")
    assert home["opening"]["price"] == Decimal("2.1")
    assert home["latest"]["price"] == Decimal("1.95")
    assert home["closing"] is None
    assert len(live) == 1 and live[0]["price"] == Decimal("1.8")
    assert movement_percent(home["opening"]["price"], home["latest"]["price"]) == Decimal("-7.14")
    scheduled, _ = project_odds_snapshots(rows, kickoff=kickoff, match_status="SCHEDULED", as_of=as_of)
    assert all(row["closing"] is None for row in scheduled)


def test_match_detail_api_does_not_infer_closing_from_latest_or_live_odds():
    from app.api.routes import match_detail
    from app.models import ActiveMatch, ActiveOdds, Bookmaker

    engine = create_engine("sqlite://")
    ActiveMatch.__table__.create(engine)
    Bookmaker.__table__.create(engine)
    ActiveOdds.__table__.create(engine)
    now = datetime.now(timezone.utc)
    kickoff = now - timedelta(hours=3)
    with Session(engine) as db:
        bookmaker = Bookmaker(provider="sportmonks", provider_bookmaker_id="8", name="Bookmaker A",
                              source="SPORTMONKS", active=True)
        match = ActiveMatch(provider="sportmonks", provider_match_id="m-1", league_id="l-1",
                            league_name="League", season="2026-27", home_team="Home", away_team="Away",
                            start_time=kickoff, status="FINISHED", expires_at=now + timedelta(days=1))
        db.add_all([bookmaker, match]); db.flush()
        db.add_all([
            ActiveOdds(match_id=match.id, bookmaker_entity_id=bookmaker.id, provider="sportmonks",
                       provider_bookmaker_id="8", bookmaker="Bookmaker A", market="1X2", selection="HOME",
                       line_key="", odds=Decimal("2.1"), captured_at=kickoff - timedelta(hours=1),
                       source="SPORTMONKS", period="PREMATCH", status="AVAILABLE", expires_at=now + timedelta(days=1)),
            ActiveOdds(match_id=match.id, bookmaker_entity_id=bookmaker.id, provider="sportmonks",
                       provider_bookmaker_id="8", bookmaker="Bookmaker A", market="1X2", selection="HOME",
                       line_key="", odds=Decimal("1.8"), captured_at=kickoff + timedelta(minutes=5),
                       source="SPORTMONKS", period="LIVE", status="AVAILABLE", expires_at=now + timedelta(days=1)),
        ])
        db.flush()
        data = match_detail(match.id, db)
        assert data["odds_summary"][0]["closing"] is None
        assert len(data["live_odds"]) == 1 and data["live_odds"][0]["price"] == Decimal("1.8")
        assert data["odds_history"][0]["bookmaker_entity"]["name"] == "Bookmaker A"
    engine.dispose()


def test_lessons_require_evidence_and_changes_remain_proposals():
    assert generate_lessons([{"won": False, "confidence": .95}] * 5, minimum_sample=20) == []
    outcomes = [{"won": False, "confidence": .9}] * 7 + [{"won": True, "confidence": .7}] * 13
    assert generate_lessons(outcomes, minimum_sample=20)[0].category == "OVERCONFIDENCE"
    proposal = propose_parameter_change(parameter="confidence_floor", current_value="0.8", proposed_value="0.82", reason="calibration", evidence="20 outcomes", expected_effect="fewer low-quality selections")
    assert proposal.status == "PROPOSAL"


def test_provider_normalization_duplicate_identity_and_mock_mode():
    raw = {"id": 7, "homeTeam": {"name": "Home"}, "awayTeam": {"name": "Away"}, "utcDate": "2026-11-08T18:00:00Z"}
    assert len(normalize_matches([raw, raw], provider="sample-provider")) == 1
    with pytest.raises(ValueError, match="timezone"):
        normalize_match({**raw, "utcDate": "2026-11-08T18:00:00"}, provider="sample-provider")
    odds = normalize_odds({"bookmaker": "book", "market": "1X2", "selection": "HOME", "odds": "2.10", "timestamp": "2026-11-08T17:00:00Z"}, match_id="7")
    assert str(odds["odds"]) == "2.10"
    assert EmptyMockProvider().get_matches(date.today()) == []


def test_telegram_fingerprint_deduplicates_messages(monkeypatch):
    sent = []
    class Settings:
        telegram_bot_token = "test-token"
        telegram_chat_id = "test-chat"
    class Ledger:
        fingerprints = set()
        def claim(self, key):
            if key in self.fingerprints: return False
            self.fingerprints.add(key); return True
        def mark_sent(self, key): pass
        def mark_failed(self, key): pass
    class Response:
        def raise_for_status(self): pass
    class Client:
        async def post(self, *args, **kwargs):
            sent.append(kwargs["json"]); return Response()
    monkeypatch.setattr(telegram, "get_settings", lambda: Settings())
    service = telegram.TelegramService(Ledger(), Client())
    assert asyncio.run(service.send_prediction("text", "p-1")) is True
    assert asyncio.run(service.send_prediction("text", "p-1")) is False
    assert len(sent) == 1


def test_api_status_aliases_and_unconfigured_storage_are_real(monkeypatch):
    import app.api.routes as route_module
    from app.core.config import Settings

    monkeypatch.setattr(route_module, "get_settings", lambda: Settings(_env_file=None))
    monkeypatch.setattr(route_module, "provider_health", lambda: {
        "provider": "UNKNOWN", "status": "PROVIDER_NOT_CONFIGURED", "display_status": "NOT CONFIGURED",
        "latency_ms": None, "error_code": "PROVIDER_NOT_CONFIGURED", "last_successful_request": None,
        "coverage": {"leagues": [], "seasons": []},
    })
    client = TestClient(app)
    health = client.get("/health").json()
    status = client.get("/api/system/status").json()
    storage = client.get("/api/system/storage").json()
    assert health["provider"] == "PROVIDER_NOT_CONFIGURED"
    assert status["provider_mode"] == "PROVIDER_NOT_CONFIGURED"
    assert status["services"]["goaloo"] == "NOT_CONFIGURED"
    assert status["services"]["postgres"] == "NOT_CONFIGURED"
    assert status["services"]["r2"] == "NOT_CONFIGURED"
    assert status["services"]["groq"] == "NOT_CONFIGURED"
    assert storage["postgres"]["bytes"] is None
    assert storage["r2"]["bytes"] is None and storage["r2"]["object_count"] is None


def test_versionless_api_data_routes_return_unavailable_without_database():
    client = TestClient(app)
    for endpoint in ("matches", "predictions", "coupons", "performance", "lessons", "metrics/daily"):
        response = client.get(f"/api/{endpoint}")
        assert response.status_code == 503
        assert response.json()["detail"] in {"DATABASE_NOT_CONFIGURED", "DATABASE_OFFLINE"}


def test_provider_discovery_reports_not_configured_without_network_fallback(monkeypatch):
    import app.api.routes as route_module
    from app.core.config import Settings

    monkeypatch.setattr(route_module, "get_settings", lambda: Settings(_env_file=None))
    response = TestClient(app).get("/api/v1/provider/leagues")
    assert response.status_code == 503
    assert response.json()["detail"] == "PROVIDER_NOT_CONFIGURED"
from datetime import datetime, timedelta, timezone

from app.jobs.ledger import MemoryLedger, run_idempotent_job
from app.jobs.tasks import calculate_daily_metrics, cleanup_database, collect_matches


def test_job_ledger_is_idempotent_and_persists_skip_status():
    ledger = MemoryLedger()
    calls = []
    handler = lambda: (calls.append(1) or {"status": "SKIPPED", "reason": "PROVIDER_NOT_CONFIGURED", "records_processed": 0})
    first = run_idempotent_job(ledger, "collect:2026-11-08T12", "collect_matches", handler)
    second = run_idempotent_job(ledger, "collect:2026-11-08T12", "collect_matches", handler)
    assert first["status"] == "SKIPPED"
    assert second["reason"] == "IDEMPOTENT_DUPLICATE"
    assert len(calls) == 1
    assert ledger.runs["collect:2026-11-08T12"].finished_at is not None


def test_job_failures_store_exception_type_without_sensitive_message():
    ledger = MemoryLedger()
    result = run_idempotent_job(ledger, "failure:1", "example", lambda: (_ for _ in ()).throw(RuntimeError("token=secret")))
    assert result["status"] == "FAILED"
    assert result["reason"] == "RuntimeError"
    assert ledger.runs["failure:1"].error == "RuntimeError"
    assert "secret" not in ledger.runs["failure:1"].error


def test_job_history_retention_removes_only_completed_old_runs():
    ledger = MemoryLedger()
    old = ledger.claim("old", "example")[0]
    ledger.finish(old, "SUCCESS")
    old.started_at = datetime.now(timezone.utc) - timedelta(days=45)
    running, _ = ledger.claim("running", "example")
    running.started_at = datetime.now(timezone.utc) - timedelta(days=45)
    assert ledger.prune(30) == 1
    assert "old" not in ledger.runs
    assert ledger.runs["running"].status == "RUNNING"


def test_unavailable_inputs_are_explicitly_skipped_without_fake_metrics_or_cleanup():
    assert collect_matches()["status"] == "SKIPPED"
    assert calculate_daily_metrics()["reason"] == "DATABASE_NOT_CONFIGURED"
    assert cleanup_database()["reason"] == "DATABASE_NOT_CONFIGURED"


from decimal import Decimal
from types import SimpleNamespace

from sqlalchemy import create_engine
from sqlalchemy.orm import Session


def test_provider_registry_does_not_invent_live_adapter():
    from app.core.config import Settings
    from app.providers.base import ProviderNotConfigured, ProviderUnavailable
    from app.providers.registry import create_provider, provider_status
    assert provider_status(Settings(_env_file=None)) == "PROVIDER_NOT_CONFIGURED"
    configured = Settings(provider_name="unknown", provider_base_url="https://provider.example",
                          provider_api_key="not-a-real-secret", enabled_leagues="league-1")
    assert provider_status(configured) == "PROVIDER_UNAVAILABLE"
    with pytest.raises(ProviderUnavailable):
        create_provider(configured)
    with pytest.raises(ProviderNotConfigured):
        create_provider(Settings(match_provider="mock", provider_base_url="x", provider_api_key="x", enabled_leagues="l"))


def test_provider_caller_retries_rate_limits_with_bounded_backoff():
    from app.providers.base import ProviderRateLimited
    from app.providers.registry import ProviderCaller
    class Adapter:
        name = "test"
        calls = 0
        def get_leagues(self):
            self.calls += 1
            if self.calls == 1:
                raise ProviderRateLimited(retry_after=0)
            return [{"id": "league-1"}]
    adapter = Adapter()
    caller = ProviderCaller(adapter, requests_per_minute=6000, max_retries=2, sleep=lambda _: None, monotonic=lambda: 1)
    assert caller.call("get_leagues") == [{"id": "league-1"}]
    assert adapter.calls == 2


def test_normalize_result_accepts_only_final_scores():
    from app.providers.normalize import normalize_result
    assert normalize_result({"id": 5, "status": "IN_PLAY"}, provider="p")["status"] == "IN_PROGRESS"
    result = normalize_result({"id": 5, "status": "FT", "home_score": 2, "away_score": 1,
                               "finished_at": "2026-11-13T20:00:00Z"}, provider="p")
    assert result["status"] == "FINAL" and result["home_score"] == 2
    with pytest.raises(ValueError, match="integer"):
        normalize_result({"id": 5, "status": "FT", "home_score": None, "away_score": 0}, provider="p")


def test_upsert_match_and_odds_snapshots_are_idempotent():
    from app.jobs.ingestion import upsert_matches, upsert_odds
    from app.models import ActiveMatch, ActiveOdds, Bookmaker
    engine = create_engine("sqlite://")
    ActiveMatch.__table__.create(engine)
    Bookmaker.__table__.create(engine)
    ActiveOdds.__table__.create(engine)
    now = datetime(2026, 11, 13, 12, tzinfo=timezone.utc)
    match_data = normalize_match({"id": "m-1", "league_id": "league-1", "season": "2026-27",
                                  "home_team": "Home", "away_team": "Away",
                                  "start_time": "2026-11-13T20:00:00Z"}, provider="p")
    match_data["provider_season_id"] = "provider-only-season-id"
    with Session(engine) as db:
        assert upsert_matches(db, [match_data], now=now) == (1, 0)
        assert upsert_matches(db, [match_data], now=now) == (1, 0)
        match = db.query(ActiveMatch).one()
        assert match.season == "2026-27"
        payload = {"provider_odds_id": "o-1", "bookmaker": "book", "market": "1X2",
                   "selection": "HOME", "odds": "2.1", "captured_at": "2026-11-13T11:00:00Z",
                   "period": "PREMATCH", "source": "provider"}
        second_book = {**payload, "bookmaker": "Book B", "provider_bookmaker_id": "book-b"}
        over25 = {**payload, "market": "Over/Under", "selection": "Over", "line": "2.5"}
        over30 = {**over25, "line": "3/3.5"}
        assert upsert_odds(db, "p", match, [payload, second_book, over25, over30], now=now) == (4, 0)
        assert upsert_odds(db, "p", match, [payload], now=now) == (0, 1)
        assert db.query(ActiveOdds).count() == 4
        assert db.query(Bookmaker).count() == 2
        totals = db.query(ActiveOdds).filter(ActiveOdds.market == "OU").order_by(ActiveOdds.line_key).all()
        assert [(row.selection, row.line, row.raw_line) for row in totals] == [
            ("OVER", Decimal("2.500"), "2.5"), ("OVER", Decimal("3.250"), "3/3.5")]
    engine.dispose()


def test_prediction_settlement_and_daily_metrics_ignore_pending_records():
    from app.jobs.results import aggregate_daily_metrics, prediction_outcome, settle_verified_results
    from app.models import ActiveMatch, Coupon, CouponItem, DailyMetric, MatchResult, Prediction
    assert prediction_outcome("1X2", "HOME", 2, 1) is True
    assert prediction_outcome("BTTS", "NO", 2, 0) is True
    assert prediction_outcome("OVER_UNDER", "OVER_2.5", 2, 1) is True
    engine = create_engine("sqlite://")
    for model in (ActiveMatch, MatchResult, Prediction, Coupon, CouponItem, DailyMetric):
        model.__table__.create(engine)
    day = date(2026, 11, 13)
    start = datetime(2026, 11, 13, 18, tzinfo=timezone.utc)
    with Session(engine) as db:
        match = ActiveMatch(provider="p", provider_match_id="1", league_id="l", season="s",
                            home_team="H", away_team="A", start_time=start, status="FINISHED",
                            expires_at=start + timedelta(days=14))
        db.add(match); db.flush()
        db.add(MatchResult(match_id=match.id, provider="p", home_score=2, away_score=1,
                           status="FINAL", finished_at=start, result_source="p", verified_at=start))
        won = Prediction(match_id=match.id, market="1X2", selection="HOME", probability=Decimal(".6"),
                         confidence=Decimal(".7"), value_score=Decimal(".1"), evidence_status="STRONG",
                         model_version="v1", decimal_odds=Decimal("2"), paper_stake=Decimal("5"),
                         status="PENDING", created_at=start, expires_at=start + timedelta(days=1))
        pending = Prediction(match_id=match.id, market="UNSUPPORTED_TEST_MARKET", selection="YES", probability=Decimal(".6"),
                              confidence=Decimal(".7"), value_score=Decimal(".1"), evidence_status="STRONG",
                              model_version="v1", paper_stake=Decimal("5"), status="PENDING",
                              created_at=start, expires_at=start + timedelta(days=1))
        db.add_all([won, pending]); db.flush()
        coupon = Coupon(type="SAFE", total_odds=Decimal("2"), confidence=Decimal(".7"),
                        paper_stake=Decimal("3"), status="PAPER_PENDING",
                        expires_at=start + timedelta(days=1))
        db.add(coupon); db.flush()
        db.add(CouponItem(coupon_id=coupon.id, match_id=match.id, market="1X2", selection="HOME",
                          odds=Decimal("2"), prediction_id=won.id))
        db.commit()
        first = settle_verified_results(db, day=day)
        assert first["predictions_settled"] == 1
        second = settle_verified_results(db, day=day)
        assert second["predictions_settled"] == 0
        settled_coupon = db.query(Coupon).one()
        assert settled_coupon.status == "PAPER_WON"
        assert settled_coupon.paper_profit == Decimal("3")
        assert db.query(CouponItem).one().status == "WON"
        metric = aggregate_daily_metrics(db, day=day)
        assert metric.wins == 1 and metric.losses == 0 and metric.predictions_pending == 1
        assert metric.hit_rate == Decimal("1")
        assert metric.paper_pl == Decimal("5")
        assert metric.paper_stake == Decimal("5")
    engine.dispose()


def test_historical_query_excludes_future_odds_snapshot():
    store = InMemoryObjectStore()
    key = "odds/season=2026/league=l/date=2026-11-13.parquet"
    store.put_parquet(key, [
        {"captured_at": datetime(2026, 11, 13, 14, tzinfo=timezone.utc), "odds": 2.0},
        {"captured_at": datetime(2026, 11, 13, 17, tzinfo=timezone.utc), "odds": 3.0},
    ])
    rows = HistoricalRepository(store).get_historical_odds(
        [key], before=datetime(2026, 11, 13, 15, tzinfo=timezone.utc))
    assert [row["odds"] for row in rows] == [2.0]


def test_historical_query_excludes_result_until_after_verification_time():
    store = InMemoryObjectStore()
    key = "results/season=2026/league=l/date=2026-11-13.parquet"
    store.put_parquet(key, [{
        "finished_at": datetime(2026, 11, 13, 14, tzinfo=timezone.utc),
        "verified_at": datetime(2026, 11, 13, 17, tzinfo=timezone.utc),
        "home_score": 2, "away_score": 0,
    }])
    repository = HistoricalRepository(store)
    before_verification = repository._read([key], before=datetime(2026, 11, 13, 15, tzinfo=timezone.utc))
    after_verification = repository._read([key], before=datetime(2026, 11, 13, 18, tzinfo=timezone.utc))
    assert before_verification == []
    assert len(after_verification) == 1 and after_verification[0]["home_score"] == 2


def test_run_ledger_failure_is_retryable_and_has_bounded_error_detail():
    ledger = MemoryLedger()
    result = run_idempotent_job(ledger, "retry-me", "collect_matches",
                                lambda: (_ for _ in ()).throw(RuntimeError("token=secret")))
    assert result["status"] == "FAILED"
    assert ledger.runs["retry-me"].error_code == "RUNTIMEERROR"
    run, claimed = ledger.claim("retry-me", "collect_matches")
    assert claimed and run.status == "RUNNING"
    assert ledger.prune(0) == 0


def test_interrupted_durable_job_becomes_retryable_without_stale_wait():
    ledger = MemoryLedger()
    with pytest.raises(KeyboardInterrupt):
        run_idempotent_job(ledger, "resume:1", "historical_ingest", lambda: (_ for _ in ()).throw(KeyboardInterrupt()))
    assert ledger.runs["resume:1"].status == "PARTIAL"
    run, claimed = ledger.claim("resume:1", "historical_ingest")
    assert claimed and run.status == "RUNNING"


def test_historical_backfill_checkpoints_valid_rows_and_retries_only_failed_day(monkeypatch):
    from app.core.config import Settings
    from app.jobs.ingestion import historical_ingest
    from app.models import ArchiveJob, HistoricalIngestDay
    from app.providers.registry import ProviderCaller

    engine = create_engine("sqlite://")
    ArchiveJob.__table__.create(engine)
    HistoricalIngestDay.__table__.create(engine)
    monkeypatch.setattr("app.jobs.ingestion.get_settings",
                        lambda: Settings(enabled_leagues="league-1", enabled_seasons="2026-27"))

    class Adapter:
        name = "test-provider"
        def get_leagues(self): return [{"id": "league-1"}]
        def get_matches(self, day): return [
            {"id": "valid-1", "league_id": "league-1", "season": "2026-27",
             "status": "CANCELLED",
             "home_team": "Home", "away_team": "Away", "start_time": "2026-11-13T18:00:00Z"},
            {"id": "valid-1", "league_id": "league-1", "season": "2026-27",
             "status": "CANCELLED",
             "home_team": "Home", "away_team": "Away", "start_time": "2026-11-13T18:00:00Z"},
            {"id": "other-season", "league_id": "league-1", "season": "2025-26",
             "home_team": "Old Home", "away_team": "Old Away", "start_time": "2026-11-13T17:00:00Z"},
            {"league_id": "league-1", "home_team": "Broken", "away_team": "Row",
             "start_time": "2026-11-13T19:00:00Z"},
        ]
        def get_results(self, day): return []

    caller = ProviderCaller(Adapter(), requests_per_minute=100000, max_retries=1)
    store = InMemoryObjectStore()
    day = date(2026, 11, 13)
    with Session(engine) as db:
        first = historical_ingest(db, caller, store, season="2026-27", league="league-1",
                                  start_date=day, end_date=day)
        checkpoint = db.query(HistoricalIngestDay).one()
        assert first["status"] == "PARTIAL" and first["records_written"] == 1
        assert first["matches_written"] == 1 and first["duplicates"] == 1
        assert first["postgres_rows_written"] == first["postgres_rows_deleted"] == 0
        assert checkpoint.status == "PARTIAL" and checkpoint.attempt_count == 1
        assert checkpoint.records_failed == 1 and checkpoint.failed_match_ids == ["unknown"]
        second = historical_ingest(db, caller, store, season="2026-27", league="league-1",
                                   start_date=day, end_date=day)
        assert second["status"] == "PARTIAL"
        assert checkpoint.attempt_count == 2 and checkpoint.status == "PARTIAL"
        archived = store.read_parquet("matches/season=2026-27/league=league-1/date=2026-11-13.parquet")
        assert archived[0]["provider_match_id"] == "valid-1"
        assert "other-season" not in {row["provider_match_id"] for row in archived}
    engine.dispose()


def test_historical_ingestion_storage_guard_stops_only_at_critical(monkeypatch):
    from app.core.config import Settings
    from app.jobs.ingestion import historical_ingest
    from app.storage.monitor import StorageMonitor

    engine = create_engine("sqlite://")
    settings = Settings(enabled_leagues="league-1", enabled_seasons="2026-27")
    monkeypatch.setattr("app.jobs.ingestion.get_settings", lambda: settings)
    monkeypatch.setattr(StorageMonitor, "historical_ingestion_status", lambda self: "critical")

    class MustNotCallProvider:
        name = "test-provider"

    with Session(engine) as db:
        result = historical_ingest(db, MustNotCallProvider(), InMemoryObjectStore(),
                                   season="2026-27", league="league-1",
                                   start_date=date(2026, 11, 13), end_date=date(2026, 11, 13))
    assert result["status"] == "BLOCKED"
    assert result["error_code"] == "STORAGE_CRITICAL"
    assert result["records_written"] == 0
    engine.dispose()


def test_historical_ingest_skips_unknown_league_and_unavailable_season(monkeypatch):
    from app.core.config import Settings
    from app.jobs.ingestion import historical_ingest
    from app.providers.registry import ProviderCaller

    settings = Settings(enabled_leagues="league-1,unknown", enabled_seasons="2024-2025")
    monkeypatch.setattr("app.jobs.ingestion.get_settings", lambda: settings)
    engine = create_engine("sqlite://")

    class Adapter:
        name = "test-provider"
        def get_leagues(self): return [{"id": "league-1"}]
        def get_seasons(self, league): return [{"id": "2025-2026"}]

    caller = ProviderCaller(Adapter(), requests_per_minute=100000, max_retries=1)
    with Session(engine) as db:
        wrong_league = historical_ingest(db, caller, InMemoryObjectStore(), season="2024-2025",
                                         league="unknown", start_date=date(2024, 7, 1), end_date=date(2024, 7, 1))
        unavailable = historical_ingest(db, caller, InMemoryObjectStore(), season="2024-2025",
                                        league="league-1", start_date=date(2024, 7, 1), end_date=date(2024, 7, 1))
    assert wrong_league["error_code"] == "LEAGUE_NOT_AVAILABLE"
    assert unavailable["error_code"] == "SEASON_NOT_AVAILABLE"
    engine.dispose()


def test_historical_ingest_archives_only_goaloo_opening_and_latest_odds(monkeypatch):
    from app.core.config import Settings
    from app.jobs.ingestion import historical_ingest
    from app.models import ArchiveJob, HistoricalIngestDay
    from app.providers.registry import ProviderCaller

    settings = Settings(enabled_leagues="league-1", enabled_seasons="2025-2026")
    monkeypatch.setattr("app.jobs.ingestion.get_settings", lambda: settings)
    engine = create_engine("sqlite://")
    ArchiveJob.__table__.create(engine)
    HistoricalIngestDay.__table__.create(engine)

    class Adapter:
        name = "test-provider"
        def get_leagues(self): return [{"id": "league-1"}]
        def get_matches(self, day): return [{"id": "m1", "league_id": "league-1", "season": "2025-2026",
            "home_team": "Home", "away_team": "Away", "status": "FINISHED",
            "start_time": "2025-08-10T18:00:00Z"}]
        def get_results(self, day): return []
        def get_odds(self, match_id): return [
            {"provider": "test-provider", "provider_odds_id": "opening", "market": "1X2", "selection": "HOME",
             "odds": Decimal("2.1"), "snapshot_type": "OPENING", "period": "PREMATCH", "bookmaker": "Book"},
            {"provider": "test-provider", "provider_odds_id": "latest", "market": "1X2", "selection": "HOME",
             "odds": Decimal("2.0"), "snapshot_type": "LATEST", "period": "PREMATCH", "bookmaker": "Book"},
            {"provider": "test-provider", "provider_odds_id": "live", "market": "1X2", "selection": "HOME",
             "odds": Decimal("1.8"), "snapshot_type": "LIVE", "period": "LIVE", "bookmaker": "Book"},
        ]

    caller = ProviderCaller(Adapter(), requests_per_minute=100000, max_retries=1)
    store = InMemoryObjectStore()
    day = date(2025, 8, 10)
    with Session(engine) as db:
        result = historical_ingest(db, caller, store, season="2025-2026", league="league-1",
                                   start_date=day, end_date=day)
        rows = store.read_parquet("odds/season=2025-2026/league=league-1/date=2025-08-10.parquet")
    assert result["odds_written"] == 2
    assert {row["snapshot_type"] for row in rows} == {"OPENING", "LATEST"}
    assert all(row["source_captured_at"] is None for row in rows)
    assert all(row["retrieved_at"] is not None for row in rows)
    engine.dispose()


def test_historical_odds_do_not_overwrite_existing_verified_partition(monkeypatch):
    from app.core.config import Settings
    from app.jobs.ingestion import archive_rows, historical_ingest
    from app.models import ArchiveJob, HistoricalIngestDay
    from app.providers.registry import ProviderCaller
    from app.storage.archive import SQLArchiveTracker

    settings = Settings(enabled_leagues="league-1", enabled_seasons="2025-2026")
    monkeypatch.setattr("app.jobs.ingestion.get_settings", lambda: settings)
    engine = create_engine("sqlite://")
    ArchiveJob.__table__.create(engine)
    HistoricalIngestDay.__table__.create(engine)

    class Adapter:
        name = "test-provider"
        def get_leagues(self): return [{"id": "league-1"}]
        def get_matches(self, day): return [{"id": "m1", "league_id": "league-1", "season": "2025-2026",
            "home_team": "Home", "away_team": "Away", "status": "FINISHED",
            "start_time": "2025-08-10T18:00:00Z"}]
        def get_results(self, day): return []
        def get_odds(self, match_id): return [{"provider": "test-provider", "provider_odds_id": "opening",
            "market": "1X2", "selection": "HOME", "odds": Decimal("2.1"),
            "snapshot_type": "OPENING", "period": "PREMATCH", "bookmaker": "Book"}]

    caller = ProviderCaller(Adapter(), requests_per_minute=100000, max_retries=1)
    store = InMemoryObjectStore()
    day = date(2025, 8, 10)
    with Session(engine) as db:
        archive_rows(store, SQLArchiveTracker(db), dataset="odds", season="2025-2026", league="league-1",
                     day=day, rows=[{"provider_odds_id": "existing", "snapshot_type": "OPENING", "odds": 2.5}])
        original = store.read_parquet("odds/season=2025-2026/league=league-1/date=2025-08-10.parquet")
        result = historical_ingest(db, caller, store, season="2025-2026", league="league-1",
                                   start_date=day, end_date=day)
        preserved = store.read_parquet("odds/season=2025-2026/league=league-1/date=2025-08-10.parquet")
    assert result["odds_written"] == 1 and result["odds_skipped_conflict"] == 0
    assert preserved == original
    engine.dispose()


def test_interrupted_historical_day_releases_lease_for_immediate_resume(monkeypatch):
    from app.core.config import Settings
    from app.jobs.ingestion import historical_ingest
    from app.models import ArchiveJob, HistoricalIngestDay
    from app.providers.registry import ProviderCaller

    engine = create_engine("sqlite://")
    ArchiveJob.__table__.create(engine)
    HistoricalIngestDay.__table__.create(engine)
    monkeypatch.setattr("app.jobs.ingestion.get_settings",
                        lambda: Settings(enabled_leagues="league-1", enabled_seasons="2026-27"))

    class Adapter:
        name = "test-provider"
        calls = 0
        def get_leagues(self): return [{"id": "league-1"}]
        def get_matches(self, day):
            self.calls += 1
            if self.calls == 1:
                raise KeyboardInterrupt
            return [{"id": "m1", "league_id": "league-1", "season": "2026-27",
                     "home_team": "Home", "away_team": "Away", "status": "CANCELLED", "start_time": "2026-11-13T18:00:00Z"}]
        def get_results(self, day): return []

    adapter = Adapter()
    caller = ProviderCaller(adapter, requests_per_minute=100000, max_retries=1)
    store = InMemoryObjectStore()
    day = date(2026, 11, 13)
    with Session(engine) as db:
        with pytest.raises(KeyboardInterrupt):
            historical_ingest(db, caller, store, season="2026-27", league="league-1",
                              start_date=day, end_date=day)
        checkpoint = db.query(HistoricalIngestDay).one()
        assert checkpoint.status == "PARTIAL" and checkpoint.lease_token is None
        resumed = historical_ingest(db, caller, store, season="2026-27", league="league-1",
                                    start_date=day, end_date=day)
        assert resumed["status"] == "SUCCESS"
        assert checkpoint.attempt_count == 2
    engine.dispose()


def test_provider_timeout_is_retried_and_then_classified_unavailable():
    import httpx
    from app.providers.base import ProviderUnavailable
    from app.providers.registry import ProviderCaller
    class Adapter:
        name = "timeout-test"
        calls = 0
        def get_leagues(self):
            self.calls += 1
            raise httpx.ReadTimeout("slow")
    adapter = Adapter()
    caller = ProviderCaller(adapter, requests_per_minute=6000, max_retries=2,
                            sleep=lambda _: None, monotonic=lambda: 1)
    with pytest.raises(ProviderUnavailable, match="timed out"):
        caller.call("get_leagues")
    assert adapter.calls == 2


def test_cleanup_reverifies_r2_checksum_before_deleting_expired_odds():
    from app.jobs.ingestion import archive_rows, cleanup_verified_archives
    from app.models import ActiveMatch, ActiveOdds, ArchiveJob, Bookmaker
    store = InMemoryObjectStore()
    engine = create_engine("sqlite://")
    for model in (ActiveMatch, Bookmaker, ActiveOdds, ArchiveJob):
        model.__table__.create(engine)
    day = date(2026, 11, 10)
    captured = datetime(2026, 11, 10, 12, tzinfo=timezone.utc)
    with Session(engine) as db:
        match = ActiveMatch(provider="p", provider_match_id="old", league_id="league-1", season="2026-27",
                            home_team="H", away_team="A", start_time=captured, status="FINISHED",
                            expires_at=captured + timedelta(days=1))
        db.add(match); db.flush()
        db.add(ActiveOdds(match_id=match.id, provider="p", bookmaker="b", market="1X2",
                          selection="HOME", odds=Decimal("2"), captured_at=captured,
                          expires_at=captured + timedelta(days=1)))
        db.commit()
        rows = [{"captured_at": captured, "match_id": str(match.id), "odds": 2.0}]
        archive_rows(store, __import__("app.storage.archive", fromlist=["SQLArchiveTracker"]).SQLArchiveTracker(db),
                     dataset="odds", season="2026-27", league="league-1", day=day, rows=rows)
        store.put_object("odds/season=2026-27/league=league-1/date=2026-11-10.parquet",
                         b"tampered", content_type="application/vnd.apache.parquet",
                         metadata={"sha256": "bad", "row-count": "1", "archive-key": "bad"})
        assert cleanup_verified_archives(db, store, today=date(2026, 11, 13)) == 0
        assert db.query(ActiveOdds).count() == 1
        job = db.query(ArchiveJob).one()
        assert job.status == "VERIFIED"
    engine.dispose()


def test_cleanup_deletes_only_after_live_object_reverification():
    from app.jobs.ingestion import archive_rows, cleanup_verified_archives
    from app.models import ActiveMatch, ActiveOdds, ArchiveJob, Bookmaker
    from app.storage.archive import SQLArchiveTracker
    store = InMemoryObjectStore()
    engine = create_engine("sqlite://")
    for model in (ActiveMatch, Bookmaker, ActiveOdds, ArchiveJob):
        model.__table__.create(engine)
    day = date(2026, 11, 10)
    captured = datetime(2026, 11, 10, 12, tzinfo=timezone.utc)
    with Session(engine) as db:
        match = ActiveMatch(provider="p", provider_match_id="old", league_id="league-1", season="2026-27",
                            home_team="H", away_team="A", start_time=captured, status="FINISHED",
                            expires_at=captured + timedelta(days=1))
        db.add(match); db.flush()
        db.add(ActiveOdds(match_id=match.id, provider="p", bookmaker="b", market="1X2",
                          selection="HOME", odds=Decimal("2"), captured_at=captured,
                          expires_at=captured + timedelta(days=1)))
        other_match = ActiveMatch(provider="p", provider_match_id="other-league", league_id="league-2",
                                  season="2026-27", home_team="H2", away_team="A2", start_time=captured,
                                  status="FINISHED", expires_at=captured + timedelta(days=1))
        db.add(other_match); db.flush()
        db.add(ActiveOdds(match_id=other_match.id, provider="p", bookmaker="b", market="1X2",
                          selection="HOME", odds=Decimal("2"), captured_at=captured,
                          expires_at=captured + timedelta(days=1)))
        db.commit()
        archived_odds = db.query(ActiveOdds).filter_by(match_id=match.id).one()
        archived_row = {column.name: getattr(archived_odds, column.name) for column in ActiveOdds.__table__.columns}
        archived_row = {name: float(value) if isinstance(value, Decimal) else str(value) if name in {"id", "match_id", "bookmaker_entity_id"} and value is not None else value
                        for name, value in archived_row.items()}
        archived_row["odds_id"] = str(archived_odds.id)
        archive_rows(store, SQLArchiveTracker(db), dataset="odds", season="2026-27", league="league-1",
                     day=day, rows=[archived_row])
        assert cleanup_verified_archives(db, store, today=date(2026, 11, 13)) == 1
        assert db.query(ActiveOdds).count() == 1
        assert db.query(ActiveOdds).one().match_id == other_match.id
        assert db.query(ArchiveJob).one().status == "DELETED"
    engine.dispose()


def test_verified_match_archive_allows_expired_active_match_cleanup():
    from app.jobs.ingestion import archive_rows, cleanup_verified_archives
    from app.models import ActiveMatch, ActiveOdds, ArchiveJob, Bookmaker, Coupon, CouponItem, MatchAnalysis, MatchResult, Prediction
    from app.storage.archive import SQLArchiveTracker

    engine = create_engine("sqlite://")
    for model in (ActiveMatch, Bookmaker, ActiveOdds, MatchResult, MatchAnalysis, Prediction, Coupon, CouponItem, ArchiveJob):
        model.__table__.create(engine)
    store = InMemoryObjectStore()
    day = date(2025, 1, 10)
    kickoff = datetime(2025, 1, 10, 18, tzinfo=timezone.utc)
    with Session(engine) as db:
        match = ActiveMatch(provider="goaloo", provider_match_id="old-match", league_id="36",
                            league_name="English Premier League", season="2024-2025",
                            home_team="Home", away_team="Away", start_time=kickoff,
                            status="FINISHED", expires_at=kickoff + timedelta(days=14))
        db.add(match)
        db.commit()
        assert cleanup_verified_archives(db, store, today=date(2025, 2, 1)) == 0
        assert db.query(ActiveMatch).count() == 1
        archive_rows(store, SQLArchiveTracker(db), dataset="matches", season="2024-2025", league="36",
                     day=day, rows=[{"provider": "goaloo", "provider_match_id": "old-match", "league_id": "36",
                                     "season": "2024-2025", "home_team": "Home", "away_team": "Away",
                                     "start_time": kickoff, "status": "FINISHED"}])
        assert cleanup_verified_archives(db, store, today=date(2025, 2, 1)) == 1
        assert db.query(ActiveMatch).count() == 0
    engine.dispose()


def test_verified_match_archive_preserves_prediction_and_linked_match():
    from app.jobs.ingestion import archive_rows, cleanup_verified_archives
    from app.models import ActiveMatch, ActiveOdds, ArchiveJob, Bookmaker, Coupon, CouponItem, MatchAnalysis, MatchResult, Prediction
    from app.storage.archive import SQLArchiveTracker

    engine = create_engine("sqlite://")
    for model in (ActiveMatch, Bookmaker, ActiveOdds, MatchResult, MatchAnalysis, Prediction, Coupon, CouponItem, ArchiveJob):
        model.__table__.create(engine)
    store = InMemoryObjectStore()
    day = date(2025, 1, 10)
    kickoff = datetime(2025, 1, 10, 18, tzinfo=timezone.utc)
    with Session(engine) as db:
        match = ActiveMatch(provider="goaloo", provider_match_id="paper-match", league_id="36",
                            league_name="English Premier League", season="2024-2025",
                            home_team="Home", away_team="Away", start_time=kickoff,
                            status="FINISHED", expires_at=kickoff + timedelta(days=14))
        db.add(match)
        db.flush()
        prediction = Prediction(match_id=match.id, market="1X2", selection="HOME",
                                probability=Decimal("0.55"), confidence=Decimal("0.55"),
                                value_score=Decimal("0.05"), evidence_status="VERIFIED",
                                model_version="test", paper_stake=Decimal("1"), status="WON",
                                expires_at=kickoff + timedelta(days=30))
        db.add(prediction)
        db.commit()
        archive_rows(store, SQLArchiveTracker(db), dataset="matches", season="2024-2025", league="36",
                     day=day, rows=[{"provider": "goaloo", "provider_match_id": "paper-match", "league_id": "36",
                                     "season": "2024-2025", "home_team": "Home", "away_team": "Away",
                                     "start_time": kickoff, "status": "FINISHED"}])
        assert cleanup_verified_archives(db, store, today=date(2025, 2, 1)) == 0
        assert db.query(Prediction).count() == 1
        assert db.query(ActiveMatch).count() == 1
    engine.dispose()
