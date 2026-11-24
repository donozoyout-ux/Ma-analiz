"""Stage 7D.1 synthetic validation. SQLite FK enforcement + memory R2 ONLY."""
from copy import deepcopy
from datetime import timedelta
from decimal import Decimal
import json

import pytest
from sqlalchemy import event
from sqlalchemy.exc import IntegrityError

from app.jobs import retention_validation as validation
from app.models import ActiveMatch, JobRun, MatchAnalysis, MatchResult, Prediction, SystemSetting, User
from app.storage import lifecycle as lc
from app.storage.r2 import InMemoryObjectStore
from test_lifecycle import NOW, fixture, session


def inspect(session, store, **kwargs):
    return validation.inspect_records(session, store, now=NOW, **kwargs)


def test_no_rows_is_no_eligible_not_a_claim_that_delete_was_tested(session):
    result = inspect(session, InMemoryObjectStore())
    assert result["status"] == "NO_ELIGIBLE_RECORDS" and result["over_48h"] == result["eligible"] == 0
    assert result["scan_complete"] and result["LIVE_DELETE"] == "NOT_RUN"


def test_full_offline_auto_delete_history_metadata_and_repeat(session):
    from app.historical.repository import HistoricalRepository
    match, result = fixture(session, hours=50)
    user = User(); session.add(user)
    protected = {"app-settings": {"synthetic": True}, "review-source:offline": {"identity": "synthetic"},
        "review-evidence:offline": {"synthetic": True}, "review-audit:offline": {"synthetic": True},
        "operator-import:offline": {"synthetic": True}}
    for key, value in protected.items(): session.add(SystemSetting(key=key, value=value))
    session.add(JobRun(idempotency_key="old-critical-audit", job_type="synthetic", status="SUCCESS", started_at=NOW-timedelta(days=365)))
    session.commit()
    store = InMemoryObjectStore()
    lc.archive_early(session, store, now=NOW)
    original = deepcopy(store._objects)
    plan = inspect(session, store)
    assert plan["status"] == "ELIGIBLE_RECORDS_FOUND" and plan["over_48h"] == plan["eligible"] == 2
    assert all(row["archive_verification"] == row["source_identity"] == "PASS" for row in plan["records"])
    # Observe real SQL DELETE statements executed in this isolated engine.
    deletes = []
    def observe(conn, cursor, statement, parameters, context, many):
        if statement.lstrip().upper().startswith("DELETE "): deletes.append(statement.split()[2])
    event.listen(session.get_bind(), "before_cursor_execute", observe)
    try:
        cleanup = lc.automatic(session, store, enabled=True, now=NOW)
    finally:
        event.remove(session.get_bind(), "before_cursor_execute", observe)
    assert cleanup["deleted"] == 2 and cleanup["LIVE_DELETE"] == "EXECUTED"
    assert set(deletes) == {"match_results", "active_matches"}
    assert session.query(ActiveMatch).count() == session.query(MatchResult).count() == 0
    assert session.get(User, user.id) is not None
    for key, value in protected.items(): assert session.get(SystemSetting, key).value == value
    assert session.query(JobRun).filter_by(idempotency_key="old-critical-audit").count() == 1
    index = session.get(SystemSetting, lc.source_index_key(match))
    assert index.value["match_id"] == str(match.id) and index.value["operational_removed_at"]
    start = lc.aware(match.start_time).replace(hour=0, minute=0, second=0, microsecond=0)
    history = HistoricalRepository(store).get_similarity_history(league="de.bl1", season="2026-2027",
        start=start, end=start+timedelta(days=1), before=NOW)
    assert len(history) == 1 and history[0]["home_score"] == history[0]["away_score"] == 1
    repeat = lc.automatic(session, store, enabled=True, now=NOW)
    assert repeat["deleted"] == 0 and repeat["LIVE_DELETE"] == "NOT_RUN"
    assert store._objects == original and session.query(JobRun).filter_by(job_type="retention_cleanup").count() == 1
    assert inspect(session, store)["status"] == "NO_ELIGIBLE_RECORDS"


@pytest.mark.parametrize("blocker", ["scheduled", "prediction", "analysis", "corrupt", "partial", "identity"])
def test_blocked_bundle_is_retained_by_actual_offline_auto_path(session, monkeypatch, blocker):
    match, _ = fixture(session)
    if blocker == "scheduled": match.status = "SCHEDULED"
    if blocker == "prediction":
        session.add(Prediction(match_id=match.id, market="1X2", selection="DRAW", probability=Decimal(".3"),
            confidence=Decimal(".5"), value_score=0, evidence_status="synthetic", model_version="synthetic",
            status="PENDING", analysis_timestamp=NOW-timedelta(hours=50), expires_at=NOW,
            created_at=NOW-timedelta(hours=50)))
    if blocker == "analysis":
        session.add(MatchAnalysis(match_id=match.id, analysis_timestamp=NOW-timedelta(hours=50),
            analysis_status="RUNNING", final_decision="NO BET", model_version="synthetic", feature_version="synthetic", analysis_output="{}"))
    session.commit()
    store = InMemoryObjectStore(); lc.archive_early(session, store, now=NOW)
    if blocker == "identity":
        index = session.get(SystemSetting, lc.source_index_key(match)); index.value = {"match_id": "conflict"}; session.commit()
    if blocker == "corrupt":
        key = next(iter(store._objects)); body, mime, meta = store._objects[key]
        store._objects[key] = (b"synthetic corruption", mime, meta)
    if blocker == "partial":
        store._objects.pop(next(iter(store._objects)))  # offline fault injection, not a production delete
        def deny_repair(*args, **kwargs): raise TimeoutError("synthetic upload failure")
        monkeypatch.setattr(store, "put_object_if_absent", deny_repair)
    before = deepcopy(store._objects)
    plan = inspect(session, store)
    assert plan["eligible"] == 0 and plan["over_48h"] >= 2
    cleanup = lc.automatic(session, store, enabled=True, now=NOW)
    assert cleanup["deleted"] == 0 and cleanup["LIVE_DELETE"] == "NOT_RUN"
    assert session.query(ActiveMatch).count() == session.query(MatchResult).count() == 1
    assert store._objects == before
    assert session.query(JobRun).filter_by(job_type="retention_cleanup").count() == 0


def test_duplicate_native_insert_is_refused_without_touching_original(session):
    match, _ = fixture(session)
    duplicate = ActiveMatch(provider=match.provider, provider_match_id=match.provider_match_id,
        league_id="de.bl1", season="2026-2027", home_team="Synthetic", away_team="Synthetic2",
        start_time=match.start_time, expires_at=match.expires_at)
    session.add(duplicate)
    with pytest.raises(IntegrityError): session.commit()
    session.rollback()
    assert session.query(ActiveMatch).one().id == match.id
    assert inspect(session, InMemoryObjectStore())["eligible"] == 0


def test_report_paginates_and_is_honest_about_scan_limit(session):
    fixture(session, source_id="synthetic-1"); fixture(session, source_id="synthetic-2")
    store = InMemoryObjectStore(); lc.archive_early(session, store, now=NOW)
    limited = inspect(session, store, batch_size=1, max_pages=1)
    assert limited["status"] == "INCOMPLETE_SCAN" and not limited["scan_complete"]
    assert limited["datasets"]["matches"]["not_scanned"] == 1
    full = inspect(session, store, batch_size=1, max_pages=2)
    assert full["scan_complete"] and full["eligible"] == full["over_48h"] == 4
    assert len({r["record"] for r in full["records"]}) == 4


def test_unseen_orphan_coupon_cannot_be_claimed_scanned(session):
    from app.models import Coupon
    session.add(Coupon(type="SAFE", confidence=Decimal(".5"), status="PAPER_WON", total_odds=1,
        expires_at=NOW, settled_at=NOW-timedelta(hours=50), created_at=NOW-timedelta(hours=50)))
    session.commit()
    result = inspect(session, InMemoryObjectStore())
    assert result["status"] == "INCOMPLETE_SCAN" and result["datasets"]["coupons"]["not_scanned"] == 1


def test_report_is_read_only_even_when_eligible(session, monkeypatch):
    fixture(session); store = InMemoryObjectStore(); lc.archive_early(session, store, now=NOW)
    before = deepcopy(store._objects)
    monkeypatch.setattr(session, "commit", lambda: pytest.fail("inspection committed"))
    monkeypatch.setattr(session, "delete", lambda *a: pytest.fail("inspection deleted"))
    monkeypatch.setattr(store, "put_object_if_absent", lambda *a, **k: pytest.fail("inspection wrote R2"))
    assert inspect(session, store)["eligible"] == 2 and store._objects == before


@pytest.mark.parametrize("size,status", [(699999999,"HEALTHY"), (700000000,"WARNING"),
    (800000000,"HIGH"), (900000000,"CRITICAL"), (1000000000,"CRITICAL")])
def test_capacity_alerts_are_explicit_and_never_trigger_delete(monkeypatch, capsys, size, status):
    from app.jobs.actions_retention import emit
    from app.storage.monitor import database_capacity
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    monkeypatch.delenv("GITHUB_STEP_SUMMARY", raising=False)
    result = database_capacity(size)
    assert result["status"] == status
    emit({"capacity_status": status, "LIVE_DELETE": "NOT_RUN"}, 0)
    output = capsys.readouterr().out
    assert ("::warning::POSTGRES_CAPACITY_" in output) == (status != "HEALTHY")
    assert "NOT_RUN" in output


def test_error_annotation_and_secrets_scrubbing(monkeypatch, capsys):
    from app.jobs.actions_retention import emit
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    monkeypatch.setenv("DATABASE_URL", "postgresql://SYNTHETIC_SECRET")
    monkeypatch.delenv("GITHUB_STEP_SUMMARY", raising=False)
    emit({"status": "FAILED", "error_code": "SAFE_FIXED_CODE", "detail": "postgresql://SYNTHETIC_SECRET"}, 1)
    output = capsys.readouterr().out
    assert "::error::Retention failed" in output and "SYNTHETIC_SECRET" not in output


def test_metadata_inventory_only_exports_counts_not_values(session):
    session.add(User()); session.add(SystemSetting(key="review-evidence:synthetic", value={"private": "NEVER_EXPORT"}))
    session.commit()
    result = validation.protected_counts(session)
    assert result["metadata_prefix_counts"]["review-evidence:"] == 1
    assert result["tables"]["users"] == 1 and "NEVER_EXPORT" not in json.dumps(result)


def test_crash_during_delete_rolls_back_and_retry_reuses_archives(session, monkeypatch):
    fixture(session); store = InMemoryObjectStore(); lc.archive_early(session, store, now=NOW)
    original = deepcopy(store._objects)
    flush = session.flush
    def crash(*args, **kwargs):
        if session.deleted: raise RuntimeError("synthetic crash before delete flush")
        return flush(*args, **kwargs)
    monkeypatch.setattr(session, "flush", crash)
    with pytest.raises(RuntimeError, match="synthetic crash"):
        lc.automatic(session, store, enabled=True, now=NOW)
    monkeypatch.setattr(session, "flush", flush)
    assert session.query(ActiveMatch).count() == session.query(MatchResult).count() == 1
    assert session.query(JobRun).count() == 0 and store._objects == original
    assert lc.automatic(session, store, enabled=True, now=NOW)["deleted"] == 2
    assert store._objects == original


def test_two_scheduler_connections_cannot_enter_same_mocked_pg_lock(monkeypatch):
    """Threads exercise production lock acquisition/release with an isolated fake PG transport."""
    from concurrent.futures import ThreadPoolExecutor
    from contextlib import contextmanager
    from threading import Event, Lock
    from types import SimpleNamespace
    from app.core.config import Settings
    from app.jobs import retention
    lock, entered, release = Lock(), Event(), Event()
    class Connection:
        def execute(self, statement, args):
            assert args["key"] == lc.LOCK_ID
            if "pg_try_advisory_lock" in str(statement):
                return SimpleNamespace(scalar_one=lambda: lock.acquire(blocking=False))
            assert "pg_advisory_unlock" in str(statement)
            lock.release()
        def commit(self): pass
        def rollback(self): pass
    class Engine:
        @contextmanager
        def connect(self): yield Connection()
        def dispose(self): pass
    monkeypatch.setattr(retention, "create_engine", lambda *a, **kw: Engine())
    cfg = Settings(_env_file=None, database_url="postgresql://synthetic@localhost/synthetic")
    def first():
        with retention.storage_session(cfg, readonly=False):
            entered.set()
            assert release.wait(timeout=5)
    with ThreadPoolExecutor(max_workers=2) as pool:
        worker = pool.submit(first)
        assert entered.wait(timeout=5)
        try:
            with pytest.raises(RuntimeError, match="LIFECYCLE_ALREADY_RUNNING"):
                with retention.storage_session(cfg, readonly=False): pytest.fail("overlapping worker entered")
        finally:
            release.set()
        worker.result(timeout=5)
    assert not lock.locked()


def test_unknown_foreign_key_is_a_global_report_blocker(session, monkeypatch):
    fixture(session); store = InMemoryObjectStore(); lc.archive_early(session, store, now=NOW)
    monkeypatch.setattr(lc, "unknown_dependencies", lambda s: ["synthetic_external_table"])
    result = inspect(session, store)
    assert result["eligible"] == 0 and result["unknown_dependency_tables"] == 1
    assert all("UNKNOWN_FOREIGN_KEY_DEPENDENCY" in r["reasons"] for r in result["records"])
    assert "synthetic_external_table" not in json.dumps(result)


def test_production_report_uses_read_only_repeatable_snapshot_without_writes(session, monkeypatch):
    from contextlib import contextmanager
    from types import SimpleNamespace
    from app.jobs import retention, import_preflight
    statements = []
    @contextmanager
    def storage(settings, *, readonly):
        assert readonly is True
        class ReadOnly:
            def __getattr__(self, name): return getattr(session, name)
            def execute(self, statement, *args, **kwargs):
                sql = str(statement); statements.append(sql)
                if sql.startswith("SET TRANSACTION ISOLATION LEVEL"): return None
                if sql == "SHOW transaction_read_only": return SimpleNamespace(scalar_one=lambda: "on")
                if sql == "SELECT pg_database_size(current_database())": return SimpleNamespace(scalar_one=lambda: 1000)
                assert sql.lstrip().startswith("SELECT")
                return session.execute(statement, *args, **kwargs)
        yield ReadOnly()
    monkeypatch.setattr(import_preflight, "run", lambda cfg: {"status": "PASS"})
    monkeypatch.setattr(retention, "storage_session", storage)
    monkeypatch.setattr(import_preflight, "bounded_store", lambda cfg: SimpleNamespace(client=SimpleNamespace(close=lambda: None)))
    monkeypatch.setattr(retention, "target_check", lambda *a: {"status": "VERIFIED"})
    monkeypatch.setattr(retention, "vacuum_assessment", lambda s: {"manual_vacuum": "NOT_RUN"})
    monkeypatch.setattr(validation, "table_sizes", lambda s: [])
    monkeypatch.setattr(session, "commit", lambda: pytest.fail("report committed"))
    monkeypatch.setattr(session, "delete", lambda *a: pytest.fail("report deleted"))
    result = validation.run(settings=object())
    assert statements[0] == "SET TRANSACTION ISOLATION LEVEL REPEATABLE READ"
    assert result["status"] == "NO_ELIGIBLE_RECORDS"
    assert result["protected_metadata"]["same_snapshot_counts_unchanged"]
    assert result["LIVE_DELETE"] == result["SQL_WRITE"] == result["R2_WRITE"] == "NOT_RUN"


def test_preflight_failure_prevents_second_connection_and_preserves_not_run(monkeypatch):
    from app.jobs import retention, import_preflight
    monkeypatch.setattr(import_preflight, "run", lambda cfg: {"status": "BLOCKED"})
    monkeypatch.setattr(retention, "storage_session", lambda *a, **kw: pytest.fail("storage reached"))
    result = validation.run(settings=object())
    assert result["status"] == "BLOCKED" and result["LIVE_DELETE"] == "NOT_RUN"
