"""Offline-only SQLite + in-memory R2 tests; never production DELETE evidence."""
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import json

import pytest
from sqlalchemy import Column, ForeignKey, Integer, Table, create_engine, event
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.db.base import Base
from app.models import (ActiveMatch, ActiveOdds, ActiveStatistic, ArchiveJob, Coupon, CouponItem,
                        JobRun, MatchAnalysis, MatchResult, Prediction, SystemSetting, User)
from app.storage import lifecycle as lc
from app.storage.r2 import InMemoryObjectStore

# Synthetic fixtures must never be observed before their DB server-created
# available_at timestamps. A hard-coded timestamp can become stale in CI.
NOW = max(datetime(2026, 11, 25, 12, tzinfo=timezone.utc),
          datetime.now(timezone.utc) + timedelta(minutes=5))


@compiles(JSONB, "sqlite")
def _jsonb(element, compiler, **kw): return "JSON"


@pytest.fixture
def session():
    engine = create_engine("sqlite://")
    @event.listens_for(engine, "connect")
    def fk(dbapi, _): dbapi.execute("PRAGMA foreign_keys=ON")
    Base.metadata.create_all(engine)
    with Session(engine, expire_on_commit=False) as sql:
        yield sql
    engine.dispose()


def fixture(session, *, hours=50, source_id="offline-1"):
    match = ActiveMatch(provider="openligadb", provider_match_id=source_id, league_id="de.bl1", league_name="Bundesliga",
        season="2026-2027", home_team="Offline Home", away_team="Offline Away", status="FINISHED",
        start_time=NOW-timedelta(hours=hours), expires_at=NOW+timedelta(days=14))
    session.add(match); session.flush()
    result = MatchResult(match_id=match.id, provider="openligadb", provider_result_id="offline-1", result_source="offline ODbL fixture",
        status="FINAL", home_score=1, away_score=1, verified_at=NOW-timedelta(hours=49))
    session.add(result); session.commit()
    return match, result


def archive(session, store, **kw): return lc.archive_early(session, store, now=NOW, **kw)
def plan(session, store, **kw): return lc.plan(session, store, now=NOW, **kw)[0]
def eligible(report): return sum(r["eligible"] for r in report["datasets"].values())


def test_early_archive_and_dry_plan_never_delete_then_exact_48h_boundary(session):
    match, _ = fixture(session, hours=48)
    store = InMemoryObjectStore()
    assert eligible(plan(session, store)) == 0
    assert archive(session, store)["records_written"] == 2
    report = plan(session, store)
    assert eligible(report) == 2 and report["LIVE_DELETE"] == "NOT_RUN" and report["deleted"] == 0
    assert session.query(ActiveMatch).count() == session.query(MatchResult).count() == 1
    early, _ = lc.plan(session, store, now=NOW-timedelta(microseconds=1))
    assert eligible(early) == 0
    assert any("WITHIN_RETENTION_WINDOW" in r["reasons"] for r in early["records"])


def test_archive_is_early_not_waiting_for_48h_and_idempotent(session):
    fixture(session, hours=2)
    store = InMemoryObjectStore()
    assert archive(session, store)["records_written"] == 2
    original = dict(store._objects)
    assert archive(session, store)["records_written"] == 0
    assert original == store._objects and eligible(plan(session, store)) == 0


def test_execute_requires_both_explicit_gates_and_preserves_critical_metadata(session):
    fixture(session)
    session.add_all([User(), SystemSetting(key="source-ids", value={"keep": True}),
                     JobRun(idempotency_key="offline-job", job_type="test", status="SUCCESS", started_at=NOW)])
    session.commit()
    store = InMemoryObjectStore(); archive(session, store)
    report = plan(session, store)
    with pytest.raises(PermissionError): lc.execute(session, store, approval=report["approval_digest"], enabled=False, now=NOW)
    with pytest.raises(PermissionError): lc.execute(session, store, approval="wrong", enabled=True, now=NOW)
    assert session.query(ActiveMatch).count() == 1
    original = dict(store._objects)
    result = lc.execute(session, store, approval=report["approval_digest"], enabled=True, now=NOW)
    assert result["deleted"] == 2 and session.query(ActiveMatch).count() == session.query(MatchResult).count() == 0
    assert session.query(User).count() == 1 and session.query(JobRun).count() == 2  # original + atomic cleanup audit
    assert session.get(SystemSetting, "source-ids").value == {"keep": True}
    assert session.query(ArchiveJob).count() == 2 and original == store._objects
    assert session.query(SystemSetting).filter(SystemSetting.key.like("source-identity:%")).count()==1
    assert plan(session, store)["deleted"] == 0


@pytest.mark.parametrize("damage", ["body", "missing", "metadata", "ledger", "columns"])
def test_corrupt_missing_or_changed_sql_retains_everything(session, damage):
    match, result = fixture(session)
    store = InMemoryObjectStore(); archive(session, store)
    report = plan(session, store)
    key = report["records"][0]["archive_key"]
    if damage == "body": store.put_object(key, b"corrupt", content_type="bad")
    if damage == "missing": store.delete_object(key)  # offline test fault only
    if damage == "metadata":
        body, mime, meta = store._objects[key]; store._objects[key] = (body, mime, {**meta, "row-count": "999"})
    if damage == "ledger": session.query(ArchiveJob).first().status = "FAILED"; session.commit()
    if damage == "columns": result.home_score = 2; session.commit()
    assert eligible(plan(session, store)) == 0
    with pytest.raises(PermissionError): lc.execute(session, store, approval=report["approval_digest"], enabled=True, now=NOW)
    assert session.query(ActiveMatch).count() == session.query(MatchResult).count() == 1


def test_unsettled_prediction_or_new_child_blocks_whole_bundle(session):
    match, _ = fixture(session)
    prediction = Prediction(match_id=match.id, market="1X2", selection="DRAW", probability=Decimal(".3"),
        confidence=Decimal(".5"), value_score=0, evidence_status="offline", model_version="offline", status="PENDING",
        analysis_timestamp=NOW-timedelta(hours=49), expires_at=NOW-timedelta(hours=1), created_at=NOW-timedelta(hours=49))
    session.add(prediction); session.commit()
    store=InMemoryObjectStore(); archive(session, store)
    report=plan(session, store)
    assert eligible(report)==0
    assert any("PREDICTION_UNSETTLED" in row["reasons"] for row in report["records"])
    prediction.status="WON"; prediction.settled_at=NOW; session.commit()
    archive(session, store)
    assert eligible(plan(session, store))==0  # recent settlement still protected for 48h


def test_dependency_limit_is_visible_not_silently_cascaded(session):
    match,_=fixture(session)
    for index in range(3):
        session.add(MatchAnalysis(match_id=match.id, analysis_timestamp=NOW-timedelta(hours=49),
            analysis_status="COMPLETED", final_decision="NO BET", model_version=str(index), feature_version="offline", analysis_output="{}"))
    session.commit()
    store=InMemoryObjectStore(); archive(session,store,child_limit=1)
    report=plan(session,store,child_limit=1)
    assert eligible(report)==0 and any("DEPENDENCY_SCAN_LIMIT" in r["reasons"] for r in report["records"])


def test_nonpermitted_source_is_held_no_archive_and_no_forced_storage_delete(session):
    match,_=fixture(session); match.provider="goaloo"; match.provider_match_id="unreviewed";session.commit()
    store=InMemoryObjectStore();result=archive(session,store)
    assert result["records_failed"]>=1
    assert eligible(plan(session,store))==0 and session.query(ActiveMatch).count()==1


def test_partial_upload_failure_is_retryable_and_preserves_sql(session):
    fixture(session)
    class FailedStore(InMemoryObjectStore):
        failed=False
        def put_object_if_absent(self,key,body,**kw):
            if not self.failed:
                self.failed=True
                super().put_object_if_absent(key,body,**kw)
                raise TimeoutError("SECRET_MARKER")
            return super().put_object_if_absent(key,body,**kw)
    store=FailedStore();first=archive(session,store)
    assert first["status"]=="PARTIAL" and "SECRET_MARKER" not in json.dumps(first)
    assert session.query(ActiveMatch).count()==1
    original=dict(store._objects)
    assert archive(session,store)["status"]=="SUCCESS"
    assert all(store._objects[k]==v for k,v in original.items())
    assert eligible(plan(session,store))==2


def test_unknown_fk_blocks_even_after_archiving(session):
    fixture(session);store=InMemoryObjectStore();archive(session,store)
    table=Table("unreviewed_links",Base.metadata,Column("id",Integer,primary_key=True),
                Column("match_id",ForeignKey("active_matches.id")))
    try:
        table.create(session.get_bind())
        with pytest.raises(PermissionError,match="UNKNOWN_FOREIGN_KEY_DEPENDENCY"):
            lc.execute(session,store,approval=plan(session,store)["approval_digest"],enabled=True,now=NOW)
        assert session.query(ActiveMatch).count()==1
    finally:
        Base.metadata.remove(table)


def test_sql_failure_rolls_back_deletion_and_r2_unchanged(session,monkeypatch):
    fixture(session);store=InMemoryObjectStore();archive(session,store)
    report=plan(session,store);original=dict(store._objects)
    commit=session.commit
    monkeypatch.setattr(session,"commit",lambda:(_ for _ in ()).throw(RuntimeError("commit failed")))
    with pytest.raises(RuntimeError):lc.execute(session,store,approval=report["approval_digest"],enabled=True,now=NOW)
    monkeypatch.setattr(session,"commit",commit)
    assert session.query(ActiveMatch).count()==session.query(MatchResult).count()==1
    assert store._objects==original


def test_live_readonly_command_missing_config_is_honest_not_run(monkeypatch,capsys):
    from app.jobs import retention
    monkeypatch.setattr(retention,"get_settings",lambda:Settings(_env_file=None))
    monkeypatch.setattr(retention,"storage_session",lambda *a,**kw:pytest.fail("no connection"))
    assert retention.main(["--dry-run","--target"])==1
    report=json.loads(capsys.readouterr().out)
    assert report["postgres"]==report["r2"]==report["target"]["status"]==report["LIVE_DELETE"]=="NOT_RUN"


def test_capacity_warns_before_one_gb_and_config_cannot_exceed_48h():
    from app.jobs.retention import capacity
    assert capacity(700_000_000)["status"]=="WARNING"
    assert capacity(800_000_000)["status"]=="HIGH"
    assert capacity(900_000_000)["status"]=="CRITICAL"
    with pytest.raises(ValueError):Settings(_env_file=None,operational_retention_hours=49)


def test_permitted_statistics_only_and_duplicate_observation_not_changed(session):
    from app.jobs.technical_ingestion import store_permitted_statistics
    match,result=fixture(session)
    rows=[{"id":"offline-stat", "provider":"openligadb", "provider_match_id":match.provider_match_id,
        "name":"Shots", "unit":"count", "period":"ftstat", "home_value":10,"away_value":3,
        "source_url":"https://offline.invalid/stat", "observed_at":(NOW-timedelta(hours=48.5)).isoformat()}]
    assert store_permitted_statistics(session,match,rows,now=NOW)["written"]==0
    session.add(SystemSetting(key="source-reuse-policy:openligadb",value={"approved":True,"provider":"openligadb",
        "datasets":["statistics"],"licence":"offline-test-only","attribution":"offline fixture",
        "reference":"https://offline.invalid/permission"}));session.commit()
    assert store_permitted_statistics(session,match,rows,now=NOW)["written"]==1
    assert store_permitted_statistics(session,match,rows,now=NOW)["written"]==0
    assert store_permitted_statistics(session,match,[{**rows[0],"home_value":None}],now=NOW)["rejected"]==1
    session.commit()
    store=InMemoryObjectStore();archive(session,store)
    assert session.query(ActiveStatistic).count()==1
    assert eligible(plan(session,store))==3


def test_complete_analysis_prediction_coupon_odds_bundle_deletes_child_first_only_in_offline_test(session):
    match,_=fixture(session)
    policy={"approved":True,"provider":"openligadb","datasets":["odds"],"licence":"offline-test-only",
            "attribution":"offline fixture","reference":"https://offline.invalid/permission"}
    session.add(SystemSetting(key="source-reuse-policy:openligadb",value=policy))
    session.add(ActiveOdds(match_id=match.id,provider="openligadb",source="openligadb",bookmaker="offline",
        market="1X2",selection="DRAW",odds=Decimal("3"),captured_at=NOW-timedelta(hours=49),expires_at=NOW))
    prediction=Prediction(match_id=match.id,market="1X2",selection="DRAW",probability=Decimal(".3"),
        confidence=Decimal(".5"),value_score=0,evidence_status="offline",model_version="offline",status="WON",
        analysis_timestamp=NOW-timedelta(hours=49),created_at=NOW-timedelta(hours=49),settled_at=NOW-timedelta(hours=49),expires_at=NOW)
    coupon=Coupon(type="SINGLE",total_odds=3,confidence=Decimal(".5"),status="PAPER_WON",
        created_at=NOW-timedelta(hours=49),settled_at=NOW-timedelta(hours=49),expires_at=NOW)
    session.add_all([prediction,coupon]);session.flush()
    session.add(CouponItem(coupon_id=coupon.id,match_id=match.id,prediction_id=prediction.id,market="1X2",
        selection="DRAW",odds=3,status="WON",settled_at=NOW-timedelta(hours=49)))
    session.add(MatchAnalysis(match_id=match.id,analysis_timestamp=NOW-timedelta(hours=49),analysis_status="COMPLETED",
        final_decision="NO BET",model_version="offline",feature_version="offline",analysis_output='{"offline":true}'))
    session.commit()
    store=InMemoryObjectStore();archive(session,store)
    report=plan(session,store)
    assert eligible(report)==7
    lc.execute(session,store,approval=report["approval_digest"],enabled=True,now=NOW)
    assert all(session.query(model).count()==0 for model in (ActiveMatch,MatchResult,ActiveOdds,Prediction,Coupon,CouponItem,MatchAnalysis))
    assert session.query(ArchiveJob).count()==7 and session.get(SystemSetting,"source-reuse-policy:openligadb")


def test_coupon_spanning_other_match_is_reported_and_retained(session):
    first,_=fixture(session);second,_=fixture(session,source_id="offline-2")
    coupon=Coupon(type="MULTI",total_odds=3,confidence=Decimal(".5"),status="PAPER_WON",
        created_at=NOW-timedelta(hours=49),settled_at=NOW-timedelta(hours=49),expires_at=NOW)
    session.add(coupon);session.flush()
    for match in (first,second):
        session.add(CouponItem(coupon_id=coupon.id,match_id=match.id,market="1X2",selection="DRAW",odds=2,
            status="WON",settled_at=NOW-timedelta(hours=49)))
    session.commit()
    store=InMemoryObjectStore();archive(session,store)
    report=plan(session,store)
    assert eligible(report)==0 and any("EXTERNAL_COUPON_DEPENDENCY" in r["reasons"] for r in report["records"])
    assert session.query(CouponItem).count()==2


def test_r2_history_survives_sql_cleanup_and_validates_versions_at_cutoff(session):
    from app.historical.repository import HistoricalRepository
    match,result=fixture(session);store=InMemoryObjectStore();archive(session,store)
    start=lc.aware(match.start_time).replace(hour=0,minute=0,second=0,microsecond=0)
    args={"league":"de.bl1","season":"2026-2027","start":start,"end":start+timedelta(days=1)}
    repository=HistoricalRepository(store)
    assert repository.get_similarity_history(**args,before=NOW)[0]["home_score"]==1
    # A later correction must have a later event timestamp; never backdate a
    # correction before the original revision's own SQL updated_at timestamp.
    correction_at = NOW + timedelta(minutes=1)
    result.home_score=2;result.updated_at=correction_at;session.commit()
    lc.archive_early(session,store,now=correction_at+timedelta(minutes=1))
    # Historical queries may not see a revision that has not yet become available.
    assert repository.get_similarity_history(**args,before=NOW-timedelta(hours=45))==[]
    assert repository.get_similarity_history(**args,before=NOW)[0]["home_score"]==1
    assert repository.get_similarity_history(**args,before=correction_at+timedelta(minutes=2))[0]["home_score"]==2
    later=NOW+timedelta(hours=49)
    report,_=lc.plan(session,store,now=later)
    lc.execute(session,store,approval=report["approval_digest"],enabled=True,now=later)
    assert session.query(ActiveMatch).count()==0
    assert repository.get_similarity_history(**args,before=later)[0]["home_score"]==2


def test_bayern_end_to_end_offline_canonical_archive_and_operational_alias(session,monkeypatch):
    from test_verified_import import candidate, native_match
    from app.jobs.verified_import import persist_verified
    from app.jobs.retention import target_check
    from app.analysis.scanning import _r2_history
    import app.analysis.scanning as scanner
    match=native_match();session.add(match);session.commit()
    store=InMemoryObjectStore()
    first=persist_verified(session,store,candidate(),now=datetime.now(timezone.utc))
    assert first["status"]=="VERIFIED"
    assert target_check(session,store)["status"]=="VERIFIED"
    archive(session,store)
    monkeypatch.setattr(scanner,"R2ObjectStore",lambda:store)
    history=_r2_history(match,as_of=NOW)
    assert history["status"]=="AVAILABLE" and len(history["rows"])==1
    assert history["rows"][0]["provider"]=="goaloo"
    assert history["rows"][0]["provider_match_id"]=="3018869"
    assert history["rows"][0]["home_team"]=="Bayern Munchen"
    assert history["rows"][0]["canonical_league_id"]=="de.bl1"
    assert history["rows"][0]["attribution"].startswith("Data: OpenLigaDB")
    report=plan(session,store)
    # The verified import was observed recently. Retention must not delete it
    # before its 48-hour protection window expires, even when a manual gate
    # is supplied with a valid empty plan.
    assert report["pending_approval"]==0
    assert any("WITHIN_RETENTION_WINDOW" in row["reasons"] for row in report["records"])
    lc.execute(session,store,approval=report["approval_digest"],enabled=True,now=NOW)
    assert target_check(session,store)["status"]=="VERIFIED"
    assert session.query(ActiveMatch).count()==1


def test_scheduler_uses_gated_automatic_retention_path(monkeypatch):
    from app.jobs import tasks,retention
    calls=[]
    monkeypatch.setattr(tasks,"SessionLocal",object())
    monkeypatch.setattr(retention,"run",lambda **kw:calls.append(kw) or {"status":"DRY_RUN","deleted":0,"LIVE_DELETE":"NOT_RUN"})
    report=tasks.cleanup_database()
    assert calls==[{"mode":"auto"}] and report["deleted"]==0 and report["LIVE_DELETE"]=="NOT_RUN"


def test_legacy_sql_job_prune_is_disabled(session):
    from app.jobs.ledger import SQLLedger
    session.add(JobRun(idempotency_key="protected-old",job_type="offline",status="SUCCESS",
                       started_at=NOW-timedelta(days=365),created_at=NOW-timedelta(days=365)))
    session.commit()
    assert SQLLedger(session).prune(1)==0 and session.query(JobRun).count()==1


def test_object_changed_during_predelete_recheck_retains_sql(session,monkeypatch):
    fixture(session);store=InMemoryObjectStore();archive(session,store)
    report=plan(session,store);verify=lc.verify_record;calls=[]
    def changed(sql,objects,record):
        calls.append(record.key)
        if len(calls)==3: # two checks inside the fresh plan, then pre-delete check
            objects.put_object(record.key,b"offline fault",content_type="fault")
        return verify(sql,objects,record)
    monkeypatch.setattr(lc,"verify_record",changed)
    with pytest.raises(RuntimeError,match="PRE_DELETE_REVERIFICATION_FAILED"):
        lc.execute(session,store,approval=report["approval_digest"],enabled=True,now=NOW)
    assert session.query(ActiveMatch).count()==session.query(MatchResult).count()==1


def test_unverified_legacy_parquet_is_not_analysis_evidence():
    from app.historical.repository import HistoricalRepository
    from app.storage.r2 import parquet_bytes
    store=InMemoryObjectStore();key="matches/season=2026/league=x/date=2026-11-20.parquet"
    store.put_object(key,parquet_bytes([{"start_time":NOW,"home_team":"offline"}]),content_type="application/vnd.apache.parquet")
    with pytest.raises(IOError,match="CHECKSUM_NOT_VERIFIED"):
        HistoricalRepository(store)._read([key],before=NOW)


@pytest.mark.parametrize("status,expected",[("FINISHED",0),("CANCELLED",1)])
def test_finished_fixture_waits_for_verified_result_but_cancelled_fixture_needs_no_invented_score(session,status,expected):
    match,result=fixture(session)
    session.delete(result)  # offline fixture setup only
    match.status=status;session.commit()
    store=InMemoryObjectStore();archive(session,store)
    report=plan(session,store)
    assert eligible(report)==expected
    if status=="FINISHED":
        assert "FINAL_RESULT_MISSING_OR_UNVERIFIED" in report["records"][0]["reasons"]
