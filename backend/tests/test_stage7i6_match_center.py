"""Synthetic SQL/fake Parquet read-only HTTP chain and Node probe integration."""
from datetime import datetime, timedelta
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
from uuid import uuid4

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest
from sqlalchemy import event, select, text

from app.api import match_center as api
from app.models import ActiveMatch, ArchiveJob, MatchAnalysis, MatchResult, SystemSetting
from app.storage import lifecycle as lc
from app.storage.r2 import InMemoryObjectStore, parquet_bytes, parquet_rows
from test_lifecycle import NOW, fixture
from test_match_center import session


@pytest.fixture
def view(session,monkeypatch):
    match,result=fixture(session,source_id="SYNTHETIC-7I6")
    for status,hours,version in (("ANALYZED",49,"synthetic-old"),("ANALYZED",48,"synthetic-current"),
                                 ("RUNNING",1,"synthetic-running"),("ANALYZED",-1,"synthetic-future")):
        session.add(MatchAnalysis(match_id=match.id,analysis_timestamp=NOW-timedelta(hours=hours),analysis_status=status,
            final_decision="NO BET",model_version=version,feature_version="synthetic-1",analysis_output='{"private":"PRIVATE_7I6"}'))
    session.commit();store=InMemoryObjectStore();report=lc.archive_early(session,store,now=NOW)
    assert report["records_failed"]==0
    class Clock(datetime):
        @classmethod
        def now(cls,tz=None):return NOW if tz else NOW.replace(tzinfo=None)
    monkeypatch.setattr(api,"datetime",Clock)
    monkeypatch.setattr(api,"store_factory",lambda:store)
    monkeypatch.setattr(api,"read_engine",lambda:pytest.fail("Production engine forbidden"))
    return session,store,match,result


def client_for(session,monkeypatch):
    # Seed is over; every HTTP observation must use only SELECT on a query-only connection.
    session.execute(text("PRAGMA query_only=ON"))
    statements=[]
    def observe(conn,cursor,statement,*a):
        statements.append(statement)
        assert statement.lstrip().upper().startswith("SELECT"),"WRITE_DURING_READBACK"
    event.listen(session.get_bind(),"before_cursor_execute",observe)
    monkeypatch.setattr(session,"commit",lambda:pytest.fail("Readback cannot commit"))
    app=FastAPI();app.include_router(api.router,prefix="/api/v1")
    app.dependency_overrides[api.read_db]=lambda:session
    return TestClient(app),statements,observe


def chain(view,monkeypatch):
    session,store,match,_=view
    client,statements,observe=client_for(session,monkeypatch)
    try:
        with client:
            page=client.get("/api/v1/match-center?limit=1")
            assert page.status_code==200 and len(page.json()["items"])==1
            uid=page.json()["items"][0]["id"]
            detail=client.get("/api/v1/match-center/"+uid)
            history=client.get("/api/v1/match-center?source=history&limit=1")
            assert history.status_code==200 and history.json()["history_status"]=="VERIFIED"
            old=history.json()["items"][0]["id"]
            archive=client.get("/api/v1/match-center/"+old+"?source=history")
            responses={"/api/v1/match-center?limit=1":page,"/api/v1/match-center/"+uid:detail,
                "/api/v1/match-center?source=history&limit=1":history,"/api/v1/match-center/"+old+"?source=history":archive}
            for response in responses.values():
                assert response.status_code==200
                assert response.headers["cache-control"]=="no-store"
                assert "PRIVATE_7I6" not in response.text and "sql_snapshot_json" not in response.text
            assert uid==old==str(match.id)
            assert detail.json()["storage"]=="POSTGRESQL" and archive.json()["storage"]=="R2"
            assert detail.json()["result"]==archive.json()["result"]
            assert detail.json()["analysis_summary"]["model_version"]==archive.json()["analysis_summary"]["model_version"]=="synthetic-current"
            assert statements
            return {path:{"statusCode":r.status_code,"body":r.json()} for path,r in responses.items()}
    finally:event.remove(session.get_bind(),"before_cursor_execute",observe)


def test_readonly_list_detail_history_detail_same_uuid_and_analysis_metadata(view,monkeypatch):
    chain(view,monkeypatch)


def test_empty_database_and_unknown_uuid_never_fabricate_data(session,monkeypatch):
    monkeypatch.setattr(api,"store_factory",lambda:pytest.fail("Empty history must not scan R2"))
    monkeypatch.setattr(api,"read_engine",lambda:pytest.fail("Production engine forbidden"))
    client,_,observe=client_for(session,monkeypatch)
    try:
        with client:
            for source in ("all","active","history"):
                response=client.get("/api/v1/match-center",params={"source":source})
                assert response.status_code==200 and response.json()["items"]==[]
                assert response.json()["history_status"]=="NOT_READ"
            assert client.get(f"/api/v1/match-center/{uuid4()}").status_code==404
    finally:event.remove(session.get_bind(),"before_cursor_execute",observe)


def test_one_corrupt_archive_is_explicitly_partial_not_verified(view,monkeypatch):
    session,store,_,_=view
    other,_=fixture(session,source_id="SYNTHETIC-7I6-SECOND")
    lc.archive_early(session,store,now=NOW)
    index=session.get(SystemSetting,lc.source_index_key(other))
    store._objects.pop(index.value["first_verified_fixture_key"])
    client,_,observe=client_for(session,monkeypatch)
    try:
        with client:
            response=client.get("/api/v1/match-center?source=history")
            assert response.status_code==200 and len(response.json()["items"])==1
            assert response.json()["history_status"]=="PARTIAL"
            assert response.json()["warnings"]==["SOME_MATCHES_NOT_VERIFIED"]
    finally:event.remove(session.get_bind(),"before_cursor_execute",observe)


def test_real_synthetic_http_responses_satisfy_frontend_probe_but_never_verify_analysis(view,monkeypatch):
    if os.environ.get("STAGE7I6_NODE_CONTRACT")!="true":pytest.skip("PR-only offline Node contract opt-in absent")
    node=shutil.which("node")
    assert node,"NODE_REQUIRED_FOR_PR_CONTRACT"
    routes=chain(view,monkeypatch)
    routes.update({"/":{"statusCode":200},"/health/live":{"statusCode":200,"body":{"status":"ok"}},
        "/health/ready":{"statusCode":200,"body":{"status":"ok","scope":"web_api_transport","dependencies":"not_checked"}},
        "/api/v1/health":{"statusCode":200,"body":{"status":"ok","database":"CONNECTED","database_schema":"READY","r2":"CONNECTED"}}})
    result=subprocess.run([node,str(Path(__file__).with_name("node_match_center_bridge.mjs"))],input=json.dumps(routes),
        capture_output=True,text=True,timeout=10,check=False)
    assert result.returncode==0,"OFFLINE_PROBE_BRIDGE_FAILED"
    report=json.loads(result.stdout)
    assert report["fixture_requests"]==8 and report["blockers"]==["ANALYSIS_NOT_VERIFIED"]
    assert report["result"]==report["analysis"]==report["LIVE_MATCH_CENTER"]=="NOT_VERIFIED"
    assert report["SAFE_TO_DELETE"]=="NO" and report["LIVE_DELETE"]=="NOT_RUN"
    assert "PRIVATE_7I6" not in result.stdout and "sql_snapshot_json" not in result.stdout
    assert "SYNTHETIC-7I6" not in result.stdout
    assert str(view[2].id) not in result.stdout and view[2].home_team not in result.stdout


@pytest.mark.parametrize("damage",["missing","checksum","licence","attribution"])
def test_bad_archived_object_or_source_policy_is_not_a_trusted_history(view,monkeypatch,damage):
    session,store,match,_=view
    key=next(k for k in store._objects if k.startswith("matches/"))
    if damage=="missing":store._objects.pop(key)
    elif damage=="checksum":
        body,mime,meta=store._objects[key];store._objects[key]=(b"bad",mime,meta)
    else:
        body,mime,meta=store._objects[key];row=parquet_rows(body)[0]
        row[damage]="UNVERIFIED"
        payload=parquet_bytes([row]);checksum=hashlib.sha256(payload).hexdigest()
        store._objects[key]=(payload,mime,{**meta,"sha256":checksum})
        ledger=session.scalar(select(ArchiveJob).where(ArchiveJob.r2_key==key))
        ledger.checksum=checksum;ledger.file_size_bytes=len(payload);session.commit()
    client,_,observe=client_for(session,monkeypatch)
    try:
        with client:
            response=client.get(f"/api/v1/match-center/{match.id}?source=history")
            assert response.status_code==503 and response.json()["detail"]=="MATCH_ARCHIVE_NOT_VERIFIED"
    finally:event.remove(session.get_bind(),"before_cursor_execute",observe)


@pytest.mark.parametrize("field,value",[("home_score",None),("home_score",-1),("verified_at",None),
                                      ("verified_at",NOW+timedelta(hours=1)),("status","IN_PROGRESS")])
def test_unverified_sql_final_is_never_fabricated(view,monkeypatch,field,value):
    session,_,match,result=view;setattr(result,field,value);session.commit()
    client,_,observe=client_for(session,monkeypatch)
    try:
        with client:
            response=client.get(f"/api/v1/match-center/{match.id}")
            assert response.status_code==200 and response.json()["result"] is None
    finally:event.remove(session.get_bind(),"before_cursor_execute",observe)


def test_unknown_uuid_and_missing_policy_degrade_safely(view,monkeypatch):
    session,_,match,_=view;monkeypatch.setattr(lc,"policy_for",lambda *a,**kw:None)
    client,_,observe=client_for(session,monkeypatch)
    try:
        with client:
            assert client.get(f"/api/v1/match-center/{uuid4()}").status_code==404
            response=client.get(f"/api/v1/match-center/{match.id}")
            assert response.json()["source"] is None and response.json()["result"] is None
            assert client.get(f"/api/v1/match-center/{match.id}?source=history").status_code==503
    finally:event.remove(session.get_bind(),"before_cursor_execute",observe)
