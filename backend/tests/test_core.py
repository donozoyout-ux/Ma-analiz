import asyncio
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
    old_job = SimpleNamespace(status="VERIFIED", checksum="sha256", verified_at=datetime.now(timezone.utc), row_count=3, archive_date=date(2020, 1, 1), deleted_at=None)
    session, deleted = FakeSession(old_job), []
    cleanup_database_rows_after_verified(session, key="matches/x", delete_rows=lambda db, job: deleted.append((db, job)))
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
    monkeypatch.setattr("app.storage.r2.get_settings", lambda: SimpleNamespace(r2_endpoint=None, r2_access_key_id=None, r2_secret_access_key=None, r2_bucket=""))
    with pytest.raises(R2NotConfigured, match="R2_NOT_CONFIGURED"):
        R2ObjectStore()


def test_storage_thresholds_and_unconfigured_sizes_are_honest():
    settings = SimpleNamespace(postgres_normal_mb=300, postgres_warning_mb=500, postgres_high_mb=750, postgres_critical_mb=1000)
    mib = 1024 * 1024
    assert postgres_status(299 * mib, settings) == "healthy"
    assert postgres_status(400 * mib, settings) == "warning"
    assert postgres_status(600 * mib, settings) == "high"
    assert postgres_status(800 * mib, settings) == "high"
    assert postgres_status(1000 * mib, settings) == "critical"
    snapshot = StorageMonitor(None, settings=SimpleNamespace(
        postgres_normal_mb=300, postgres_warning_mb=500, postgres_high_mb=750, postgres_critical_mb=750,
        r2_endpoint=None, r2_access_key_id=None, r2_secret_access_key=None, r2_bucket="",
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


def test_api_status_aliases_and_unconfigured_storage_are_real():
    client = TestClient(app)
    health = client.get("/health").json()
    status = client.get("/api/system/status").json()
    storage = client.get("/api/system/storage").json()
    assert health["provider"] == "PROVIDER_NOT_CONFIGURED"
    assert status["provider_mode"] == "NOT_CONFIGURED"
    assert storage["postgres"]["bytes"] is None
    assert storage["r2"]["bytes"] is None and storage["r2"]["object_count"] is None


def test_versionless_api_data_routes_return_unavailable_without_database():
    client = TestClient(app)
    for endpoint in ("matches", "predictions", "coupons", "performance", "lessons", "metrics/daily"):
        response = client.get(f"/api/{endpoint}")
        assert response.status_code == 503
        assert response.json()["detail"] in {"DATABASE_NOT_CONFIGURED", "DATABASE_OFFLINE"}
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
    assert calculate_daily_metrics()["reason"] == "NO_RESULT_SOURCE"
    assert cleanup_database()["reason"] == "NO_VERIFIED_ARCHIVE_CANDIDATES"
