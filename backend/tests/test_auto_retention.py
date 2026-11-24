"""Automatic deletion tests run exclusively on offline SQLite/in-memory R2."""
from contextlib import contextmanager
from types import SimpleNamespace
from datetime import timedelta
import json

import pytest
from sqlalchemy import select, update

from app.core.config import Settings
from app.models import ActiveMatch, ArchiveJob, JobRun, MatchResult, SystemSetting
from app.storage import lifecycle as lc
from app.storage.r2 import InMemoryObjectStore
from test_lifecycle import NOW, fixture as make_fixture, session


def test_auto_flag_defaults_off_and_is_separate_from_manual_gate():
    assert not Settings(_env_file=None).auto_retention_delete_enabled
    cfg=Settings(_env_file=None,auto_retention_delete_enabled=True)
    assert cfg.auto_retention_delete_enabled and not cfg.operational_delete_enabled


def test_disabled_auto_does_not_repair_write_or_delete(session):
    make_fixture(session);store=InMemoryObjectStore()
    result=lc.automatic(session,store,enabled=False,now=NOW)
    assert result["LIVE_DELETE"]=="NOT_RUN" and result["deleted"]==0
    assert store.list_objects()==[] and session.query(ArchiveJob).count()==0
    assert session.query(ActiveMatch).count()==1


def test_auto_repairs_before_deleting_and_audits_atomically_without_manual_digest(session):
    match,_=make_fixture(session);store=InMemoryObjectStore()
    result=lc.automatic(session,store,enabled=True,now=NOW)
    assert result["deleted"]==2 and result["policy_hours"]==48
    assert result["generated_plan_digest"]==result["approval_digest"]
    assert result["archive_repair"]["records_written"]==2
    assert session.query(ActiveMatch).count()==session.query(MatchResult).count()==0
    audit=session.query(JobRun).one()
    assert audit.idempotency_key==f"retention_cleanup:{result['approval_digest']}"
    assert audit.records_written==2
    assert session.get(SystemSetting,"lifecycle:last-auto-delete").value["plan_digest"]==result["approval_digest"]
    assert session.get(SystemSetting,lc.source_index_key(match)).value["cleanup_trigger"]=="automatic"
    original=dict(store._objects)
    repeat=lc.automatic(session,store,enabled=True,now=NOW)
    assert repeat["deleted"]==0 and repeat["LIVE_DELETE"]=="NOT_RUN"
    assert session.query(JobRun).count()==1 and original==store._objects


def test_auto_cannot_use_orm_cache_or_changed_plan_to_delete(session,monkeypatch):
    make_fixture(session);store=InMemoryObjectStore();plan=lc.plan;calls=[]
    def changing(*args,**kwargs):
        report,actions=plan(*args,**kwargs);calls.append(report["approval_digest"])
        if len(calls)==1:
            # Deliberately bypass the ORM cache, while the automatic operation owns locks.
            session.execute(update(MatchResult).values(home_score=9).execution_options(synchronize_session=False))
        return report,actions
    monkeypatch.setattr(lc,"plan",changing)
    with pytest.raises(PermissionError,match="APPROVAL_PLAN_CHANGED"):
        lc.automatic(session,store,enabled=True,now=NOW)
    assert len(calls)==2 and calls[0]!=calls[1]
    assert session.query(MatchResult).one().home_score==1
    assert session.query(ActiveMatch).count()==1 and session.query(JobRun).count()==0


def test_corrupt_archive_is_not_overwritten_or_forced_deleted(session):
    make_fixture(session);store=InMemoryObjectStore();lc.archive_early(session,store,now=NOW)
    key=store.list_objects()[0].key
    store.put_object(key,b"offline corruption",content_type="fault")
    result=lc.automatic(session,store,enabled=True,now=NOW)
    assert result["deleted"]==0 and result["archive_repair"]["records_failed"]>0
    assert store.get_object(key)==b"offline corruption" and session.query(ActiveMatch).count()==1


@pytest.mark.parametrize("age",[47,48])
def test_auto_exact_boundary_and_fixed_48h_policy(session,age):
    make_fixture(session,hours=age);store=InMemoryObjectStore()
    result=lc.automatic(session,store,enabled=True,now=NOW,hours=1)
    assert result["policy_hours"]==48
    assert result["deleted"]==(0 if age==47 else 2)


def test_auto_commit_failure_keeps_data_and_has_no_success_audit(session,monkeypatch):
    make_fixture(session);store=InMemoryObjectStore();lc.archive_early(session,store,now=NOW)
    commit=session.commit
    def fail_success():
        if session.scalar(select(JobRun.id).limit(1)):raise RuntimeError("offline commit fault")
        commit()
    monkeypatch.setattr(session,"commit",fail_success)
    with pytest.raises(RuntimeError):lc.automatic(session,store,enabled=True,now=NOW)
    assert session.query(ActiveMatch).count()==session.query(MatchResult).count()==1
    assert session.query(JobRun).count()==0 and session.get(SystemSetting,"lifecycle:last-auto-delete") is None


def configure_run(monkeypatch,session,store,*,enabled):
    from app.jobs import retention
    real_execute=session.execute
    def execute(statement,*args,**kwargs):
        sql=str(statement)
        if "pg_database_size" in sql:return SimpleNamespace(scalar_one=lambda:8_860_000)
        if "pg_stat_user_tables" in sql:return SimpleNamespace(mappings=lambda:[])
        return real_execute(statement,*args,**kwargs)
    monkeypatch.setattr(session,"execute",execute)
    contexts=[]
    @contextmanager
    def context(cfg,*,readonly):contexts.append(readonly);yield session
    monkeypatch.setattr(retention,"storage_session",context)
    monkeypatch.setattr(retention,"R2ObjectStore",lambda:store)
    monkeypatch.setattr(retention,"metadata_report",lambda s:{"status":"OFFLINE_TEST"})
    monkeypatch.setattr(retention,"target_check",lambda *a:{"status":"OFFLINE_TEST"})
    monkeypatch.setattr(retention,"vacuum_assessment",lambda s:{"manual_vacuum":"NOT_RUN"})
    from alembic.runtime.migration import MigrationContext
    monkeypatch.setattr(MigrationContext,"configure",lambda *a:SimpleNamespace(get_current_heads=lambda:{"0010_active_statistics"}))
    cfg=Settings(_env_file=None,database_url="postgresql://offline:SECRET@localhost/offline",
        r2_endpoint="https://offline.invalid",r2_access_key_id="SECRET",r2_secret_access_key="SECRET",r2_bucket="offline-test",
        auto_retention_delete_enabled=enabled,operational_delete_enabled=False)
    return retention,cfg,contexts


def test_run_auto_uses_write_lock_and_independent_gate_and_persists_cursor(monkeypatch,session):
    make_fixture(session);store=InMemoryObjectStore()
    retention,cfg,contexts=configure_run(monkeypatch,session,store,enabled=True)
    # Use the test policy time; production run uses actual UTC time.
    monkeypatch.setattr(retention,"datetime",SimpleNamespace(now=lambda *a:NOW))
    result=retention.run(mode="auto",settings=cfg)
    assert contexts==[False] and result["deleted"]==2
    assert session.get(SystemSetting,"lifecycle:auto-retention-cursor") is not None
    assert session.get(SystemSetting,"lifecycle:last-auto-run").value["deleted"]==2
    assert "SECRET" not in json.dumps(result,default=str)


def test_run_disabled_auto_is_read_only_and_does_not_write_progress(monkeypatch,session):
    make_fixture(session);store=InMemoryObjectStore()
    retention,cfg,contexts=configure_run(monkeypatch,session,store,enabled=False)
    result=retention.run(mode="auto",settings=cfg)
    assert contexts==[True] and result["deleted"]==0 and store.list_objects()==[]
    assert session.get(SystemSetting,"lifecycle:auto-retention-cursor") is None


def test_scheduler_cleanup_returns_partial_for_overdue_failures(monkeypatch):
    from app.jobs import tasks,retention
    monkeypatch.setattr(tasks,"SessionLocal",object())
    monkeypatch.setattr(retention,"run",lambda **kw:{"status":"COMPLETED","deleted":2,
        "archive_repair":{"records_failed":1},"datasets":{"matches":{"overdue_retained":1}}})
    report=tasks.cleanup_database()
    assert report["status"]=="PARTIAL" and report["records_written"]==2 and report["records_failed"]==1


def test_retention_schedule_is_independent_and_heartbeat_is_bounded(monkeypatch,session):
    import app.worker as worker
    calls=[]
    monkeypatch.setattr(worker,"settings",Settings(_env_file=None,worker_archive_interval_seconds=99999,worker_cleanup_interval_seconds=99999))
    @contextmanager
    def context():yield session
    monkeypatch.setattr(worker,"SessionLocal",context)
    monkeypatch.setattr(worker,"run_task",lambda name,key,ledger:calls.append((name,key)) or {"job":name,"status":"SKIPPED","LIVE_DELETE":"NOT_RUN"})
    worker.retention_cycle(NOW)
    worker.retention_cycle(NOW+timedelta(minutes=17))
    assert [name for name,_ in calls]==["archive_data","cleanup_database"]*2
    assert calls[2][1].endswith("12:15:00+00:00") and calls[3][1].endswith("12:00:00+00:00")
    heartbeat=session.get(SystemSetting,"scheduler:retention-heartbeat")
    assert heartbeat.value["last_cycle_at"]==(NOW+timedelta(minutes=17)).isoformat()
    assert session.query(SystemSetting).filter_by(key="scheduler:retention-heartbeat").count()==1
    calls.clear();monkeypatch.setattr(worker,"SessionLocal",None)
    worker.cycle(NOW,include_retention=False)
    assert [name for name,_ in calls]==["collect_matches","collect_odds","process_results","scheduled_scan"]


def test_busy_database_lock_prevents_second_automatic_worker(monkeypatch):
    from app.jobs import retention
    class Connection:
        def __enter__(self):return self
        def __exit__(self,*args):pass
        def execute(self,statement,args):
            assert "pg_try_advisory_lock" in str(statement) and args["key"]==lc.LOCK_ID
            return SimpleNamespace(scalar_one=lambda:False)
        def commit(self):pass
    class Engine:
        def connect(self):return Connection()
        def dispose(self):pass
    monkeypatch.setattr(retention,"create_engine",lambda *a,**kw:Engine())
    with pytest.raises(RuntimeError,match="LIFECYCLE_ALREADY_RUNNING"):
        with retention.storage_session(Settings(_env_file=None,database_url="postgresql://offline@localhost/offline"),readonly=False):
            pytest.fail("busy worker must never reach writes")


def test_vacuum_assessment_only_selects_and_flags_dead_tuple_pressure():
    from app.jobs.retention import vacuum_assessment
    calls=[]
    class Session:
        @contextmanager
        def begin_nested(self):yield
        def execute(self,statement,*args):
            sql=str(statement);calls.append(sql)
            assert sql.lstrip().startswith("SELECT")
            if "current_setting" in sql:
                return SimpleNamespace(mappings=lambda:SimpleNamespace(one=lambda:{"autovacuum":"on","track_counts":"on"}))
            rows=[{"table":"active_matches","n_live_tup":100,"n_dead_tup":2000}]
            return SimpleNamespace(mappings=lambda:SimpleNamespace(all=lambda:rows))
    report=vacuum_assessment(Session())
    assert len(calls)==2 and report["recommendation"]=="REVIEW_AUTOVACUUM"
    assert report["manual_vacuum"]=="NOT_RUN" and report["vacuum_full"]=="DISABLED"
