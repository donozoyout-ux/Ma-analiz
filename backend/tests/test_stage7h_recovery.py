"""Real pg_dump/pg_restore only inside the verified PR PostgreSQL 16 service."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import hashlib
import json
import os
from pathlib import Path
from uuid import uuid4

from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
import pytest
from sqlalchemy import insert, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.api import match_center as api
from app.db.base import Base
from app.models import (ActiveMatch, ArchiveJob, Coupon, CouponItem, JobRun, MatchAnalysis, MatchResult,
                        Prediction, SystemSetting, User)
from app.storage import lifecycle as lc
from app.storage.archive import _verify_object
from app.storage.r2 import InMemoryObjectStore, ObjectInfo, parquet_bytes
from recovery_support import RecoveryFailure, RecoveryHarness, canonical, digest, snapshot

pytestmark=pytest.mark.skipif(os.environ.get("STAGE7H_RECOVERY_ENABLED")!="true",reason="Isolated PostgreSQL recovery opt-in absent")
CHECKS={"roundtrip","foreign_keys","match_center","archive_missing","archive_corrupt","backup_missing",
        "backup_corrupt","schema_only","checksum_mismatch","nonempty_target","repeat_empty_target"}


class ReadOnlyObjects:
    def __init__(self, objects): self.objects=deepcopy(objects)
    def head_object(self,key):
        item=self.objects.get(key)
        return ObjectInfo(key,len(item[0]),metadata=dict(item[2])) if item else None
    def get_object(self,key): return self.objects[key][0]
    def put_object(self,*a,**kw): raise AssertionError("READBACK_ONLY")
    put_object_if_absent=put_object
    delete_object=put_object
    list_objects=put_object


def seed(engine,now):
    """No importer, migration, archive job, cleanup runner or external object SDK."""
    Base.metadata.create_all(engine)
    store=InMemoryObjectStore()
    with Session(engine,expire_on_commit=False) as sql:
        matches=[];children={}
        for kind in ("active","history"):
            match=ActiveMatch(provider="openligadb",provider_match_id="SYNTHETIC-RECOVERY-"+kind,
                league_id="de.bl1",league_name="Synthetic recovery league",season="2026-2027",
                home_team="SYNTHETIC Home "+kind,away_team="SYNTHETIC Away "+kind,status="FINISHED",
                start_time=now-timedelta(hours=60),expires_at=now+timedelta(hours=48),last_updated_at=now-timedelta(hours=57))
            sql.add(match);sql.flush()
            result=MatchResult(match_id=match.id,provider="openligadb",provider_result_id="SYNTHETIC-RESULT-"+kind,
                status="FINAL",home_score=2,away_score=1,result_source="SYNTHETIC RECOVERY FIXTURE",
                finished_at=now-timedelta(hours=58),verified_at=now-timedelta(hours=57),updated_at=now-timedelta(hours=57))
            analysis=MatchAnalysis(match_id=match.id,analysis_timestamp=now-timedelta(hours=59),analysis_status="ANALYZED",
                final_decision="NO BET",model_version="synthetic-recovery-1",feature_version="synthetic-1",
                analysis_output='{"synthetic":true}',created_at=now-timedelta(hours=59))
            sql.add_all([result,analysis]);sql.flush();matches.append(match);children[match.id]=[result,analysis]
        active,historical=matches
        prediction=Prediction(match_id=active.id,market="1X2",selection="HOME",probability=Decimal("0.6"),
            confidence=Decimal("0.5"),value_score=Decimal("0.1"),evidence_status="SYNTHETIC",model_version="synthetic-1",
            analysis_timestamp=now-timedelta(hours=59),status="WON",settled_at=now-timedelta(hours=57),
            created_at=now-timedelta(hours=59),expires_at=now+timedelta(hours=48))
        coupon=Coupon(type="SYNTHETIC",total_odds=Decimal("2.25"),confidence=Decimal("0.5"),status="PAPER_WON",
            settled_at=now-timedelta(hours=57),created_at=now-timedelta(hours=59),expires_at=now+timedelta(hours=48))
        sql.add_all([prediction,coupon,User(created_at=now-timedelta(days=1)),
            SystemSetting(key="review-audit:synthetic-recovery",value={"synthetic":True,"version":1}),
            JobRun(idempotency_key="synthetic-recovery-audit",job_type="synthetic_fixture",status="SUCCESS",
                   started_at=now-timedelta(hours=59),finished_at=now-timedelta(hours=57))]);sql.flush()
        item=CouponItem(coupon_id=coupon.id,match_id=active.id,prediction_id=prediction.id,market="1X2",selection="HOME",
            odds=Decimal("2.25"),status="WON",settled_at=now-timedelta(hours=57))
        sql.add(item);sql.commit();children[active.id]+=[prediction,coupon,item]
        expected={}
        for match in matches:
            for entity in [match,*children[match.id]]:
                record=lc.make_record(sql,match,entity,now=now,hours=48)
                assert not record.blockers
                payload=parquet_bytes([record.row]);assert hashlib.sha256(payload).hexdigest()==record.checksum
                # Synthetic Parquet bytes only, in memory; never a real R2 client.
                store.put_object_if_absent(record.key,payload,content_type="application/vnd.apache.parquet",
                    metadata={"sha256":record.checksum,"row-count":"1","archive-key":record.key})
                expected[record.key]=canonical(record.row)
                sql.add(ArchiveJob(archive_date=record.day,dataset_type=record.dataset,league=record.league,
                    r2_key=record.key,row_count=1,file_size_bytes=len(payload),checksum=record.checksum,status="VERIFIED",verified_at=now))
                if entity is match:
                    sql.add(SystemSetting(key=lc.source_index_key(match),value={"provider":match.provider,
                        "provider_match_id":match.provider_match_id,"match_id":str(match.id),"season":match.season,
                        "league_id":match.league_id,"first_verified_fixture_key":record.key}))
        sql.commit()
        ids={"active":active.id,"history":historical.id}
        # Model an archive-only fixture using SQL only in this disposable source DB.
        # No retention/cleanup function is invoked.
        sql.delete(historical);sql.commit()
    return ReadOnlyObjects(store._objects),expected,ids


@pytest.fixture(scope="module")
def recovery(tmp_path_factory):
    h=RecoveryHarness(os.environ,tmp_path_factory.mktemp("stage7h2"))
    try:
        h.verify_container()
        h.source_name,h.source=h.database("source")
        h.now=datetime.now(timezone.utc).replace(microsecond=0)
        h.store,h.archive_rows,h.ids=seed(h.source,h.now)
        h.expected=snapshot(h.source)
        h.backup=h.directory/"synthetic.dump";h.backup.write_bytes(h.dump(h.source_name))
        h.backup_sha=hashlib.sha256(h.backup.read_bytes()).hexdigest()
        h.dest_name,h.dest=h.database("restored");h.empty(h.dest_name,h.dest)
        h.receipt=h.restore(h.backup,h.backup_sha,h.dest_name,h.dest,h.expected)
        h.report.update(backup_sha256=h.backup_sha,backup_bytes=h.receipt["bytes"],
            tables=h.expected["tables"],columns_sha256=h.expected["columns_sha256"],
            constraints_sha256=digest(h.expected["constraints"]),indexes_sha256=h.expected["indexes_sha256"],
            synthetic_object_count=len(h.archive_rows))
        yield h
    finally:
        try:
            h.close();h.report["test_database_cleanup"]="PASS"
        except Exception:
            h.report["test_database_cleanup"]="FAILED"
            raise
        finally:
            h.report["status"]="PASS" if set(h.report["checks"])==CHECKS and h.report.get("test_database_cleanup")=="PASS" else "FAILED"
            if path:=os.environ.get("RECOVERY_REPORT_PATH"):
                Path(path).write_text(json.dumps(h.report,indent=2)+"\n")


def passed(h,code,**details): h.report["checks"][code]={"status":"PASS",**details}


def test_complete_pg_dump_restore_preserves_every_table_row_uuid_and_archive(recovery):
    h=recovery
    assert snapshot(h.dest)==h.expected
    assert h.expected["tables"]["active_matches"]["count"]==h.expected["tables"]["match_results"]["count"]==1
    with Session(h.dest) as sql:
        assert sql.get(ActiveMatch,h.ids["history"]) is None
        assert sql.get(ActiveMatch,h.ids["active"]).id==h.ids["active"]
        ledgers=sql.scalars(select(ArchiveJob)).all();assert len(ledgers)==len(h.archive_rows)==9
        for ledger in ledgers:
            rows=_verify_object(h.store,ledger.r2_key,expected_size=ledger.file_size_bytes,
                expected_rows=ledger.row_count,expected_checksum=ledger.checksum)
            assert canonical(rows[0])==h.archive_rows[ledger.r2_key]
    passed(h,"roundtrip",all_tables_rows_uuids="PASS",ledger_sha256_row_count_content="PASS",archive_objects=9)


def test_restored_foreign_keys_are_present_validated_and_enforced(recovery):
    h=recovery;fks=[r for r in h.expected["constraints"] if r["contype"]=="f"]
    assert fks and all(r["convalidated"] for r in fks)
    with Session(h.dest) as sql:
        for statement in (update(MatchResult).values(match_id=uuid4()),update(CouponItem).values(coupon_id=uuid4()),
                          update(CouponItem).values(prediction_id=uuid4())):
            with pytest.raises(IntegrityError) as error:
                with sql.begin_nested(): sql.execute(statement)
            assert error.value.orig.sqlstate=="23503"
        sql.rollback()
    assert snapshot(h.dest)==h.expected
    passed(h,"foreign_keys",constraint_count=len(fks),rejected_orphan_updates=3)


def test_restored_match_center_active_history_list_and_detail_http(recovery,monkeypatch):
    h=recovery
    def db():
        from sqlalchemy import text
        with h.dest.connect() as conn:
            conn.execute(text("SET TRANSACTION READ ONLY"))
            with Session(bind=conn) as sql: yield sql
            conn.rollback()
    app=FastAPI();app.include_router(api.router,prefix="/api/v1");app.dependency_overrides[api.read_db]=db
    monkeypatch.setattr(api,"store_factory",lambda:h.store)
    monkeypatch.setattr(api,"read_engine",lambda:pytest.fail("Production engine forbidden"))
    with TestClient(app) as client:
        for source,count in (("active",1),("history",2),("all",2)):
            response=client.get("/api/v1/match-center",params={"source":source})
            assert response.status_code==200 and len(response.json()["items"])==count
            assert response.headers["cache-control"]=="no-store"
        for kind in ("active","history"):
            response=client.get(f"/api/v1/match-center/{h.ids[kind]}",params={"source":"history" if kind=="history" else "all"})
            assert response.status_code==200
            row=response.json();assert row["id"]==str(h.ids[kind]) and row["result"]["home_score"]==2 and row["result"]["away_score"]==1
            assert row["storage"]==("R2" if kind=="history" else "POSTGRESQL")
            assert row["analysis_summary"]["model_version"]=="synthetic-recovery-1"
    assert snapshot(h.dest)==h.expected
    passed(h,"match_center",http_lists=3,http_details=2,history_without_operational_sql="PASS")


@pytest.mark.parametrize("fault",["missing","corrupt"])
def test_restored_ledger_cannot_hide_missing_or_corrupt_archive(recovery,fault):
    h=recovery;damaged=ReadOnlyObjects(h.store.objects)
    with Session(h.dest) as sql:
        key=sql.get(SystemSetting,"source-identity:"+hashlib.sha256(lc.json_bytes(["openligadb","SYNTHETIC-RECOVERY-history"])).hexdigest()).value["first_verified_fixture_key"]
        if fault=="missing": damaged.objects.pop(key)
        else:
            body,mime,meta=damaged.objects[key];damaged.objects[key]=(b"corrupt",mime,meta)
        with pytest.raises(HTTPException) as error: api.detail_view(sql,h.ids["history"],source="history",now=h.now,store=damaged)
        assert error.value.status_code==503
    passed(h,"archive_"+fault,readback_status="REJECTED")


def test_missing_backup_fails_and_does_not_create_schema(recovery):
    h=recovery;name,engine=h.database("missing")
    with pytest.raises(RecoveryFailure,match="^BACKUP_MISSING$"): h.restore(h.directory/"missing.dump",h.backup_sha,name,engine,h.expected)
    result=h.raw_restore(name,engine,missing_file=True);assert result.returncode!=0
    h.empty(name,engine);passed(h,"backup_missing",restore_status="FAILED",target_empty=True)


def test_truncated_dump_pg_restore_fails_atomically(recovery):
    h=recovery;name,engine=h.database("corrupt")
    result=h.raw_restore(name,engine,payload=h.backup.read_bytes()[:h.receipt["bytes"]//2]);assert result.returncode!=0
    h.empty(name,engine);passed(h,"backup_corrupt",restore_status="FAILED",target_empty=True)


def test_valid_schema_only_dump_is_not_a_successful_data_recovery(recovery):
    h=recovery;name,engine=h.database("partial")
    path=h.directory/"schema_only.dump";payload=h.dump(h.source_name,schema_only=True);path.write_bytes(payload)
    with pytest.raises(RecoveryFailure,match="^RESTORE_CONTENT_MISMATCH$"):
        h.restore(path,hashlib.sha256(payload).hexdigest(),name,engine,h.expected)
    passed(h,"schema_only",restore_status="FAILED",tool_exit_zero_is_insufficient=True)


def test_backup_checksum_mismatch_blocks_before_restore(recovery):
    h=recovery;name,engine=h.database("checksum")
    path=h.directory/"altered.dump";path.write_bytes(h.backup.read_bytes()+b"changed")
    with pytest.raises(RecoveryFailure,match="^BACKUP_CHECKSUM_MISMATCH$"): h.restore(path,h.backup_sha,name,engine,h.expected)
    h.empty(name,engine);passed(h,"checksum_mismatch",restore_status="FAILED",target_empty=True)


def test_repeat_restore_into_nonempty_target_is_rejected_without_overwrite(recovery):
    h=recovery
    with pytest.raises(RecoveryFailure,match="^RESTORE_TARGET_NOT_EMPTY$"): h.restore(h.backup,h.backup_sha,h.dest_name,h.dest,h.expected)
    assert snapshot(h.dest)==h.expected;passed(h,"nonempty_target",restore_status="REJECTED",existing_data_unchanged=True)


def test_same_backup_restores_again_into_another_empty_database(recovery):
    h=recovery;name,engine=h.database("repeat")
    assert h.restore(h.backup,h.backup_sha,name,engine,h.expected)["status"]=="PASS"
    assert snapshot(h.source)==h.expected==snapshot(h.dest)==snapshot(engine)
    passed(h,"repeat_empty_target",restore_status="PASS",source_and_first_restore_unchanged=True)
