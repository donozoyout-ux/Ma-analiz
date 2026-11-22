"""Opt-in local integration harness. Synthetic SQLite + memory objects ONLY.

Runs real app routers; production settings/clients, egress and SQL writes after
fixture setup are forbidden. Never imported by the production app or worker.
"""
from datetime import datetime, timedelta, timezone
import hashlib
import os
from pathlib import Path
import socket
from uuid import UUID


def guard():
    if os.environ.get("BETA_INTEGRATION_TEST") != "true":
        raise RuntimeError("SYNTHETIC_TEST_OPT_IN_REQUIRED")
    if any(value for key,value in os.environ.items() if key.startswith(("R2_","PG")) or
           any(word in key.upper() for word in ("DATABASE_URL","API_KEY","TOKEN","CREDENTIAL","SECRET","PASSWORD"))):
        raise RuntimeError("STORAGE_OR_CREDENTIAL_ENV_FORBIDDEN")
    if any(os.environ.get(key) != "false" for key in ("AUTO_RETENTION_DELETE_ENABLED","OPERATIONAL_DELETE_ENABLED")):
        raise RuntimeError("DELETE_GATES_MUST_BE_FALSE")
    env_file=Path(__file__).resolve().parents[2]/".env"
    if env_file.exists() and env_file.read_text(encoding="utf-8").strip():
        raise RuntimeError("NONEMPTY_ENV_FILE_FORBIDDEN")


guard()
connect=socket.socket.connect
dns=socket.getaddrinfo
def local_connect(self,address):
    if isinstance(address,tuple) and address[0] not in {"localhost","127.0.0.1","::1"}:
        raise PermissionError("NONLOCAL_EGRESS_FORBIDDEN")
    return connect(self,address)
def local_dns(host,*args,**kwargs):
    if host not in {None,"localhost","127.0.0.1","::1"}:
        raise PermissionError("NONLOCAL_EGRESS_FORBIDDEN")
    return dns(host,*args,**kwargs)
socket.socket.connect=local_connect
socket.getaddrinfo=local_dns

from fastapi import HTTPException, Request
from sqlalchemy import create_engine, event, text
from sqlalchemy.orm import Session, attributes
from sqlalchemy.pool import StaticPool
from app.main import app
from app.api import match_center as center, routes
from app.db import session as db_module
from app.db.base import Base
from app.models import ActiveMatch, MatchResult, SystemSetting
from app.storage import lifecycle as lc
from app.storage.r2 import InMemoryObjectStore
from test_lifecycle import fixture, NOW  # also registers the SQLite JSONB test compiler

assert db_module.engine is None, "APPLICATION_ENGINE_MUST_BE_UNCONFIGURED"
state={"mode":"empty","sql_reads":0,"sql_write_attempts":0,"object_reads":0,"object_write_attempts":0}
def engine():
    result=create_engine("sqlite://",connect_args={"check_same_thread":False},poolclass=StaticPool)
    @event.listens_for(result,"connect")
    def foreign_keys(conn,_):conn.execute("PRAGMA foreign_keys=ON")
    Base.metadata.create_all(result)
    return result
data,empty=engine(),engine()
store=InMemoryObjectStore()
UID=UUID("a1111111-1111-4111-8111-111111111111")
with Session(data,expire_on_commit=False) as sql:
    # Fixture times/UUIDs only. No production data is present or altered.
    match=ActiveMatch(id=UID,provider="openligadb",provider_match_id="SYNTHETIC-BETA-TODAY",league_id="de.bl1",
        league_name="Bundesliga",season="2026-2027",home_team="SENTETİK Ev Takımı",away_team="SENTETİK Konuk Takımı",
        status="FINISHED",start_time=NOW-timedelta(hours=2),expires_at=NOW+timedelta(days=1),last_updated_at=NOW-timedelta(minutes=20))
    sql.add(match);sql.flush()
    sql.add(MatchResult(match_id=UID,provider="openligadb",provider_result_id="SYNTHETIC-BETA-TODAY",result_source="Synthetic licensed fixture",
        home_score=1,away_score=1,status="FINAL",verified_at=NOW-timedelta(minutes=30)))
    other,_=fixture(sql,source_id="SYNTHETIC-BETA-OLDER")
    other.home_team="SENTETİK Arşiv Ev";other.away_team="SENTETİK Arşiv Konuk";sql.commit()
    report=lc.archive_early(sql,store,now=NOW)
    assert report["records_failed"]==0
    partial_key=sql.get(SystemSetting,lc.source_index_key(other)).value["first_verified_fixture_key"]
original={key:hashlib.sha256(value[0]).hexdigest() for key,value in store._objects.items()}

class Clock(datetime):
    @classmethod
    def now(cls,tz=None):return NOW if tz else NOW.replace(tzinfo=None)
center.datetime=routes.datetime=Clock

# SQLite drops timezone offsets. Restore UTC on loaded fixture values, matching
# PostgreSQL timestamptz DTO semantics, without changing rows or issuing writes.
def utc_fixture_values(instance,_):
    for name in ("start_time","expires_at","last_updated_at","verified_at"):
        value=getattr(instance,name,None)
        if isinstance(value,datetime) and value.tzinfo is None:
            attributes.set_committed_value(instance,name,value.replace(tzinfo=timezone.utc))
event.listen(ActiveMatch,"load",utc_fixture_values)
event.listen(MatchResult,"load",utc_fixture_values)

class ReadOnlyObjects:
    def head_object(self,key):
        state["object_reads"]+=1
        if state["mode"]=="missing" or state["mode"]=="partial" and key==partial_key:return None
        if state["mode"]=="timeout":raise TimeoutError("SYNTHETIC_TIMEOUT")
        return store.head_object(key)
    def get_object(self,key):
        state["object_reads"]+=1
        if state["mode"]=="corrupt":return b"SYNTHETIC_CORRUPT_ARCHIVE"
        return store.get_object(key)
    def forbidden(self,*a,**kw):
        state["object_write_attempts"]+=1
        raise PermissionError("READ_ONLY_OBJECTS")
    put_object=put_object_if_absent=delete_object=list_objects=forbidden
center.store_factory=lambda:ReadOnlyObjects()
def no_production_engine():raise AssertionError("PRODUCTION_ENGINE_FORBIDDEN")
center.read_engine=no_production_engine

for target in (data,empty):
    with target.connect() as conn:conn.execute(text("PRAGMA query_only=ON"))
    @event.listens_for(target,"before_cursor_execute")
    def select_only(conn,cursor,statement,*args):
        if not statement.lstrip().upper().startswith("SELECT"):
            state["sql_write_attempts"]+=1
            raise PermissionError("READ_ONLY_SQL")
        state["sql_reads"]+=1

def read_session():
    with Session(empty if state["mode"]=="empty" else data,expire_on_commit=False) as sql:
        yield sql
app.dependency_overrides[center.read_db]=read_session
app.dependency_overrides[db_module.get_db]=read_session

@app.middleware("http")
async def test_transport_only(request:Request,call_next):
    if request.method!="GET" and not request.url.path.startswith("/__beta_test__/"):
        from fastapi.responses import JSONResponse
        return JSONResponse({"detail":"TEST_GET_ONLY"},status_code=405)
    response=await call_next(request)
    response.headers["X-Beta-Isolation"]="synthetic-sqlite-memory-r2"
    return response

@app.get("/__beta_test__/status")
def status():
    intact=original=={key:hashlib.sha256(value[0]).hexdigest() for key,value in store._objects.items()}
    return {**state,"evidence":"ISOLATED_SYNTHETIC_ONLY","objects_unchanged":intact,
        "clock_utc":NOW.isoformat(),"SAFE_TO_DELETE":"NO","LIVE_DELETE":"NOT_RUN",
        "AUTO_RETENTION_DELETE_ENABLED":False,"OPERATIONAL_DELETE_ENABLED":False}

@app.post("/__beta_test__/scenario/{mode}")
def scenario(mode:str,request:Request):
    if request.headers.get("x-beta-test")!="synthetic-only" or mode not in {"empty","valid","missing","corrupt","timeout","partial"}:
        raise HTTPException(400,detail="INVALID_SYNTHETIC_SCENARIO")
    state["mode"]=mode
    return {"mode":mode}

if __name__=="__main__":
    import uvicorn
    port=int(os.environ.get("BETA_TEST_PORT","8106"))
    if not 1024<=port<=65535:raise RuntimeError("INVALID_LOCAL_TEST_PORT")
    uvicorn.run(app,host="127.0.0.1",port=port,access_log=False,log_level="warning")
