"""Stage 7D.2 tests: synthetic evidence, SQLite and memory R2; never production retry."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path

import pytest

from app.jobs import archive_retry as retry
from app.jobs.actions_watchdog import assess, stamp
from app.jobs.retention_validation import archive_runtime
from app.models import ActiveMatch, ArchiveJob, JobRun, MatchResult, SystemSetting
from app.storage import lifecycle as lc
from app.storage.r2 import InMemoryObjectStore
from test_lifecycle import NOW, fixture, session


def sources():
    return [{"id": i, "event": "schedule", "display_title": "Retention " + mode,
             "conclusion": "success", "created_at": NOW.isoformat()} for i,mode in enumerate(("archive","auto"),1)]


def storage():
    return {"status": "NO_ELIGIBLE_RECORDS", "plan": {"status": "NO_ELIGIBLE_RECORDS", "scan_complete": True,
        "as_of": NOW.isoformat(), "records": [{"archive_verification": "PASS", "source_identity": "PASS"}]},
        "storage_preflight": {"status": "PASS"}, "protected_target": {"status": "VERIFIED"},
        "protected_metadata": {"transaction_read_only":"on", "same_snapshot_counts_unchanged":True},
        "SQL_WRITE": "NOT_RUN", "R2_WRITE": "NOT_RUN", "LIVE_DELETE": "NOT_RUN",
        "AUTO_RETENTION_DELETE_ENABLED": False, "OPERATIONAL_DELETE_ENABLED": False,
        "archive_runtime": {"incomplete_archive_ledgers": 0, "running_jobs": [], "running_jobs_truncated": False},
        "capacity": {"status": "HEALTHY"}, "sql_scheduler_audit": {mode:{"status": "SKIPPED", "completed_at": NOW.isoformat(),
            "run_id": str(i)} for i,mode in enumerate(("archive","auto"),1)}}


def health(*, runs=None, proof=None, now=NOW):
    return assess({"state":"active", "created_at": (NOW-timedelta(days=2)).isoformat()},
                  sources() if runs is None else runs, {}, now=now, storage=proof)


def test_healthy_requires_fresh_readback_sql_audit_and_scheduled_evidence():
    result = health(proof=storage())
    assert result["state"] == "HEALTHY" and result["errors"] == []
    assert result["storage_evidence"]["last_real_readback_at"] == NOW.isoformat()
    assert result["checks"]["archive"]["sql_audit_matches_scheduled_success"]


@pytest.mark.parametrize("change", ["missing", "old", "incomplete", "runtime", "source", "r2", "lease", "ledger", "audit", "link", "flags", "critical"])
def test_missing_or_conflicting_storage_can_never_be_healthy(change):
    proof = storage()
    if change == "missing": proof = {"status":"NOT_VERIFIED"}
    if change == "old": proof["plan"]["as_of"] = (NOW-timedelta(minutes=11)).isoformat()
    if change == "incomplete": proof["plan"]["scan_complete"] = False
    if change == "runtime": proof.pop("archive_runtime")
    if change == "source": proof["plan"]["records"][0]["source_identity"] = "NOT_VERIFIED"
    if change == "r2": proof["plan"]["records"][0]["archive_verification"] = "NOT_VERIFIED"
    if change == "lease": proof["archive_runtime"]["running_jobs"] = [{"state":"UNKNOWN_RECONCILE_REQUIRED"}]
    if change == "ledger": proof["archive_runtime"]["incomplete_archive_ledgers"] = 1
    if change == "audit": proof["sql_scheduler_audit"]["archive"]["completed_at"] = (NOW-timedelta(hours=2)).isoformat()
    if change == "link": proof["sql_scheduler_audit"]["archive"]["run_id"] = "manual-run"
    if change == "flags": proof["AUTO_RETENTION_DELETE_ENABLED"] = True
    if change == "critical": proof["capacity"]["status"] = "CRITICAL"
    result = health(proof=proof)
    assert result["state"] != "HEALTHY" and result["status"] == "FAILED"
    assert result["SQL_ARCHIVE_WRITE"] == result["LIVE_DELETE"] == "NOT_RUN"


def test_workflow_only_is_explicitly_insufficient_not_false_healthy():
    assert health()["state"] == "INSUFFICIENT_EVIDENCE"


@pytest.mark.parametrize("mode,minutes,expected", [("archive",35,"SCHEDULE_DELAYED"), ("archive",46,"ARCHIVE_STALE"),
    ("auto",95,"SCHEDULE_DELAYED"), ("auto",121,"AUTO_STALE")])
def test_delayed_and_stale_are_distinct(mode, minutes, expected):
    runs = sources()
    next(r for r in runs if r["display_title"] == "Retention " + mode)["created_at"] = (NOW-timedelta(minutes=minutes)).isoformat()
    result = health(runs=runs, proof=storage())
    assert result["state"] == expected


@pytest.mark.parametrize("mode", ["archive", "auto"])
@pytest.mark.parametrize("conclusion", ["failure", "cancelled", "timed_out"])
def test_failed_or_cancelled_schedule_is_not_hidden_by_manual_success(mode, conclusion):
    runs = sources() + [{"id":7,"event":"schedule","display_title":"Retention "+mode,
        "created_at":(NOW+timedelta(seconds=1)).isoformat(),"conclusion":conclusion},
        {"id":8,"event":"workflow_dispatch","display_title":"Retention "+mode,
         "created_at":(NOW+timedelta(seconds=2)).isoformat(),"conclusion":"success"}]
    assert health(runs=runs, proof=storage())["state"] == mode.upper()+"_FAILED"


def test_naive_api_timestamp_cannot_be_a_success_age():
    assert stamp("2026-11-21T00:00:00") is None


def seed(session):
    match, result = fixture(session)
    session.add(SystemSetting(key=lc.source_index_key(match), value={"provider": match.provider,
        "provider_match_id": match.provider_match_id, "match_id": str(match.id), "season":match.season,"league_id":match.league_id}))
    session.commit()
    return match, result


def plan(session, store, match):
    return retry.retry_plan(session, store, match_id=match.id, now=NOW)[0]


def execute(session, store, match):
    current = plan(session, store, match)
    return retry.retry_in_session(session, store, match_id=match.id, approval=current["approval_digest"], reason="Synthetic manual retry", now=NOW)


def test_normal_retry_repeat_and_immutable_object_no_operational_delete(session):
    match,_ = seed(session); store=InMemoryObjectStore()
    assert execute(session,store,match)["status"] == "SUCCESS"
    objects=deepcopy(store._objects)
    assert execute(session,store,match)["status"] == "SKIPPED"
    audit_count=session.query(JobRun).count()
    assert execute(session,store,match)["reason"] == "IDEMPOTENT_DUPLICATE"
    assert session.query(JobRun).count()==audit_count and store._objects==objects
    assert session.query(ActiveMatch).count()==session.query(MatchResult).count()==1
    assert len(objects)==2


@pytest.mark.parametrize("fault", ["upload_ack", "upload", "sql_ack", "timeout"])
def test_retry_crash_and_acknowledgement_loss_are_unknown_then_recover(session,monkeypatch,fault):
    match,_=seed(session);store=InMemoryObjectStore()
    put,commit=store.put_object_if_absent,session.commit
    def broken_put(*a,**kw):
        if fault=="upload_ack": put(*a,**kw)
        if fault=="timeout": raise KeyboardInterrupt()
        raise TimeoutError("synthetic SECRET upload detail")
    def broken_commit():
        if session.query(ArchiveJob).count():
            commit();raise TimeoutError("synthetic SECRET SQL acknowledgement")
        commit()
    if fault in {"upload_ack","upload","timeout"}:monkeypatch.setattr(store,"put_object_if_absent",broken_put)
    if fault=="sql_ack":monkeypatch.setattr(session,"commit",broken_commit)
    result=execute(session,store,match)
    assert result["status"]=="UNKNOWN_RECONCILE_REQUIRED" and "SECRET" not in json.dumps(result)
    assert session.query(ActiveMatch).count()==session.query(MatchResult).count()==1
    monkeypatch.setattr(store,"put_object_if_absent",put);monkeypatch.setattr(session,"commit",commit)
    # A different current plan is reviewed after uncertainty; no automatic loop.
    resumed=execute(session,store,match)
    assert resumed["status"] in {"SUCCESS","SKIPPED"}
    assert len(store._objects)==2


@pytest.mark.parametrize("fault", ["corrupt", "ledger", "identity", "plan", "journal"])
def test_reconcile_mismatch_blocks_before_claiming_a_new_retry(session,fault):
    match,result=seed(session);store=InMemoryObjectStore();execute(session,store,match)
    approval=plan(session,store,match)["approval_digest"]
    if fault=="corrupt":
        key=next(iter(store._objects));body,mime,meta=store._objects[key];store._objects[key]=(b"corrupt",mime,meta)
    if fault=="ledger":session.query(ArchiveJob).first().row_count=999;session.commit()
    if fault=="identity":
        session.get(SystemSetting,lc.source_index_key(match)).value={"match_id":"wrong"};session.commit()
    if fault=="plan":result.home_score=2;session.commit()
    if fault=="journal":
        session.add(SystemSetting(key="archive-retry:"+approval,value={"match_id":"wrong"}));session.commit()
    objects=deepcopy(store._objects);count=session.query(JobRun).count()
    with pytest.raises(retry.RetryBlocked):
        retry.retry_in_session(session,store,match_id=match.id,approval=approval,reason="Synthetic",now=NOW)
    assert store._objects==objects and session.query(JobRun).count()==count


def test_missing_verified_object_needs_two_explicit_one_attempt_commands(session):
    match,_=seed(session);store=InMemoryObjectStore();execute(session,store,match)
    store._objects.pop(next(iter(store._objects)))  # isolated missing object fault only
    assert execute(session,store,match)["status"]=="UNKNOWN_RECONCILE_REQUIRED"
    assert execute(session,store,match)["status"]=="SUCCESS"
    assert len(store._objects)==2


def test_live_job_blocks_retry_but_timed_out_slot_is_not_assumed_rolled_back(session):
    match,_=seed(session);store=InMemoryObjectStore()
    row=JobRun(idempotency_key="archive_data:synthetic-slot",job_type="archive_data",status="RUNNING",started_at=NOW)
    session.add(row);session.commit()
    assert "RETENTION_JOB_IN_PROGRESS" in plan(session,store,match)["blockers"]
    row.started_at=NOW-timedelta(hours=2);session.commit()
    assert plan(session,store,match)["stale_jobs_not_assumed_rolled_back"]==1
    assert execute(session,store,match)["status"]=="SUCCESS"
    assert session.get(JobRun,row.id).status=="RUNNING"  # Never relabel another entire scheduled slot successful.


def test_retry_capability_cannot_delete_or_overwrite_any_object_or_sql(session):
    match,_=seed(session);store=InMemoryObjectStore()
    _,records=retry.retry_plan(session,store,match_id=match.id,now=NOW)
    limited=retry.AppendOnlyRetryStore(store,records)
    with pytest.raises(retry.RetryBlocked):limited.put_object("existing",b"bad")
    with pytest.raises(retry.RetryBlocked):limited.delete_object("existing")
    with pytest.raises(retry.RetryBlocked):limited.put_object_if_absent("outside",b"bad")
    from sqlalchemy import text
    with retry.no_delete_sql(session):
        with pytest.raises(retry.RetryBlocked):session.execute(text("DELETE FROM active_matches"))
        with pytest.raises(retry.RetryBlocked):session.execute(text("WITH x AS (DELETE FROM active_matches RETURNING id) SELECT * FROM x"))
        with pytest.raises(retry.RetryBlocked):session.execute(text("UPDATE active_matches SET status = 'CANCELLED'"))
    session.rollback()
    assert session.query(ActiveMatch).count()==1 and not store._objects


def test_runtime_inventory_reports_partial_and_stale_slots_without_sensitive_values(session):
    session.add(JobRun(idempotency_key="SECRET",job_type="archive_data",status="RUNNING",started_at=NOW-timedelta(hours=2),error_message="SECRET"))
    session.commit()
    report=archive_runtime(session,now=NOW,stale_seconds=3600)
    assert report["running_jobs"][0]["state"]=="UNKNOWN_RECONCILE_REQUIRED"
    assert "SECRET" not in json.dumps(report)


def test_watchdog_workflow_is_read_only_reuses_five_secrets_never_retries():
    root=Path(__file__).parents[2]
    workflow=(root/".github/workflows/retention-watchdog.yml").read_text()
    assert "WATCHDOG_STORAGE_CHECK: 'true'" in workflow
    for name in ("DATABASE_URL","R2_ENDPOINT","R2_ACCESS_KEY_ID","R2_SECRET_ACCESS_KEY","R2_BUCKET"):
        assert "${{ secrets."+name+" }}" in workflow
    assert "AUTO_RETENTION_DELETE_ENABLED: 'false'" in workflow and "OPERATIONAL_DELETE_ENABLED: 'false'" in workflow
    assert "archive_retry" not in workflow and "--execute" not in workflow


def test_reconcile_annotation_has_no_raw_records(monkeypatch,capsys):
    from app.jobs.actions_retention import emit
    monkeypatch.setenv("GITHUB_ACTIONS","true");monkeypatch.delenv("GITHUB_STEP_SUMMARY",raising=False)
    emit({"archive_failures":1,"LIVE_DELETE":"NOT_RUN"},1)
    assert "::error::STORAGE_RECONCILE_REQUIRED" in capsys.readouterr().out


@pytest.mark.parametrize("state", ["NOT_VERIFIED", "CRITICAL"])
def test_manual_execute_never_opens_write_connection_when_preflight_or_capacity_blocks(monkeypatch,state):
    from uuid import uuid4
    proof={"status":"BLOCKED"} if state=="NOT_VERIFIED" else {"status":"NO_ELIGIBLE_RECORDS","capacity":{"status":"CRITICAL"}}
    monkeypatch.setattr(retry.retention_validation,"run",lambda cfg:proof)
    monkeypatch.setattr(retry.retention,"storage_session",lambda *a,**kw:pytest.fail("writable session reached"))
    result=retry.run(match_id=uuid4(),execute=True,approval="a"*64,reason="Synthetic",settings=object())
    assert result["SQL_ARCHIVE_WRITE"]==result["LIVE_DELETE"]=="NOT_RUN"


def test_watchdog_api_failure_produces_safe_artifact_and_annotation(monkeypatch,capsys,tmp_path):
    from app.jobs import actions_watchdog as watchdog
    monkeypatch.setenv("GITHUB_REPOSITORY","synthetic/repo")
    monkeypatch.setenv("GH_TOKEN","SECRET_TOKEN")
    path=tmp_path/"report.json";monkeypatch.setenv("WATCHDOG_REPORT_PATH",str(path))
    def fail(*a):raise RuntimeError("SECRET_TOKEN postgresql://private/SECRET")
    monkeypatch.setattr(watchdog,"github_get",fail)
    assert watchdog.main()==1
    report=json.loads(path.read_text())
    output=capsys.readouterr().out
    assert report["state"]=="INSUFFICIENT_EVIDENCE" and "::error::WATCHDOG_OBSERVATION_NOT_VERIFIED" in output
    assert "SECRET" not in output and "private" not in output
    monkeypatch.setenv("WATCHDOG_REPORT_PATH",str(tmp_path))
    assert watchdog.unavailable("SAFE_FIXED_CODE")==1
    assert "::error::WATCHDOG_REPORT_WRITE_FAILED" in capsys.readouterr().out


def test_manual_driver_uses_read_only_default_and_existing_global_lock_for_explicit_execute(session,monkeypatch):
    from contextlib import contextmanager
    from types import SimpleNamespace
    match,_=seed(session);store=InMemoryObjectStore();store.client=SimpleNamespace(close=lambda:None)
    calls=[]
    @contextmanager
    def context(cfg,*,readonly):
        calls.append(readonly)
        yield session
    monkeypatch.setattr(retry.retention_validation,"run",lambda cfg:{"status":"NO_ELIGIBLE_RECORDS","capacity":{"status":"HEALTHY"}})
    monkeypatch.setattr(retry.retention,"storage_session",context)
    monkeypatch.setattr(retry.import_preflight,"bounded_store",lambda cfg:store)
    monkeypatch.setattr(retry,"datetime",SimpleNamespace(now=lambda tz:NOW))
    cfg=SimpleNamespace(job_run_stale_after_seconds=3600)
    result=retry.run(match_id=match.id,settings=cfg)
    assert calls==[True] and not store._objects
    result=retry.run(match_id=match.id,execute=True,approval=result["approval_digest"],reason="Synthetic",settings=cfg)
    assert calls==[True,False] and result["status"]=="SUCCESS" and result["LIVE_DELETE"]=="NOT_RUN"
