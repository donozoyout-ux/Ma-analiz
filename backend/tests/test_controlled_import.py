"""Synthetic review/import integration. SQLite + memory objects, never live evidence."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import subprocess
import sys

import pytest
from sqlalchemy import create_engine
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import Session

from app.analysis.import_review import plan_report
from app.jobs.controlled_import import exercise_offline_import, recover_offline, main
from app.jobs.match_collector import LEAGUES, POLICY_PATH
from app.models import ActiveMatch, ArchiveJob, MatchResult, SystemSetting, User
from app.db.base import Base
from app.providers.multileague import digest
from app.storage.import_review import OFFLINE_LOCK, ReviewBlocked, ReviewRepository
from app.storage.lifecycle import source_index_key
from app.storage.r2 import InMemoryObjectStore, parquet_rows

NOW = datetime(2026, 11, 21, 10, tzinfo=timezone.utc)
POLICIES = json.loads(POLICY_PATH.read_text(encoding="utf-8"))


def synthetic_report(now=NOW):
    stamp = (now - timedelta(minutes=1)).isoformat()
    primary = {"provider": "openligadb", "source_match_id": "900001", "league": "de.bl1", "season": "2026-2027",
        "home_source_id": 90011, "away_source_id": 90012, "home_team": "Synthetic Home", "away_team": "Synthetic Away",
        "start_time": (NOW + timedelta(hours=2)).isoformat(), "source_league_id": 4937, "identity_scope": "bl1:2026",
        "source_status": "NOT_FINISHED", "reported_score": None, "licence": "ODbL-1.0",
        "attribution": "Data: OpenLigaDB (https://www.openligadb.de), ODbL 1.0", "observed_at": stamp,
        "response_received_at": stamp, "checked_at": stamp, "raw_response_sha256": "a" * 64,
        "evidence_kind": "LIVE_HTTP_200", "validation_status": "REVIEW_REQUIRED",
        "source_url": "https://api.openligadb.de/getmatchdata/bl1/2026/10"}
    peer = {**primary, "provider": "openfootball", "source_match_id": None, "home_source_id": None,
        "away_source_id": None, "source_league_id": None, "identity_scope": "de.1", "record_fingerprint": "c" * 64,
        "source_status": "FIXTURE_ONLY", "licence": "CC0-1.0", "raw_response_sha256": "b" * 64,
        "attribution": "OpenFootball football.json (CC0-1.0)",
        "source_url": "https://raw.githubusercontent.com/openfootball/football.json/master/2026-27/de.1.json"}
    report = {"stage": "7B", "evidence_kind": "INJECTED_TEST_TRANSPORT", "observed_at": stamp, "date_utc": NOW.date().isoformat(),
        "config_sha256": digest({"leagues": LEAGUES, "policies": POLICIES, "schema_version": 1}),
        "leagues": {"de.bl1": {"observations": [primary, peer]}}, "policies": [], "requests": [
            {"resource": "matches:bl1:10", "http_status": 200, "sha256": "a" * 64, "observed_at": stamp},
            {"resource": "football:de.bl1", "http_status": 200, "sha256": "b" * 64, "observed_at": stamp}]}
    for key, value in POLICIES["documents"].items():
        report["policies"].append({"resource": key, "status": "REVIEWED_HASH_MATCH",
            "reviewed_content_sha256": value["sha256"], "raw_response_sha256": "d" * 64})
        report["requests"].append({"resource": key, "http_status": 200, "sha256": "d" * 64, "observed_at": stamp})
    return report


def candidate(report=None, now=NOW):
    plan = plan_report(synthetic_report(now) if report is None else report, now=now)
    return next(c for c in plan["candidates"] if c["evidence"]["identity"]["provider"] == "openligadb")


@compiles(JSONB, "sqlite")
def _jsonb(element, compiler, **kw): return "JSON"


@pytest.fixture
def session():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine, expire_on_commit=False) as sql:
        yield sql
    engine.dispose()


def approve(session, item=None, now=NOW):
    item = item or candidate(now=now)
    repo = ReviewRepository(session)
    version = repo.record(item, now=now)
    decision = repo.decide(version, state="APPROVED_FIXTURE", reviewer="offline-reviewer", reason="Synthetic exact UTC and identity evidence", now=now)
    session.commit()
    return item, decision


def exercise(session, store, item, review_id, report=None, now=NOW):
    return exercise_offline_import(session, store, revalidate=lambda: report or synthetic_report(now),
                                  source_key=item["source_key"], review_id=review_id, now=now)


def test_dry_plan_is_not_write_authority_even_when_structurally_eligible():
    item = candidate()
    assert item["technical_eligibility"] and not item["production_ready"] and not item["blockers"]
    assert item["verified_final_score"] is None and "DURABLE_OPERATOR_REVIEW" in item["required_authority"]
    plan = plan_report(synthetic_report(), now=NOW)
    assert plan["SQL_WRITE"] == plan["R2_WRITE"] == plan["LIVE_DELETE"] == "NOT_RUN"


@pytest.mark.parametrize("change", ["stale", "cached", "future", "hash", "permission", "expired", "utc", "teams", "names", "config", "day"])
def test_missing_checks_cannot_be_approved(session, change):
    report = synthetic_report()
    row, peer = report["leagues"]["de.bl1"]["observations"]
    now = NOW
    if change == "stale": now += timedelta(minutes=11)
    if change == "cached": row["evidence_kind"] = "CACHED_CONDITIONAL_REVALIDATION"
    if change == "future": row["response_received_at"] = (NOW + timedelta(minutes=1)).isoformat()
    if change == "hash": row["raw_response_sha256"] = "f" * 64
    if change == "permission": report["policies"][0]["reviewed_content_sha256"] = "f" * 64
    if change == "expired": now = datetime(2026, 12, 22, tzinfo=timezone.utc)
    if change == "utc": peer["start_time"] = "2026-11-21T12:00:00"
    if change == "teams": row["home_source_id"] = None
    if change == "names": peer["home_team"] += " FC"
    if change == "config": report["config_sha256"] = "f" * 64
    if change == "day": report["date_utc"] = "2026-11-22"
    item = candidate(report, now=now)
    assert item["blockers"] and not item["technical_eligibility"]
    repo = ReviewRepository(session); version = repo.record(item, now=now)
    with pytest.raises(ReviewBlocked, match="TECHNICAL_BLOCKERS"):
        repo.decide(version, state="APPROVED_FIXTURE", reviewer="operator", reason="Cannot override", now=now)
    assert session.query(ActiveMatch).count() == 0


@pytest.mark.parametrize("field,value", [("source_url", "https://user:password@secret.invalid/a"),
    ("home_team", "postgresql://user:password@secret.invalid"), ("source_updated_at_raw", "token=secret")])
def test_untrusted_sensitive_values_never_escape_rejection(field, value):
    report = synthetic_report(); report["leagues"]["de.bl1"]["observations"][0][field] = value
    plan = plan_report(report, now=NOW)
    assert plan["rejected"][0]["state"] == "REJECTED"
    assert value not in json.dumps(plan)


def test_conflicting_utc_and_duplicate_native_id_are_quarantined():
    report = synthetic_report()
    report["leagues"]["de.bl1"]["observations"][1]["start_time"] = (NOW + timedelta(hours=3)).isoformat()
    assert candidate(report)["state"] == "CONFLICT"
    report = synthetic_report()
    duplicate = deepcopy(report["leagues"]["de.bl1"]["observations"][0]); duplicate["away_source_id"] = 999
    report["leagues"]["de.bl1"]["observations"].append(duplicate)
    assert all(c["state"] == "CONFLICT" for c in plan_report(report, now=NOW)["candidates"] if c["evidence"]["identity"]["provider"] == "openligadb")
    report = synthetic_report(); report["leagues"]["de.bl1"]["observations"] *= 2
    assert len(plan_report(report, now=NOW)["candidates"]) == 2


def test_final_declaration_is_not_a_final_import(session):
    report = synthetic_report()
    row = report["leagues"]["de.bl1"]["observations"][0]
    row.update(source_status="FINISHED", reported_score=[1, 1], source_final_confirmed=True)
    item = candidate(report)
    assert item["kind"] == "FINAL_OBSERVATION" and item["verified_final_score"] is None
    assert "INDEPENDENT_FINAL_ADAPTER_NOT_APPROVED" in item["blockers"]
    assert session.query(MatchResult).count() == 0


def test_durable_append_only_review_survives_reopen(tmp_path):
    engine = create_engine("sqlite:///" + str(tmp_path / "review.db")); Base.metadata.create_all(engine)
    with Session(engine, expire_on_commit=False) as sql:
        item, review = approve(sql)
    with Session(engine, expire_on_commit=False) as sql:
        repo = ReviewRepository(sql)
        assert repo.approved(item, review, now=NOW)["state"] == "APPROVED_FIXTURE"
        rejected = repo.decide(item["evidence_sha256"], state="REJECTED", reviewer="another-operator", reason="Withdraw approval",
                              now=NOW, previous=review)
        sql.commit()
        assert rejected != review and sql.get(SystemSetting, "review-audit:" + review)
        with pytest.raises(ReviewBlocked, match="CURRENT_DURABLE"):
            repo.approved(item, review, now=NOW)
    engine.dispose()


def test_source_identity_change_is_sticky_and_reviews_require_head(session):
    item, review = approve(session)
    report = synthetic_report(); report["leagues"]["de.bl1"]["observations"][0]["away_source_id"] = 999
    changed = candidate(report); repo = ReviewRepository(session); version = repo.record(changed, now=NOW)
    with pytest.raises(ReviewBlocked, match="REVIEW_HEAD_CHANGED"):
        repo.decide(version, state="REJECTED", reviewer="operator", reason="Conflict", now=NOW)
    with pytest.raises(ReviewBlocked, match="TECHNICAL_BLOCKERS"):
        repo.decide(version, state="APPROVED_FIXTURE", reviewer="operator", reason="Conflict", now=NOW, previous=review)
    assert session.get(SystemSetting, "review-source:" + item["source_key"]).value["conflict"]


def test_approved_offline_fixture_twice_is_one_row_and_one_immutable_part(session):
    item, review = approve(session)
    user = User(); session.add(user); session.add(SystemSetting(key="unrelated", value={"keep": True})); session.commit()
    store = InMemoryObjectStore()
    result = exercise(session, store, item, review)
    assert result["status"] == "OFFLINE_VERIFIED" and result["created"]
    objects = dict(store._objects)
    replay = exercise(session, store, item, review, now=NOW + timedelta(minutes=1))
    assert replay["status"] == "OFFLINE_VERIFIED" and not replay["created"]
    assert store._objects == objects and len(objects) == 1
    assert session.query(ActiveMatch).count() == 1 and session.query(MatchResult).count() == 0
    match = session.query(ActiveMatch).one()
    assert match.status == "SCHEDULED" and (match.expires_at - match.start_time).total_seconds() == 48 * 3600
    assert session.get(SystemSetting, source_index_key(match)).value["match_id"] == str(match.id)
    assert session.query(User).count() == 1 and session.get(SystemSetting, "unrelated").value == {"keep": True}
    obj = store.list_objects()[0]; row = parquet_rows(store.get_object(obj.key))[0]
    assert "/season=2026-2027/league=de.bl1/date=2026-11-21/part=lifecycle-" in obj.key
    assert row["licence"] == "ODbL-1.0" and row["sql_snapshot_json"]
    assert session.query(ArchiveJob).one().status == "VERIFIED"


def test_report_or_unregistered_review_does_not_authorize_sql(session):
    item = candidate()
    with pytest.raises(ReviewBlocked, match="CURRENT_DURABLE"):
        exercise(session, InMemoryObjectStore(), item, "a" * 64)
    assert session.query(ActiveMatch).count() == 0


def test_changed_source_bytes_invalidates_previous_review(session):
    item, review = approve(session)
    report = synthetic_report(); report["leagues"]["de.bl1"]["observations"][0]["raw_response_sha256"] = "f" * 64
    report["requests"][0]["sha256"] = "f" * 64
    with pytest.raises(ReviewBlocked, match="CURRENT_DURABLE"):
        exercise(session, InMemoryObjectStore(), item, review, report)
    assert session.query(ActiveMatch).count() == 0


@pytest.mark.parametrize("when", ["before_upload", "upload_ack_lost"])
def test_sql_success_r2_failure_recovers_without_source_or_duplicate(session, when):
    item, review = approve(session)
    class FailingStore(InMemoryObjectStore):
        failed = False
        def put_object_if_absent(self, *args, **kwargs):
            if not self.failed:
                self.failed = True
                if when == "upload_ack_lost": super().put_object_if_absent(*args, **kwargs)
                raise TimeoutError("sensitive_endpoint_must_not_escape")
            return super().put_object_if_absent(*args, **kwargs)
    store = FailingStore()
    failed = exercise(session, store, item, review)
    assert failed["status"] == "OFFLINE_FAILED_RETRYABLE" and "sensitive_endpoint" not in json.dumps(failed)
    assert session.query(ActiveMatch).count() == 1
    result = recover_offline(session, store, source_key=item["source_key"], now=NOW + timedelta(days=1))
    assert result["status"] == "OFFLINE_VERIFIED" and session.query(ActiveMatch).count() == 1


@pytest.mark.parametrize("acknowledged", [False, True])
def test_r2_success_sql_final_ack_failure_reconciles(session, monkeypatch, acknowledged):
    item, review = approve(session); store = InMemoryObjectStore(); commit = session.commit
    failed = False
    def fail_final_once():
        nonlocal failed
        journal = session.get(SystemSetting, "controlled-import:" + item["source_key"])
        if journal and journal.value["phase"] == "ARCHIVE_VERIFIED" and not failed:
            failed = True
            if acknowledged: commit()
            raise TimeoutError("ack lost")
        commit()
    monkeypatch.setattr(session, "commit", fail_final_once)
    assert exercise(session, store, item, review)["status"] == "OFFLINE_FAILED_RETRYABLE"
    monkeypatch.setattr(session, "commit", commit)
    original = dict(store._objects)
    assert recover_offline(session, store, source_key=item["source_key"], now=NOW)["status"] == "OFFLINE_VERIFIED"
    assert store._objects == original and session.query(ActiveMatch).count() == 1


def test_corrupt_archive_is_never_overwritten(session):
    item, review = approve(session); store = InMemoryObjectStore()
    exercise(session, store, item, review)
    key = store.list_objects()[0].key
    body, metadata, content_type = store._objects[key]
    store._objects[key] = (b"broken", metadata, content_type)
    before = dict(store._objects)
    result = recover_offline(session, store, source_key=item["source_key"], now=NOW)
    assert result["status"] == "OFFLINE_FAILED_RETRYABLE"
    assert store._objects == before and session.query(ActiveMatch).count() == 1


def test_missing_sql_cannot_be_resurrected_from_archive_or_report(session, monkeypatch):
    item, review = approve(session); store = InMemoryObjectStore()
    exercise(session, store, item, review)
    original_get = session.get
    monkeypatch.setattr(session, "get", lambda model, *a, **kw: None if model is ActiveMatch else original_get(model, *a, **kw))
    with pytest.raises(ReviewBlocked, match="SQL_OR_IDENTITY_CHANGED"):
        recover_offline(session, store, source_key=item["source_key"], now=NOW)


def test_existing_native_or_cross_source_fixture_is_not_silently_merged(session):
    item, review = approve(session)
    row = item["evidence"]["observation"]
    old = ActiveMatch(provider="goaloo", provider_match_id="offline-other", league_id="8", season="2026-2027",
        home_team=row["home_team"], away_team=row["away_team"], start_time=NOW + timedelta(hours=2),
        expires_at=NOW + timedelta(hours=50), status="SCHEDULED")
    session.add(old); session.commit(); uuid = old.id
    with pytest.raises(ReviewBlocked, match="EXISTING_FIXTURE"):
        exercise(session, InMemoryObjectStore(), item, review)
    assert session.query(ActiveMatch).count() == 1 and old.id == uuid


def test_live_adapters_and_busy_offline_lock_are_rejected_before_callback(session):
    item = candidate()
    class LiveStore: pass
    def forbidden(): pytest.fail("must not run a fetch")
    with pytest.raises(ReviewBlocked, match="PRODUCTION_PERSIST_DISABLED"):
        exercise_offline_import(session, LiveStore(), revalidate=forbidden, source_key=item["source_key"], review_id="x", now=NOW)
    OFFLINE_LOCK.acquire()
    try:
        with pytest.raises(ReviewBlocked, match="ALREADY_RUNNING"):
            exercise_offline_import(session, InMemoryObjectStore(), revalidate=forbidden, source_key=item["source_key"], review_id="x", now=NOW)
    finally:
        OFFLINE_LOCK.release()


def test_cli_persist_is_blocked_before_report_read(tmp_path, capsys, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql://secret:password@never-contact")
    assert main(["--persist", "--report", str(tmp_path / "not-present.json")]) == 1
    text = capsys.readouterr().out
    assert "STAGE7C_PRODUCTION_PERSIST_DISABLED" in text and "secret:password" not in text


def test_dry_run_imports_no_storage_or_settings(tmp_path):
    path = tmp_path / "report.json"; path.write_text(json.dumps(synthetic_report()))
    script = '''
import sys
class Guard:
 def find_spec(self, name, *args):
  if name.startswith(("app.db", "app.storage", "app.core.config", "sqlalchemy", "boto3", "pyarrow")):
   raise RuntimeError("FORBIDDEN_STORAGE_IMPORT")
sys.meta_path.insert(0, Guard())
from app.jobs.controlled_import import main
raise SystemExit(main(["--report", sys.argv[1]]))
'''
    result = subprocess.run([sys.executable, "-c", script, str(path)], capture_output=True, text=True)
    assert result.returncode == 0 and "DRY_RUN_COMPLETE" in result.stdout


def test_bayern_uuid_alias_journal_and_archives_are_untouched(session):
    from test_verified_import import native_match, persist
    from app.providers.identity import REVIEWED_IMPORT_KEY
    existing = native_match(); session.add(existing); session.commit(); uuid = existing.id
    store = InMemoryObjectStore(); persist(session, store)
    objects = dict(store._objects); prior = deepcopy(session.get(SystemSetting, REVIEWED_IMPORT_KEY).value)
    report = synthetic_report(); report["leagues"]["de.bl1"]["observations"][0]["source_match_id"] = "83237"
    item = candidate(report)
    assert "PRESERVE_EXISTING_VERIFIED_IMPORT_ROUTE" in item["blockers"]
    repo = ReviewRepository(session); version = repo.record(item, now=NOW)
    with pytest.raises(ReviewBlocked, match="TECHNICAL_BLOCKERS"):
        repo.decide(version, state="APPROVED_FIXTURE", reviewer="operator", reason="No override", now=NOW)
    assert session.query(ActiveMatch).one().id == uuid
    assert session.get(SystemSetting, REVIEWED_IMPORT_KEY).value == prior and store._objects == objects


def test_pg_mutations_fail_before_any_query_or_callback():
    from types import SimpleNamespace
    from app.storage.import_review import immutable_setting, prepare_fixture_sql
    pg = SimpleNamespace(get_bind=lambda: SimpleNamespace(dialect=SimpleNamespace(name="postgresql")))
    with pytest.raises(ReviewBlocked, match="PRODUCTION_PERSIST_DISABLED"):
        immutable_setting(pg, "not-written", {})
    with pytest.raises(ReviewBlocked, match="PRODUCTION_PERSIST_DISABLED"):
        prepare_fixture_sql(pg, candidate(), review_id="none", now=NOW)


def test_capacity_limit_holds_metadata_without_pruning(session, monkeypatch):
    import app.storage.import_review as module
    repo = ReviewRepository(session); first = candidate(); repo.record(first, now=NOW); session.commit()
    monkeypatch.setattr(module, "MAX_EVIDENCE", 1)
    report = synthetic_report(); report["requests"][0]["sha256"] = "f" * 64
    report["leagues"]["de.bl1"]["observations"][0]["raw_response_sha256"] = "f" * 64
    with pytest.raises(ReviewBlocked, match="CAPACITY_REACHED"):
        repo.record(candidate(report), now=NOW)
    assert session.get(SystemSetting, "review-evidence:" + first["evidence_sha256"])
    assert session.query(ActiveMatch).count() == 0


def test_ready_boolean_and_blocker_tampering_cannot_bypass_checks(session):
    report = synthetic_report(); report["leagues"]["de.bl1"]["observations"][1]["start_time"] = None
    item = candidate(report); item["blockers"] = []; item["technical_eligibility"] = True
    repo = ReviewRepository(session); version = repo.record(item, now=NOW)
    with pytest.raises(ReviewBlocked, match="TECHNICAL_BLOCKERS"):
        repo.decide(version, state="APPROVED_FIXTURE", reviewer="operator", reason="Cannot forge flags", now=NOW)


def test_stale_approval_and_missing_journal_block_replay(session):
    item, review = approve(session)
    with pytest.raises(ReviewBlocked, match="REVIEW_EXPIRED"):
        ReviewRepository(session).approved(item, review, now=NOW + timedelta(minutes=11))
    with pytest.raises(ReviewBlocked, match="NO_R2_ONLY_RESTORE"):
        recover_offline(session, InMemoryObjectStore(), source_key=item["source_key"], now=NOW)


def test_canonical_bundesliga_identity_uses_existing_fixture_lock_key():
    from app.providers.identity import fixture_key
    item = candidate()["evidence"]["observation"]
    row = {"provider": item["provider"], "provider_match_id": item["source_match_id"], "league_id": "bl1",
           "start_time": item["start_time"], "home_team": item["home_team"], "away_team": item["away_team"]}
    assert fixture_key(row) and fixture_key(row) == fixture_key({**row, "league_id": "de.bl1"})


@pytest.mark.parametrize("acknowledged", [False, True])
def test_initial_sql_commit_is_atomic_or_recoverable(session, monkeypatch, acknowledged):
    item, review = approve(session); store = InMemoryObjectStore(); commit = session.commit
    failed = False
    def fail_once():
        nonlocal failed
        if not failed:
            failed = True
            if acknowledged: commit()
            raise TimeoutError("SQL acknowledgement missing")
        commit()
    monkeypatch.setattr(session, "commit", fail_once)
    result = exercise(session, store, item, review)
    assert result["status"] == "OFFLINE_FAILED_RETRYABLE" and not store.list_objects()
    assert session.query(ActiveMatch).count() == int(acknowledged)
    monkeypatch.setattr(session, "commit", commit)
    recovered = (recover_offline(session, store, source_key=item["source_key"], now=NOW) if acknowledged
                 else exercise(session, store, item, review))
    assert recovered["status"] == "OFFLINE_VERIFIED" and session.query(ActiveMatch).count() == 1
