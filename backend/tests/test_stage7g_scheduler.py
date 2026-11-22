"""Stage 7G synthetic schedule/SQLite evidence. No live storage or dispatch."""
from copy import deepcopy
from datetime import timedelta
import json
from urllib.parse import parse_qs, urlsplit

import pytest

from app.jobs import actions_retention as actions, actions_watchdog as watchdog
from app.jobs.retention_validation import scheduler_audit
from app.jobs.scheduler_health import alert_payload, operation
from app.models import JobRun, SystemSetting
from test_actions_retention import cfg, factory, result
from test_lifecycle import NOW, session
from test_scheduler_reliability import sources, storage, health


def proof():
    value = storage()
    value["sql_scheduler_audit"]["scheduled_runs"] = {}
    for mode in ("archive", "auto"):
        row = value["sql_scheduler_audit"][mode]
        row.update(schema_version=2, run_attempt=1, event="schedule", storage_status="SKIPPED",
                   execution_status="EXECUTED", archive_records_written=0, archive_failures=0)
        value["sql_scheduler_audit"]["scheduled_runs"][mode] = [deepcopy(row)]
    return value


def test_missing_schedule_is_not_healed_by_manual_workflow_green():
    runs = sources()
    runs[0]["created_at"] = (NOW-timedelta(minutes=46)).isoformat()
    runs += [{**runs[0], "id":99, "event":"workflow_dispatch", "created_at":NOW.isoformat()}]
    report = health(runs=runs, proof=proof())
    assert report["state"] == "ARCHIVE_STALE"
    assert report["checks"]["archive"]["latest_run_id"] == 1
    assert report["checks"]["archive"]["observed_gap_intervals"] == 3
    assert "NO_RECENT_SUCCESSFUL_ARCHIVE_SCHEDULE" in report["errors"]


@pytest.mark.parametrize("mode,age", [("archive", 35), ("auto", 95)])
def test_transient_delay_warns_without_claiming_execution_failure(mode, age):
    runs = sources()
    next(r for r in runs if r["display_title"] == "Retention "+mode)["created_at"] = (NOW-timedelta(minutes=age)).isoformat()
    report = health(runs=runs, proof=proof())
    assert report["status"] == "PASS" and report["state"] == "SCHEDULE_DELAYED"
    assert not any("FAILED" in c for c in report["errors"])
    assert report["checks"][mode]["threshold_minutes"] == (45 if mode == "archive" else 120)


def test_pending_delayed_run_is_not_failed_or_success_and_cannot_reset_stale():
    runs = sources(); runs[0]["created_at"] = (NOW-timedelta(minutes=46)).isoformat()
    runs += [{**runs[0], "id":9, "created_at":(NOW-timedelta(minutes=3)).isoformat(), "status":"queued", "conclusion":None}]
    report = health(runs=runs, proof=proof())
    assert report["state"] == "ARCHIVE_STALE"
    assert "ARCHIVE_RUN_PENDING" in report["warnings"]
    assert report["checks"]["archive"]["last_success_run_id"] == 1
    assert "LATEST_ARCHIVE_FAILED_OR_CANCELLED" not in report["errors"]


def test_excessive_queue_is_separate_from_failed_execution():
    runs = sources()+[{"id":9,"event":"schedule","display_title":"Retention auto", "status":"in_progress",
        "conclusion":None,"created_at":(NOW-timedelta(minutes=21)).isoformat()}]
    # Make the last successful auto older than the in-progress invocation.
    runs[1]["created_at"]=(NOW-timedelta(minutes=30)).isoformat()
    report=health(runs=runs,proof=proof())
    assert "AUTO_RUN_EXCEEDED_BUDGET" in report["errors"]
    assert "LATEST_AUTO_FAILED_OR_CANCELLED" not in report["errors"]


@pytest.mark.parametrize("field,value", [("completed_at",None),("completed_at",(NOW+timedelta(minutes=1)).isoformat()),
                                        ("run_attempt",2),("event","workflow_dispatch")])
def test_stale_or_wrong_audit_cannot_match_success(field,value):
    data=proof();data["sql_scheduler_audit"]["scheduled_runs"]["archive"][0][field]=value
    data["sql_scheduler_audit"]["archive"]=deepcopy(data["sql_scheduler_audit"]["scheduled_runs"]["archive"][0])
    assert health(proof=data)["status"]=="FAILED"


def test_fresh_schedule_old_linked_sql_audit_is_distinct_stale_code():
    data=proof();data["sql_scheduler_audit"]["scheduled_runs"]["archive"][0]["completed_at"]=(NOW-timedelta(minutes=46)).isoformat()
    report=health(proof=data)
    assert "ARCHIVE_SQL_AUDIT_STALE" in report["errors"]
    assert "NO_RECENT_SUCCESSFUL_ARCHIVE_SCHEDULE" not in report["errors"]


def test_r2_failure_cannot_be_verified_even_with_fresh_workflows_and_sql():
    data=proof();data["storage_preflight"]["r2"]={"status":"FAILED"}
    report=health(proof=data)
    assert report["state"]=="STORAGE_RECONCILE_REQUIRED"
    assert report["storage_evidence"]["status"]=="NOT_VERIFIED"
    assert "R2_VERIFICATION_FAILED" in report["errors"]


@pytest.mark.parametrize("status,storage_status,count,outcome", [
    ("SUCCESS","SUCCESS",2,"REPORTED_WRITES"),("SKIPPED","SKIPPED",0,"SKIPPED"),
    ("SKIPPED","DRY_RUN",0,"DRY_RUN"),("FAILED","FAILED",0,"FAILED"),
    ("SUCCESS","UNAVAILABLE",None,"NOT_VERIFIED")])
def test_workflow_green_is_never_new_r2_data_proof(status,storage_status,count,outcome):
    row={"status":status,"storage_status":storage_status,"archive_records_written":count,"execution_status":"EXECUTED"}
    assert operation(row)=={"outcome":outcome,"new_r2_data_verified":False,"archive_records_written":count}


def test_duplicate_rows_and_new_failed_attempt_cannot_borrow_previous_success():
    runs=sources();runs[0]["run_attempt"]=1
    runs += [deepcopy(runs[0]),{**runs[0],"run_attempt":2,"status":"completed","conclusion":"failure"}]
    report=health(runs=runs,proof=proof())
    assert report["state"]=="ARCHIVE_FAILED"
    assert report["checks"]["archive"]["last_success_run_id"] is None
    assert report["checks"]["archive"]["latest_run_attempt"]==2


def test_rerun_needs_matching_attempt_in_sql():
    runs=sources();runs[0]["run_attempt"]=2
    report=health(runs=runs,proof=proof())
    assert not report["checks"]["archive"]["sql_audit_matches_scheduled_success"]


def test_manual_audit_does_not_mask_bounded_schedule_history(session,monkeypatch):
    monkeypatch.setenv("GITHUB_EVENT_NAME","schedule");monkeypatch.setenv("GITHUB_RUN_ID","123");monkeypatch.setenv("GITHUB_RUN_ATTEMPT","1")
    run=lambda **kw:{**result("SKIPPED"),"records_written":0}
    opts={"now":NOW,"clock":lambda:NOW+timedelta(seconds=10),"cfg":cfg(),"session_factory":factory(session),"runner":run}
    first,code=actions.run_once("archive",**opts)
    monkeypatch.setenv("GITHUB_EVENT_NAME","workflow_dispatch");monkeypatch.setenv("GITHUB_RUN_ID","999")
    second,repeat=actions.run_once("archive",**opts)
    assert code==repeat==0 and session.query(JobRun).count()==1
    assert second["execution_status"]=="NOT_EXECUTED" and second["archive_records_written"]==0 and second["storage_status"]=="NOT_RUN"
    audit=scheduler_audit(session)
    assert audit["archive"]["run_id"]=="999"
    scheduled=audit["scheduled_runs"]["archive"]
    assert len(scheduled)==1 and scheduled[0]["run_id"]=="123" and scheduled[0]["event"]=="schedule"
    assert scheduled[0]["completed_at"]==(NOW+timedelta(seconds=10)).isoformat()
    data=proof();data["sql_scheduler_audit"]=audit
    runs=sources();runs[0]["id"]=123
    report=health(runs=runs,proof=data,now=NOW+timedelta(seconds=11))
    assert report["checks"]["archive"]["sql_audit_matches_scheduled_success"]
    assert report["checks"]["archive"]["operation"]["outcome"]=="SKIPPED"


def test_observation_history_is_bounded_and_attempts_distinct(monkeypatch):
    monkeypatch.setenv("GITHUB_EVENT_NAME","schedule")
    value={}
    for i in range(40):
        value=actions.record_observation(value,"archive",{},run_id=str(i),started_at=NOW,completed_at=NOW)
    assert len(value["scheduled_runs"]["archive"])==32
    monkeypatch.setenv("GITHUB_RUN_ATTEMPT","2")
    value=actions.record_observation(value,"archive",{},run_id="39",started_at=NOW,completed_at=NOW)
    assert {r["run_attempt"] for r in value["scheduled_runs"]["archive"] if r["run_id"]=="39"}=={1,2}


def test_scheduled_history_paginates_and_filters_event_and_branch():
    calls=[]
    def get(path):
        query=parse_qs(urlsplit(path).query);calls.append(query)
        return {"workflow_runs":[{"id":len(calls)}]* (100 if len(calls)==1 else 2)}
    rows,truncated=watchdog.scheduled_runs(get,"main")
    assert len(rows)==102 and not truncated and len(calls)==2
    assert all(c["event"]==["schedule"] and c["branch"]==["main"] for c in calls)
    rows,truncated=watchdog.scheduled_runs(lambda p:{"workflow_runs":[{}]*100},"main",max_pages=2)
    assert truncated and len(rows)==200


def test_alert_payload_is_stable_without_secret_fields_and_changes_on_incident():
    report=health(proof=proof());report["token"]="SLACK_SECRET";report["url"]="https://private/SECRET"
    alert=alert_payload(report);again=deepcopy(report);again["observed_at"]="changed"
    assert alert==alert_payload(again) and "SECRET" not in json.dumps(alert)
    again["errors"]=["R2_VERIFICATION_FAILED"]
    assert alert_payload(again)["severity"]=="ERROR" and alert_payload(again)["dedup_key"]!=alert["dedup_key"]


def test_safe_audit_projection_does_not_export_arbitrary_metadata(session):
    row={"schema_version":2,"event":"schedule","run_id":"123","run_attempt":1,"token":"SLACK_SECRET","url":"https://private/SECRET"}
    session.add(SystemSetting(key="scheduler:github-actions-retention",value={"archive":row,"scheduled_runs":{"archive":[row]}}));session.commit()
    assert "SECRET" not in json.dumps(scheduler_audit(session))


def test_archive_artifact_uses_scrubbed_summary(monkeypatch,tmp_path,capsys):
    path=tmp_path/"retention.json";monkeypatch.setenv("RETENTION_REPORT_PATH",str(path));monkeypatch.setenv("R2_SECRET_ACCESS_KEY","PRIVATE_SECRET")
    monkeypatch.delenv("GITHUB_ACTIONS",raising=False)
    actions.emit({"status":"SKIPPED","error_code":"PRIVATE_SECRET"},0)
    assert "PRIVATE_SECRET" not in path.read_text() and "PRIVATE_SECRET" not in capsys.readouterr().out


def test_scheduled_duplicate_is_a_new_invocation_not_new_archive_write(session,monkeypatch):
    monkeypatch.setenv("GITHUB_EVENT_NAME","schedule");monkeypatch.setenv("GITHUB_RUN_ID","123")
    calls=[]
    def runner(**kw):
        calls.append(1);return {**result("SUCCESS"),"records_written":2}
    opts={"now":NOW,"clock":lambda:NOW,"cfg":cfg(),"session_factory":factory(session),"runner":runner}
    first,code=actions.run_once("archive",**opts)
    monkeypatch.setenv("GITHUB_RUN_ID","124")
    second,repeat=actions.run_once("archive",**opts)
    assert code==repeat==0 and len(calls)==1
    assert first["archive_records_written"]==2 and second["archive_records_written"]==0
    audit=scheduler_audit(session)
    assert len(audit["scheduled_runs"]["archive"])==2
    assert operation(audit["archive"])["outcome"]=="IDEMPOTENT_DUPLICATE"
    assert not operation(audit["archive"])["new_r2_data_verified"]


def test_failed_r2_verification_is_audited_without_retry_or_deletion(session,monkeypatch):
    monkeypatch.setenv("GITHUB_EVENT_NAME","schedule");monkeypatch.setenv("GITHUB_RUN_ID","123")
    report,code=actions.run_once("archive",now=NOW,clock=lambda:NOW,cfg=cfg(),session_factory=factory(session),
        runner=lambda **kw:{"status":"FAILED","failure_stage":"R2_VERIFICATION","error_code":"R2_CHECKSUM_MISMATCH",
                            "records_failed":1,"deleted":0,"LIVE_DELETE":"NOT_RUN"})
    assert code==1 and report["sql_audit"]=="RECORDED" and report["LIVE_DELETE"]=="NOT_RUN"
    assert operation(scheduler_audit(session)["archive"])["outcome"]=="FAILED"
    assert session.query(JobRun).one().status=="FAILED"


def test_pr_tests_have_separate_concurrency_and_never_run_maintenance():
    from pathlib import Path
    workflow=(Path(__file__).parents[2]/".github/workflows/retention-scheduler.yml").read_text()
    assert "-pr-{0}" in workflow and "cancel-in-progress: false" in workflow
    assert "github.event_name != 'pull_request' &&" in workflow
    assert "secrets." not in workflow.split("  validate:",1)[1].split("  retention:",1)[0]
