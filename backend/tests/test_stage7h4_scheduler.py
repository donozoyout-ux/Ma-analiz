"""Synthetic scheduler evidence only. Never connects to SQL/R2 or dispatches jobs."""
from copy import deepcopy
from datetime import timedelta
from types import SimpleNamespace

import pytest

from app.jobs.actions_watchdog import assess
from app.jobs.retention_validation import scheduler_audit
from app.jobs.retention_readiness import assess as readiness
from test_lifecycle import NOW
from test_stage7g_scheduler import proof


def evidence():
    storage=proof();runs=[]
    for i,mode in enumerate(("archive","auto"),1):
        runs.append({"id":i,"run_attempt":1,"event":"schedule","display_title":"Retention "+mode,
            "status":"completed","conclusion":"success","created_at":(NOW-timedelta(minutes=6)).isoformat(),
            "run_started_at":(NOW-timedelta(minutes=5)).isoformat(),"updated_at":(NOW-timedelta(minutes=2)).isoformat()})
        row=storage["sql_scheduler_audit"][mode]
        row.update(started_at=(NOW-timedelta(minutes=4)).isoformat(),completed_at=(NOW-timedelta(minutes=3)).isoformat())
        storage["sql_scheduler_audit"]["scheduled_runs"][mode]=[deepcopy(row)]
    return runs,storage


def health(runs,storage):
    report=assess({"state":"active","created_at":(NOW-timedelta(days=2)).isoformat()},runs,{},now=NOW,storage=storage)
    assert report["SAFE_TO_DELETE"]=="NO" and report["LIVE_DELETE"]=="NOT_RUN"
    assert report["SQL_WRITE"]==report["R2_WRITE"]=="NOT_RUN"
    return report


def audit(storage,mode):
    return storage["sql_scheduler_audit"]["scheduled_runs"][mode][0]


def test_valid_identity_and_timeline_are_distinct_from_delete_authorization():
    runs,storage=evidence();report=health(runs,storage)
    assert report["state"]=="HEALTHY" and report["status"]=="PASS"
    for mode in ("archive","auto"):
        assert report["checks"][mode]["sql_audit_time_window_verified"]
        assert not report["checks"][mode]["operation"]["new_r2_data_verified"]
    storage["plan"]["unknown_dependency_tables"]=0
    assert readiness(storage,report,now=NOW)["status"]=="SAFE_TO_DELETE_NO"


@pytest.mark.parametrize("event",["workflow_dispatch","push","pull_request"])
def test_no_natural_schedule_is_never_healed_by_other_green_events(event):
    runs,storage=evidence()
    for row in runs:row["event"]=event
    report=health(runs,storage)
    assert report["status"]=="FAILED" and report["state"]!="HEALTHY"
    assert {"NO_RECENT_SUCCESSFUL_AUTO_SCHEDULE","NO_RECENT_SUCCESSFUL_ARCHIVE_SCHEDULE"} <= set(report["errors"])


def test_no_runs_at_all_is_insufficient_even_when_storage_is_verified():
    _,storage=evidence();report=health([],storage)
    assert report["status"]=="FAILED" and report["storage_evidence"]["status"]=="VERIFIED"
    assert report["checks"]["auto"]["last_success_run_id"] is None


@pytest.mark.parametrize("mode,age,expected",[("archive",30,"HEALTHY"),("archive",31,"SCHEDULE_DELAYED"),
    ("archive",45,"SCHEDULE_DELAYED"),("archive",46,"ARCHIVE_STALE"),
    ("auto",90,"HEALTHY"),("auto",91,"SCHEDULE_DELAYED"),("auto",120,"SCHEDULE_DELAYED"),("auto",121,"AUTO_STALE")])
def test_exact_delay_stale_boundaries_and_late_completion_do_not_move_thresholds(mode,age,expected):
    runs,storage=evidence();row=next(r for r in runs if r["display_title"]=="Retention "+mode)
    row["created_at"]=(NOW-timedelta(minutes=age)).isoformat()
    report=health(runs,storage)
    assert report["state"]==expected
    assert report["checks"][mode]["threshold_minutes"]==(45 if mode=="archive" else 120)
    assert report["checks"][mode]["sql_audit_time_window_verified"]
    if age > (45 if mode=="archive" else 120):assert report["status"]=="FAILED"


def test_recent_queued_auto_does_not_erase_missing_recent_success():
    runs,storage=evidence();runs[1]["created_at"]=(NOW-timedelta(hours=3)).isoformat()
    runs.append({**runs[1],"id":30,"created_at":(NOW-timedelta(minutes=1)).isoformat(),
        "run_started_at":(NOW-timedelta(minutes=1)).isoformat(),"conclusion":None,"status":"queued"})
    report=health(runs,storage)
    assert report["state"]=="AUTO_STALE" and "AUTO_RUN_PENDING" in report["warnings"]
    assert report["checks"]["auto"]["last_success_run_id"]==2


@pytest.mark.parametrize("mode",["archive","auto"])
def test_old_run_failed_rerun_is_not_hidden_by_newer_created_success(mode):
    runs,storage=evidence();recent=next(r for r in runs if r["display_title"]=="Retention "+mode)
    runs.append({**recent,"id":30,"created_at":(NOW-timedelta(hours=4)).isoformat(),"run_attempt":2,
        "run_started_at":(NOW-timedelta(minutes=1)).isoformat(),"updated_at":NOW.isoformat(),"conclusion":"failure"})
    report=health(runs,storage)
    assert report["state"]==mode.upper()+"_FAILED"
    assert report["checks"][mode]["latest_run_id"]==30
    assert report["checks"][mode]["last_success_run_id"]==recent["id"]


@pytest.mark.parametrize("run_attempt,age,over_budget",[(1,14400,True),(2,60,False)])
def test_pending_rerun_age_uses_attempt_start_but_delivery_age_is_not_refreshed(run_attempt,age,over_budget):
    runs,storage=evidence()
    runs.append({**runs[1],"id":30,"created_at":(NOW-timedelta(hours=4)).isoformat(),"run_attempt":run_attempt,
        "run_started_at":(NOW-timedelta(minutes=1)).isoformat(),"status":"in_progress","conclusion":None})
    report=health(runs,storage)
    assert report["checks"]["auto"]["latest_pending_age_seconds"]==age
    assert report["checks"]["auto"]["last_scheduled_created_at"]==runs[1]["created_at"]
    assert ("AUTO_RUN_EXCEEDED_BUDGET" in report["errors"])==over_budget


def test_successful_manual_rerun_of_old_schedule_cannot_refresh_schedule_delivery():
    runs,storage=evidence();runs[1].update(created_at=(NOW-timedelta(hours=3)).isoformat(),run_attempt=2)
    audit(storage,"auto")["run_attempt"]=2
    report=health(runs,storage)
    assert report["state"]=="AUTO_STALE" and report["checks"]["auto"]["sql_audit_time_window_verified"]


@pytest.mark.parametrize("run_attempt",[1,2])
def test_future_pending_attempt_is_insufficient_not_healthy_or_execution_failure(run_attempt):
    runs,storage=evidence()
    runs.append({**runs[1],"id":30,"status":"queued","conclusion":None,
                 "run_attempt":run_attempt,"run_started_at":(NOW+timedelta(minutes=1)).isoformat()})
    report=health(runs,storage)
    assert "AUTO_PENDING_TIME_WINDOW_NOT_VERIFIED" in report["errors"]
    assert report["state"]=="INSUFFICIENT_EVIDENCE"
    assert "LATEST_AUTO_FAILED_OR_CANCELLED" not in report["errors"]


def test_identical_api_and_audit_duplicates_are_idempotent():
    runs,storage=evidence();runs+=deepcopy(runs)
    for mode in ("archive","auto"):storage["sql_scheduler_audit"]["scheduled_runs"][mode]*=2
    assert health(runs,storage)["state"]=="HEALTHY"


@pytest.mark.parametrize("reverse",[False,True])
@pytest.mark.parametrize("mode",["archive","auto"])
def test_conflicting_same_attempt_api_rows_fail_closed_in_both_orders(mode,reverse):
    runs,storage=evidence();row=next(r for r in runs if r["display_title"]=="Retention "+mode)
    rows=[row,{**row,"conclusion":"failure"}]
    runs=[r for r in runs if r is not row]+(rows[::-1] if reverse else rows)
    report=health(runs,storage)
    assert mode.upper()+"_SCHEDULE_OBSERVATION_CONFLICT" in report["errors"]
    assert report["state"]=="INSUFFICIENT_EVIDENCE" and report["status"]=="FAILED"


@pytest.mark.parametrize("mode",["archive","auto"])
@pytest.mark.parametrize("field,value",[("id",None),("run_attempt",None),("run_attempt",True),("run_attempt","1")])
def test_missing_or_ambiguous_github_identity_never_proves_a_schedule(mode,field,value):
    runs,storage=evidence();next(r for r in runs if r["display_title"]=="Retention "+mode)[field]=value
    report=health(runs,storage)
    assert mode.upper()+"_SCHEDULE_IDENTITY_NOT_VERIFIED" in report["errors"] and report["status"]=="FAILED"


@pytest.mark.parametrize("field,value",[("run_id","999"),("run_attempt",2),("run_attempt",True),("schema_version",3),
    ("event","workflow_dispatch"),("started_at",None),("started_at",(NOW-timedelta(hours=1)).isoformat()),
    ("started_at",NOW.isoformat()),("completed_at",(NOW-timedelta(minutes=5)).isoformat()),
    ("completed_at",NOW.isoformat()),("completed_at","2026-11-25T12:00:00")])
def test_audit_v2_identity_and_impossible_timestamps_never_pass(field,value):
    runs,storage=evidence();audit(storage,"auto")[field]=value
    storage["sql_scheduler_audit"]["auto"]=deepcopy(audit(storage,"auto"))
    report=health(runs,storage)
    assert report["status"]=="FAILED" and report["state"]!="HEALTHY"


@pytest.mark.parametrize("field,value",[("created_at",None),("run_started_at",None),("updated_at",None),
    ("run_started_at",(NOW+timedelta(seconds=1)).isoformat()),("updated_at",(NOW-timedelta(minutes=7)).isoformat())])
def test_unverifiable_github_clock_does_not_prove_audit_time_window(field,value):
    runs,storage=evidence();runs[1][field]=value
    report=health(runs,storage)
    assert not report["checks"]["auto"]["sql_audit_time_window_verified"]
    assert report["status"]=="FAILED"


@pytest.mark.parametrize("reverse",[False,True])
def test_duplicate_sql_attempt_with_conflicting_status_is_not_last_row_wins(reverse):
    runs,storage=evidence();row=audit(storage,"auto");rows=[row,{**row,"status":"FAILED"}]
    storage["sql_scheduler_audit"]["scheduled_runs"]["auto"]=rows[::-1] if reverse else rows
    report=health(runs,storage)
    assert "AUTO_AUDIT_OBSERVATION_CONFLICT" in report["errors"]
    assert not report["checks"]["auto"]["sql_audit_matches_scheduled_success"]


def test_stale_sql_audit_is_not_mislabeled_as_missing_github_delivery():
    runs,storage=evidence();audit(storage,"auto")["completed_at"]=(NOW-timedelta(minutes=121)).isoformat()
    report=health(runs,storage)
    assert "AUTO_SQL_AUDIT_STALE" in report["errors"]
    assert "NO_RECENT_SUCCESSFUL_AUTO_SCHEDULE" not in report["errors"]


def test_projection_preserves_stored_start_and_rejects_unknown_version_without_sql():
    _,storage=evidence();value={"auto":{**audit(storage,"auto"),"schema_version":3,"secret":"PRIVATE"}}
    class NoSQL:
        def get(self,model,key):return SimpleNamespace(value=value)
    report=scheduler_audit(NoSQL())
    assert report["auto"]["started_at"]==value["auto"]["started_at"]
    assert report["auto"]["schema_version"] is None and "PRIVATE" not in str(report)
