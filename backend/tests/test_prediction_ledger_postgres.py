"""Runs only against an explicitly named isolated loopback PostgreSQL instance."""
from datetime import date, datetime, timedelta, timezone
from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal
import os
import time
import hashlib
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, func, select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session
from fastapi import FastAPI
from fastapi.testclient import TestClient

TEST_URL = "postgresql+psycopg://ledger_test@127.0.0.1:55471/ledger71"
if os.environ.get("LEDGER_TEST_DATABASE_URL") != TEST_URL or (Path(__file__).resolve().parents[2] / ".env").exists():
    pytest.skip("isolated loopback database and project without .env required", allow_module_level=True)

from app.api.prediction_ledger import ledger, ledger_query, metrics, router
from app.db.session import get_db
from app.jobs.prediction_ledger import publish, settle_final
from app.models import ActiveMatch, MatchResult, Prediction
from app.models.prediction_ledger import PredictionPublication, PredictionSettlement

@pytest.fixture
def db():
    if os.environ.get("LEDGER_TEST_DATABASE_URL") != TEST_URL:
        pytest.skip("requires explicitly configured isolated test database")
    engine = create_engine(TEST_URL)
    with Session(engine, expire_on_commit=False) as session:
        yield session
        session.rollback()
    engine.dispose()


def test_legacy_results_do_not_prove_regulation_time(db):
    with pytest.raises(ValueError, match="VERIFIED_REGULATION_EVIDENCE_REQUIRED"):
        settle_final(db, publication_id=uuid4(), result_id=uuid4(), idempotency_key="legacy-result")


def test_roles_and_database_append_only(db):
    for role, statement in (("ma_ledger_reader", "INSERT INTO prediction_publications DEFAULT VALUES"),
                            ("ma_ledger_writer", "DELETE FROM prediction_publications"),
                            ("ma_ledger_writer", "UPDATE prediction_settlements SET outcome='WON'"),
                            ("ma_ledger_writer", "TRUNCATE prediction_settlements")):
        with pytest.raises(DBAPIError):
            with db.begin_nested():
                db.execute(text(f"SET LOCAL ROLE {role}"))
                db.execute(text(statement))
    # The table owner is still blocked by the trigger for ordinary DML.
    with pytest.raises(DBAPIError):
        with db.begin_nested():
            db.execute(text("DELETE FROM prediction_publications"))


def test_empty_filter_has_null_accuracy(db):
    data = metrics(start=None,end=None,tz="UTC",league="nonexistent-synthetic-league",market=None,model=None,status=None,db=db)
    assert data["total"] == 0 and data["sample_size"] == 0 and data["hit_rate"] is None


def test_istanbul_date_window_uses_utc_exclusive_boundary():
    stmt = ledger_query(start=date(2026, 11, 28), end=date(2026, 11, 28), tz="Europe/Istanbul",
                        league=None, market=None, model=None, status=None)
    values = list(stmt.compile().params.values())
    assert datetime(2026, 11, 27, 21, tzinfo=timezone.utc) in values
    assert datetime(2026, 11, 28, 21, tzinfo=timezone.utc) in values


def test_read_only_http_contract(db):
    app = FastAPI()
    app.include_router(router, prefix="/api/v1")
    app.dependency_overrides[get_db] = lambda: db
    with TestClient(app) as client:
        response = client.get("/api/v1/prediction-ledger", params={"league": "nonexistent-synthetic-league", "limit": 1, "tz": "Europe/Istanbul"})
        assert response.status_code == 200 and response.json()["items"] == []
        assert response.json()["legacy_included"] is False and response.json()["publication_delivery_verified"] is False
        result = client.get("/api/v1/prediction-ledger/metrics", params={"league": "nonexistent-synthetic-league"})
        assert result.status_code == 200 and result.json()["hit_rate"] is None
        assert client.get("/api/v1/prediction-ledger", params={"tz": "INVALID"}).status_code == 422
        assert client.post("/api/v1/prediction-ledger").status_code == 405


def test_concurrent_publication_and_failed_transaction(db):
    now = datetime.now(timezone.utc)
    uid = uuid4()
    match = ActiveMatch(id=uid, provider="test", provider_match_id=str(uid), league_id="de.bl1",
        season="2026-2027", home_team="Synthetic H", away_team="Synthetic A",
        start_time=now + timedelta(minutes=2), status="SCHEDULED", expires_at=now + timedelta(days=1))
    prediction = Prediction(id=uuid4(), match_id=uid, market="1X2", selection="HOME", probability=Decimal("0.5"),
        confidence=Decimal("0.5"), value_score=Decimal("0"), evidence_status="OK", model_version="race-test",
        feature_version="v1", feature_snapshot="synthetic", analysis_timestamp=now, status="PENDING", expires_at=now+timedelta(days=1))
    db.add(match); db.flush(); db.add(prediction); db.commit()
    with pytest.raises(DBAPIError):
        with db.begin_nested():
            db.execute(text("SET LOCAL ROLE ma_ledger_writer"))
            db.execute(text("""INSERT INTO prediction_publications
                (id,idempotency_key,prediction_id,match_id,provider,provider_match_id,league_id,season,home_team,away_team,kickoff_at,
                 market,selection,probability,model_version,feature_version,feature_sha256,publication_version)
                VALUES (:id,:key,:prediction,:match,'test',:native,'de.bl1','2026-2027','FORGED','Synthetic A',:kickoff,
                 '1X2','HOME',0.5,'race-test','v1',:hash,1)"""),
                {"id":uuid4(),"key":f"forged-pub-{uid}","prediction":prediction.id,"match":uid,"native":str(uid),
                 "kickoff":match.start_time,"hash":"a"*64})
    db.rollback()
    engine = db.get_bind()
    prediction_id = prediction.id
    def submit():
        with Session(engine) as session:
            session.execute(text("SET LOCAL ROLE ma_ledger_writer"))
            item = publish(session, prediction_id=prediction_id, idempotency_key=f"race-{uid}")
            answer = item.id
            session.commit()
            return answer
    with ThreadPoolExecutor(max_workers=2) as pool:
        ids = list(pool.map(lambda _: submit(), range(2)))
    assert ids[0] == ids[1]
    assert db.scalar(select(func.count()).select_from(PredictionPublication).where(PredictionPublication.prediction_id == prediction.id)) == 1
    with pytest.raises(ValueError, match="IDEMPOTENCY_CONFLICT"):
        publish(db, prediction_id=uuid4(), idempotency_key=f"race-{uid}")
    revision = Prediction(id=uuid4(), match_id=uid, market="1X2", selection="HOME", probability=Decimal("0.6"),
        confidence=Decimal("0.6"), value_score=Decimal("0"), evidence_status="OK", model_version="race-test-v2",
        feature_version="v1", feature_snapshot="synthetic-revision", analysis_timestamp=now, status="PENDING", expires_at=now+timedelta(days=1))
    db.add(revision); db.commit()
    revised = publish(db, prediction_id=revision.id, idempotency_key=f"revision-{uid}", publication_version=2, supersedes_id=ids[0])
    db.commit()
    assert revised.supersedes_id == ids[0]
    data = ledger(start=None,end=None,tz="UTC",league="de.bl1",market="1X2",model=None,status="PENDING",limit=100,offset=0,db=db)
    ids_list = [item["id"] for item in data["items"]]
    assert revised.id in ids_list and ids[0] not in ids_list
    other = Prediction(id=uuid4(), match_id=uid, market="1X2", selection="AWAY", probability=Decimal("0.3"),
        confidence=Decimal("0.3"), value_score=Decimal("0"), evidence_status="OK", model_version="race-test",
        feature_version="v1", feature_snapshot="synthetic-other", analysis_timestamp=now, status="PENDING", expires_at=now+timedelta(days=1))
    db.add(other); db.commit()
    other_id, match_start = other.id, match.start_time
    db.execute(text("SET LOCAL ROLE ma_ledger_writer"))
    written = db.execute(text("""INSERT INTO prediction_publications
        (id,idempotency_key,prediction_id,match_id,provider,provider_match_id,league_id,season,home_team,away_team,kickoff_at,
         published_at,market,selection,probability,model_version,feature_version,feature_sha256,publication_version)
        VALUES (:id,:key,:prediction,:match,'test',:native,'de.bl1','2026-2027','Synthetic H','Synthetic A',:kickoff,
         :fake_time,'1X2','AWAY',0.3,'race-test','v1',:hash,1) RETURNING published_at"""),
        {"id":uuid4(),"key":f"other-{uid}","prediction":other_id,"match":uid,"native":str(uid),"kickoff":match_start,
         "fake_time":datetime(2000,1,1,tzinfo=timezone.utc),"hash":hashlib.sha256(b"synthetic-other").hexdigest()}).scalar_one()
    db.commit()
    assert written >= now
    assert publish(db, prediction_id=other_id, idempotency_key=f"other-{uid}").published_at == written
    db.commit()
    first_page = ledger(start=None,end=None,tz="UTC",league="de.bl1",market="1X2",model=None,status=None,limit=1,offset=0,db=db)
    second_page = ledger(start=None,end=None,tz="UTC",league="de.bl1",market="1X2",model=None,status=None,limit=1,offset=1,db=db)
    assert first_page["next_offset"] == 1 and first_page["items"][0]["id"] != second_page["items"][0]["id"]


def test_kickoff_before_commit_is_rejected(db):
    now = datetime.now(timezone.utc)
    uid = uuid4()
    match = ActiveMatch(id=uid, provider="test", provider_match_id=str(uid), league_id="de.bl1",
        season="2026-2027", home_team="Synthetic H", away_team="Synthetic A",
        start_time=now + timedelta(seconds=2), status="SCHEDULED", expires_at=now + timedelta(days=1))
    prediction = Prediction(id=uuid4(), match_id=uid, market="1X2", selection="HOME", probability=Decimal("0.5"),
        confidence=Decimal("0.5"), value_score=Decimal("0"), evidence_status="OK", model_version="late-test",
        feature_version="v1", feature_snapshot="synthetic", analysis_timestamp=now, status="PENDING", expires_at=now+timedelta(days=1))
    db.add(match); db.flush(); db.add(prediction); db.commit()
    key = f"late-{uid}"
    publish(db, prediction_id=prediction.id, idempotency_key=key)
    time.sleep(2.2)
    with pytest.raises(DBAPIError):
        db.commit()
    db.rollback()
    assert db.scalar(select(func.count()).select_from(PredictionPublication).where(PredictionPublication.idempotency_key == key)) == 0
