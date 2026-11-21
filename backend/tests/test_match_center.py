"""Synthetic SQL/R2 fixtures only. Public readers never call a production writer."""
from datetime import timedelta
import json
from uuid import uuid4

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.api import match_center as api
from app.db.base import Base
from app.models import ActiveMatch, ArchiveJob, MatchAnalysis, MatchResult, SystemSetting
from app.storage import lifecycle as lc
from app.storage.r2 import InMemoryObjectStore
from test_lifecycle import NOW, fixture


@pytest.fixture
def session():
    engine=create_engine("sqlite://",connect_args={"check_same_thread":False},poolclass=StaticPool)
    @event.listens_for(engine,"connect")
    def fk(dbapi,_): dbapi.execute("PRAGMA foreign_keys=ON")
    Base.metadata.create_all(engine)
    with Session(engine,expire_on_commit=False) as sql: yield sql
    engine.dispose()


def archived(session):
    match,result=fixture(session)
    store=InMemoryObjectStore()
    lc.archive_early(session,store,now=NOW)
    return match,result,store


def test_active_and_combined_are_single_uuid_without_r2_requests(session):
    match,result,store=archived(session)
    class Forbidden:
        def head_object(self,*a):pytest.fail("SQL already supplies the canonical UUID")
    for source in ("active","all"):
        page=api.list_view(session,source=source,now=NOW,store=Forbidden())
        assert len(page["items"])==1 and page["items"][0]["id"]==str(match.id)
        assert page["items"][0]["result"]["home_score"]==1
        assert page["items"][0]["storage"]=="POSTGRESQL"


def test_history_and_detail_survive_real_isolated_retention_cleanup(session):
    match,result,store=archived(session)
    history=api.list_view(session,source="history",now=NOW,store=store)
    assert history["items"][0]["id"]==str(match.id) and history["history_status"]=="VERIFIED"
    plan,_=lc.plan(session,store,now=NOW)
    assert lc.execute(session,store,approval=plan["approval_digest"],enabled=True,now=NOW)["deleted"]==2
    assert session.get(ActiveMatch,match.id) is None
    detail=api.detail_view(session,match.id,now=NOW,store=store)
    assert detail["result"]["home_score"]==detail["result"]["away_score"]==1
    assert detail["storage"]=="R2" and detail["source"]["licence"]=="ODbL-1.0"
    assert api.list_view(session,now=NOW,store=store)["items"][0]["id"]==str(match.id)


@pytest.mark.parametrize("fault",["missing","body","metadata","ledger","identity","snapshot"])
def test_bad_archive_never_becomes_a_trusted_score(session,fault):
    match,result,store=archived(session)
    key=next(k for k in store._objects if k.startswith("matches/"))
    if fault=="missing":store._objects.pop(key)
    if fault=="body":
        _,mime,meta=store._objects[key];store._objects[key]=(b"corrupt",mime,meta)
    if fault=="metadata":
        body,mime,meta=store._objects[key];store._objects[key]=(body,mime,{**meta,"row-count":"999"})
    if fault=="ledger":session.query(ArchiveJob).filter_by(r2_key=key).one().status="FAILED";session.commit()
    if fault=="identity":
        item=session.get(SystemSetting,lc.source_index_key(match));item.value={**item.value,"provider_match_id":"different"};session.commit()
    if fault=="snapshot":
        from app.storage.r2 import parquet_rows,parquet_bytes
        import hashlib
        body,mime,meta=store._objects[key];row=parquet_rows(body)[0]
        row["sql_identity"]=str(uuid4());payload=parquet_bytes([row]);checksum=hashlib.sha256(payload).hexdigest()
        store._objects[key]=(payload,mime,{**meta,"sha256":checksum})
        ledger=session.query(ArchiveJob).filter_by(r2_key=key).one();ledger.checksum=checksum;ledger.file_size_bytes=len(payload);session.commit()
    with pytest.raises(HTTPException) as exc:api.detail_view(session,match.id,source="history",now=NOW,store=store)
    assert exc.value.status_code==503 and exc.value.detail=="MATCH_ARCHIVE_NOT_VERIFIED"


@pytest.mark.parametrize("field,value",[("verified_at",None),("home_score",None),("status","IN_PROGRESS")])
def test_unverified_sql_result_is_not_exposed(session,field,value):
    match,result=fixture(session)
    setattr(result,field,value);session.commit()
    assert api.detail_view(session,match.id,now=NOW)["result"] is None


def test_server_filters_dates_league_team_status_and_cursor(session):
    matches=[fixture(session,source_id=f"synthetic-{i}")[0] for i in range(3)]
    page=api.list_view(session,now=NOW,limit=1)
    assert len(page["items"])==1 and page["next_cursor"]
    second=api.list_view(session,now=NOW,limit=1,cursor=api.UUID(page["next_cursor"]))
    assert second["items"][0]["id"]!=page["items"][0]["id"]
    day=lc.aware(matches[0].start_time).astimezone(api.ISTANBUL).date()
    assert len(api.list_view(session,now=NOW,day=day,league="de.bl1",team="offline",status="finished")["items"])==3
    assert api.list_view(session,now=NOW,day=day+timedelta(days=1))["items"]==[]
    assert api.list_view(session,now=NOW,league="en.pl")["items"]==[]
    assert api.list_view(session,now=NOW,team="not-a-team")["items"]==[]
    assert api.list_view(session,now=NOW,status="scheduled")["items"]==[]
    start,end=api.day_bounds(api.date(2026,11,21),now=NOW)
    assert start.isoformat()=="2026-11-20T21:00:00+00:00" and end-start==timedelta(hours=24)


def test_reader_never_writes_or_lists_bucket(session,monkeypatch):
    match,result,store=archived(session)
    monkeypatch.setattr(session,"commit",lambda:pytest.fail("reader committed"))
    monkeypatch.setattr(session,"delete",lambda *a:pytest.fail("reader deleted"))
    for name in ("put_object","put_object_if_absent","delete_object","list_objects"):
        monkeypatch.setattr(store,name,lambda *a,**kw:pytest.fail("reader changed/scanned R2"))
    assert api.detail_view(session,match.id,source="history",now=NOW,store=store)["result"]


def test_historical_verified_analysis_summary_is_read_not_recomputed(session):
    match,result=fixture(session)
    session.add(MatchAnalysis(match_id=match.id,analysis_timestamp=NOW-timedelta(hours=49),analysis_status="ANALYZED",
        final_decision="NO BET",model_version="synthetic",feature_version="synthetic",analysis_output='{"private":"NEVER_EXPORT"}'))
    session.commit();store=InMemoryObjectStore();lc.archive_early(session,store,now=NOW)
    detail=api.detail_view(session,match.id,source="history",now=NOW,store=store)
    assert detail["analysis_summary"]["model_version"]=="synthetic"
    assert "NEVER_EXPORT" not in json.dumps(detail)


def test_public_endpoint_bounds_errors_and_no_internal_metadata(session,monkeypatch):
    match,_=fixture(session)
    app=FastAPI();app.include_router(api.router,prefix="/api/v1")
    app.dependency_overrides[api.read_db]=lambda:session
    client=TestClient(app)
    response=client.get("/api/v1/match-center?source=active&day="+lc.aware(match.start_time).date().isoformat())
    assert response.status_code==200 and response.headers["cache-control"]=="no-store"
    for key in ("sql_snapshot_json","r2_key","expires_at","source_provenance_json","journal","users"):
        assert key not in response.text
    assert client.get("/api/v1/match-center?limit=13").status_code==422
    assert client.get("/api/v1/match-center?source=invalid").status_code==422
    assert client.get("/api/v1/match-center?cursor=bad").status_code==422
    def offline(*args,**kwargs):raise RuntimeError("postgresql://SECRET@private.invalid/db")
    monkeypatch.setattr(api,"list_view",offline)
    response=client.get("/api/v1/match-center")
    assert response.status_code==503 and "SECRET" not in response.text and "private" not in response.text


def test_timeout_is_safe_and_unknown_detail_is_404(session,monkeypatch):
    match,result,store=archived(session)
    def fail(*args):raise TimeoutError("SECRET")
    monkeypatch.setattr(store,"get_object",fail)
    with pytest.raises(HTTPException) as exc:api.detail_view(session,match.id,source="history",now=NOW,store=store)
    assert "SECRET" not in str(exc.value.detail)
    with pytest.raises(HTTPException) as exc:api.detail_view(session,uuid4(),now=NOW,store=store)
    assert exc.value.status_code==404


def test_previously_reviewed_bayern_alias_uses_original_uuid_and_licensed_names(session):
    from test_verified_import import candidate,native_match
    from app.jobs.verified_import import persist_verified
    from datetime import datetime,timezone
    match=native_match();session.add(match);session.commit();store=InMemoryObjectStore()
    persist_verified(session,store,candidate(),now=datetime.now(timezone.utc))
    lc.archive_early(session,store,now=NOW)
    for source in ("all","history"):
        item=api.detail_view(session,match.id,source=source,now=NOW,store=store)
        assert item["id"]==str(match.id) and item["home_team"]=="FC Bayern München"
        assert item["result"]["home_score"]==item["result"]["away_score"]==1
