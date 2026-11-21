"""No production secrets, network or actual operational deletion in these tests."""
from contextlib import contextmanager
from datetime import timedelta
from pathlib import Path
import json

import pytest

from app.core.config import Settings
from app.jobs import actions_retention as actions
from app.jobs.actions_watchdog import assess
from app.models import JobRun, SystemSetting
from test_lifecycle import NOW, session


def cfg(**extra):
    return Settings(_env_file=None,database_url="postgresql://offline:SECRET@localhost/offline",
        r2_endpoint="https://offline.invalid",r2_access_key_id="SECRET",r2_secret_access_key="SECRET",
        r2_bucket="offline-bucket",**extra)


def factory(session):
    @contextmanager
    def context():yield session
    return context


def result(status="DRY_RUN",deleted=0):
    return {"status":status,"LIVE_DELETE":"NOT_RUN","deleted":deleted,"policy_hours":48,
            "datasets":{"matches":{"scanned":1,"eligible":0,"retained":1,"overdue_retained":0}}}


def test_dry_run_forces_both_delete_gates_off_and_records_sql_audit(session,monkeypatch):
    monkeypatch.setenv("GITHUB_RUN_ID","1001");called=[]
    def runner(*,mode,settings):
        called.append(mode)
        assert not settings.auto_retention_delete_enabled and not settings.operational_delete_enabled
        return result()
    report,code=actions.run_once("dry-run",now=NOW,cfg=cfg(auto_retention_delete_enabled=True,operational_delete_enabled=True),
        session_factory=factory(session),runner=runner)
    assert code==0 and report["deleted"]==0 and called==["dry-run"]
    assert session.query(JobRun).one().job_type=="actions_retention_dry_run"
    assert session.get(SystemSetting,"scheduler:github-actions-retention").value["dry-run"]["status"]=="SKIPPED"


def test_archive_slot_is_shared_and_duplicate_success_does_not_invoke_again(session):
    called=[]
    def runner(**kw):called.append(kw);return {**result("SUCCESS"),"records_written":1}
    kwargs={"now":NOW,"cfg":cfg(),"session_factory":factory(session),"runner":runner}
    first,code=actions.run_once("archive",**kwargs)
    second,repeat=actions.run_once("archive",**kwargs)
    assert code==repeat==0 and len(called)==1
    assert second["reason"]=="IDEMPOTENT_DUPLICATE" and second["LIVE_DELETE"]=="NOT_RUN"
    assert session.query(JobRun).count()==1
    assert session.query(JobRun).one().idempotency_key.startswith("archive_data:")


def test_auto_uses_stage5_mode_and_independent_gate_and_reports_partial(session):
    def runner(*,mode,settings):
        assert mode=="auto" and settings.auto_retention_delete_enabled
        return {**result("COMPLETED",2),"archive_repair":{"records_failed":1}}
    report,code=actions.run_once("auto",now=NOW,cfg=cfg(auto_retention_delete_enabled=True),session_factory=factory(session),runner=runner)
    assert code==1 and report["status"]=="PARTIAL" and report["deleted"]==2
    assert session.query(JobRun).one().status=="PARTIAL"


def test_missing_secrets_never_connects_or_logs_values():
    def forbidden():pytest.fail("no session before credentials")
    report,code=actions.run_once("dry-run",cfg=Settings(_env_file=None),session_factory=forbidden)
    assert code==1 and "DATABASE_URL" in report["missing_env"] and report["LIVE_DELETE"]=="NOT_RUN"


def test_sql_audit_failure_is_honest_and_redacted(monkeypatch,capsys):
    monkeypatch.delenv("GITHUB_STEP_SUMMARY",raising=False)
    monkeypatch.delenv("GITHUB_ACTIONS",raising=False)
    @contextmanager
    def failed():raise RuntimeError("DATABASE_URL=SECRET_VALUE");yield
    report,code=actions.run_once("archive",cfg=cfg(),session_factory=failed)
    assert code==1 and report["sql_audit"]=="UNAVAILABLE"
    monkeypatch.setenv("R2_SECRET_ACCESS_KEY","SECRET_VALUE")
    actions.emit({**report,"error_code":"SECRET_VALUE"},code)
    assert "SECRET_VALUE" not in capsys.readouterr().out


def test_summary_never_contains_raw_payload_or_url():
    report=actions.public_summary({**result(),"database_url":"postgres://SECRET","observations":["SECRET"],
        "records":[{"archive_key":"PRIVATE_BUCKET","reasons":["ARCHIVE_READBACK_FAILED"]}],
        "error_code":"SECRET:RAW_EXCEPTION"})
    encoded=json.dumps(report)
    assert all(secret not in encoded for secret in ("SECRET","PRIVATE_BUCKET","postgres://"))
    assert report["retention_reasons"]=={"ARCHIVE_READBACK_FAILED":1}


def test_watchdog_ignores_manual_dry_runs_for_schedule_freshness():
    now=NOW
    workflow={"state":"active","created_at":(now-timedelta(days=2)).isoformat()}
    runs=[{"event":"workflow_dispatch","display_title":"Retention dry-run","conclusion":"success","created_at":now.isoformat()}]
    report=assess(workflow,runs,{"private":False,"pushed_at":now.isoformat()},now=now)
    assert report["status"]=="FAILED" and len(report["errors"])==2


def test_watchdog_reports_failed_stale_and_inactivity_risks():
    now=NOW
    workflow={"state":"disabled_inactivity","created_at":(now-timedelta(days=61)).isoformat()}
    runs=[{"id":1,"event":"schedule","display_title":"Retention archive","conclusion":"failure","created_at":now.isoformat()}]
    report=assess(workflow,runs,{"private":False,"pushed_at":(now-timedelta(days=55)).isoformat()},now=now)
    assert "RETENTION_WORKFLOW_NOT_ACTIVE" in report["errors"]
    assert "LATEST_ARCHIVE_FAILED_OR_CANCELLED" in report["errors"]
    assert report["warnings"] and "INDEPENDENT_MONITORING_REQUIRED" in report["limitation"]


def test_watchdog_bootstrap_then_successful_schedule():
    workflow={"state":"active","created_at":NOW.isoformat()}
    first=assess(workflow,[],{},now=NOW)
    assert first["status"]=="PASS" and len(first["warnings"])==2
    runs=[{"event":"schedule","display_title":f"Retention {mode}","conclusion":"success","created_at":NOW.isoformat()} for mode in ("archive","auto")]
    assert assess(workflow,runs,{},now=NOW)["errors"]==[]


def test_workflow_is_pinned_least_privilege_and_push_is_dry_run():
    root=Path(__file__).resolve().parents[2]
    workflow=(root/".github/workflows/retention-scheduler.yml").read_text()
    assert "cancel-in-progress: false" in workflow and "group: ma-analiz-retention" in workflow
    assert "contents: read" in workflow and "pull_request" not in workflow
    assert "github.event_name != 'push'" in workflow and "default: dry-run" in workflow
    assert "vars.AUTO_RETENTION_DELETE_ENABLED == 'true'" in workflow
    assert 'if [ "$RETENTION_MODE" = auto ]' in workflow and '--mode dry-run' in workflow
    for name in actions.REQUIRED:assert "${{ secrets."+name+" }}" in workflow
    for line in workflow.splitlines():
        if "uses:" in line:
            ref=line.split("@",1)[1].split()[0]
            assert len(ref)==40 and all(c in "abcdef0123456789" for c in ref)
    render=(root/"render.yaml").read_text()
    assert "type: worker" not in render and "type: cron" not in render
    assert "startCommand: node frontend/scripts/run-single-service.mjs" in render


def test_watchdog_repository_root_has_no_trailing_slash(monkeypatch):
    from io import BytesIO
    from app.jobs import actions_watchdog as watchdog
    urls=[]
    def response(request,timeout):
        urls.append(request.full_url)
        assert timeout==20
        return BytesIO(b'{"id": 1}')
    monkeypatch.setattr(watchdog,"urlopen",response)
    assert watchdog.github_get("offline/repo","","SECRET")=={"id":1}
    watchdog.github_get("offline/repo","actions/workflows/test.yml","SECRET")
    assert urls==["https://api.github.com/repos/offline/repo","https://api.github.com/repos/offline/repo/actions/workflows/test.yml"]
