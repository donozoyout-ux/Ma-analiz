"""Synthetic signers and local DB only. No real delivery or source attestation."""
from test_prediction_ledger_postgres import db, TEST_URL  # also enforces isolation before app imports
import base64
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import time
from uuid import uuid4

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from sqlalchemy import select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from app.api.prediction_ledger import metrics
from app.jobs.prediction_ledger import publish, settle_evidence
from app.jobs.prediction_ledger_evidence import TrustPolicy, accept_evidence, canonical, publication_digest
from app.models import ActiveMatch, Prediction
from app.models.prediction_ledger import PredictionEvidence as E, PredictionSettlement as S


def candidate(db, seconds=120):
    now = datetime.now(timezone.utc)
    uid = uuid4()
    match = ActiveMatch(id=uid, provider="synthetic", provider_match_id=str(uid), league_id="isolated-evidence",
        season="2026-2027", home_team="Synthetic H", away_team="Synthetic A", start_time=now+timedelta(seconds=seconds),
        status="SCHEDULED", expires_at=now+timedelta(days=1))
    prediction = Prediction(id=uuid4(), match_id=uid, market="1X2", selection="HOME", probability=Decimal("0.5"),
        confidence=Decimal("0.5"), value_score=Decimal("0"), evidence_status="OK", model_version=str(uid),
        feature_version="v1", feature_snapshot="synthetic", analysis_timestamp=now, status="PENDING", expires_at=now+timedelta(days=1))
    db.add(match); db.flush(); db.add(prediction); db.commit()
    p = publish(db, prediction_id=prediction.id, idempotency_key=str(uid)); db.commit()
    return p, match


def envelope(p, kind="DELIVERY", prior=None, score=(2, 0), status="FINAL"):
    private = Ed25519PrivateKey.generate()
    policy = TrustPolicy("synthetic-independent-observer", "test-key", private.public_key().public_bytes_raw(), kind,
        (("test-a", "org-a", "synthetic-permission-a"), ("test-b", "org-b", "synthetic-permission-b")), "synthetic-public-channel")
    payload = {"schema":"ma-ledger-evidence/v1", "kind":kind, "event_id":str(uuid4()),
        "authority":policy.authority, "key_id":policy.key_id, "observed_at":datetime.now(timezone.utc).isoformat(),
        "supersedes_id":str(prior) if prior else None,
        "match":{key:str(getattr(p,key)) for key in ("match_id","provider","provider_match_id","league_id","season","home_team","away_team")}}
    payload["match"]["kickoff_at"] = p.kickoff_at.astimezone(timezone.utc).isoformat()
    if kind == "DELIVERY":
        payload.update(publication_id=str(p.id), publication_sha256=publication_digest(p),
                       audience=policy.audience, access="PUBLICLY_RETRIEVED")
    else:
        payload.update(status=status, period="REGULATION_90_PLUS_STOPPAGE", home_score=score[0], away_score=score[1], sources=[])
        for name, org, permission in policy.sources:
            payload["sources"].append(dict(name=name, permission_ref=permission, source_match_id="synthetic-id",
                artifact_ref="synthetic://offline-fixture", sha256="a"*64, status=status, period=payload["period"],
                home_score=score[0], away_score=score[1]))
    return payload, policy, private


def accept(db, p, payload, policy, private):
    signature = base64.b64encode(private.sign(canonical(payload))).decode()
    db.execute(text("SET LOCAL ROLE ma_ledger_verifier"))
    e = accept_evidence(db, publication_id=p.id, payload=payload, signature=signature, policy=policy, enabled=True)
    db.commit()
    return e


def stats(db, p):
    return metrics(start=None,end=None,tz="UTC",league="isolated-evidence",market=None,model=p.model_version,status=None,db=db)


def test_signed_delivery_retention_correction_and_replay(db):
    p, match = candidate(db, seconds=3)
    payload, policy, private = envelope(p)
    d = accept(db,p,payload,policy,private)
    assert accept(db,p,payload,policy,private).id == d.id
    assert stats(db,p)["hit_rate"] is None
    assert stats(db,p)["publication_delivery_verified"] is True
    assert stats(db,p)["verification_status"] == "RESULT_EVIDENCE_PENDING_OR_UNSCORED"
    time.sleep(3.2)
    db.delete(match); db.commit()  # remove all expiring sources BEFORE first result arrives
    payload, policy, private = envelope(p,"RESULT")
    e = accept(db,p,payload,policy,private)
    engine, pub_id, eid = db.get_bind(), p.id, e.id
    def submit(_):
        with Session(engine) as session:
            session.execute(text("SET LOCAL ROLE ma_ledger_writer"))
            event = settle_evidence(session, publication_id=pub_id, evidence_id=eid, idempotency_key=str(eid))
            event_id = event.id; session.commit(); return event_id
    with ThreadPoolExecutor(max_workers=2) as pool:
        ids = list(pool.map(submit, range(2)))
    assert ids[0] == ids[1]
    assert stats(db,p)["sample_size"] == 1 and stats(db,p)["hit_rate"] == 1.0
    payload, policy, private = envelope(p,"RESULT",prior=e.id,score=(0,1))
    corrected = accept(db,p,payload,policy,private)
    assert stats(db,p)["hit_rate"] is None  # old WON excluded while correction waits
    with pytest.raises(ValueError,match="SUPERSEDED_RESULT_EVIDENCE"):
        settle_evidence(db,publication_id=p.id,evidence_id=e.id,idempotency_key="stale-"+str(e.id))
    db.rollback()
    lost = settle_evidence(db,publication_id=p.id,evidence_id=corrected.id,idempotency_key=str(corrected.id)); db.commit()
    assert lost.outcome == "LOST" and lost.supersedes_id == ids[0]
    assert db.get(S,ids[0]).outcome == "WON" and stats(db,p)["hit_rate"] == 0.0
    payload, policy, private = envelope(p,"RESULT",prior=corrected.id,score=(None,None),status="RETRACTED")
    withdrawn = accept(db,p,payload,policy,private)
    unscored = settle_evidence(db,publication_id=p.id,evidence_id=withdrawn.id,idempotency_key=str(withdrawn.id));db.commit()
    assert unscored.outcome == "UNSCORED" and stats(db,p)["sample_size"] == 0
    payload, policy, private = envelope(p,"RESULT",prior=withdrawn.id,score=(None,None),status="CANCELLED")
    cancelled = accept(db,p,payload,policy,private)
    void = settle_evidence(db,publication_id=p.id,evidence_id=cancelled.id,idempotency_key=str(cancelled.id));db.commit()
    assert void.outcome == "VOID" and stats(db,p)["hit_rate"] is None


def test_signature_replay_and_default_disabled(db):
    p,_ = candidate(db)
    payload,policy,private = envelope(p)
    signature = base64.b64encode(private.sign(canonical(payload))).decode()
    with pytest.raises(ValueError,match="INTEGRATION_DISABLED"):
        accept_evidence(db,publication_id=p.id,payload=payload,signature=signature,policy=policy)
    payload["publication_sha256"] = "b"*64
    db.execute(text("SET LOCAL ROLE ma_ledger_verifier"))
    with pytest.raises(ValueError,match="INVALID_EVIDENCE_SIGNATURE"):
        accept_evidence(db,publication_id=p.id,payload=payload,signature=signature,policy=policy,enabled=True)
    with pytest.raises(ValueError,match="PUBLICATION_DELIVERY_UNVERIFIED"):
        accept(db,p,payload,policy,private)
    db.rollback()
    payload["publication_sha256"] = publication_digest(p)
    accept(db,p,payload,policy,private)
    payload["audience"] = "another-channel"
    with pytest.raises(ValueError,match="EVIDENCE_REPLAY_CONFLICT"):
        accept(db,p,payload,policy,private)


def test_late_delivery_never_counts(db):
    p,_ = candidate(db,seconds=1)
    payload,policy,private = envelope(p)
    time.sleep(1.1)
    with pytest.raises(ValueError,match="PUBLICATION_DELIVERY_UNVERIFIED"):
        accept(db,p,payload,policy,private)
    db.rollback()
    payload,policy,private = envelope(p,"RESULT")
    e = accept(db,p,payload,policy,private)
    settle_evidence(db,publication_id=p.id,evidence_id=e.id,idempotency_key=str(e.id));db.commit()
    assert stats(db,p)["sample_size"] == 0 and stats(db,p)["hit_rate"] is None


@pytest.mark.parametrize("failure",["extra-time","missing-permission","one-organization","disagree","future","wrong-match"])
def test_bad_result_attestations(db,failure):
    p,_=candidate(db,seconds=0.4);time.sleep(0.5)
    payload,policy,private=envelope(p,"RESULT")
    expected=""
    if failure=="extra-time": payload["period"]="AFTER_EXTRA_TIME";expected="RESULT_PERIOD_UNVERIFIED"
    if failure=="missing-permission": payload["sources"][0]["permission_ref"]="";expected="SOURCE_PERMISSION_UNVERIFIED"
    if failure=="one-organization": policy=replace(policy,sources=(("test-a","same","synthetic-permission-a"),("test-b","same","synthetic-permission-b")));expected="INDEPENDENT_RESULT_SOURCES_REQUIRED"
    if failure=="disagree": payload["sources"][1]["away_score"]=4;expected="INDEPENDENT_RESULT_DISAGREEMENT"
    if failure=="future": payload["observed_at"]=(datetime.now(timezone.utc)+timedelta(days=1)).isoformat();expected="FUTURE_EVIDENCE"
    if failure=="wrong-match": payload["match"]["match_id"]=str(uuid4());expected="EVIDENCE_MATCH_MISMATCH"
    with pytest.raises(ValueError,match=expected): accept(db,p,payload,policy,private)


def test_verifier_separation_schema_and_search_path(db):
    for role,statement in (("ma_ledger_writer","INSERT INTO prediction_evidence DEFAULT VALUES"),
            ("ma_ledger_verifier","INSERT INTO prediction_settlements DEFAULT VALUES"),
            ("ma_ledger_verifier","DELETE FROM prediction_evidence"),
            ("ma_ledger_reader","CREATE TABLE public.ledger_attack(id int)")):
        with pytest.raises(DBAPIError):
            with db.begin_nested():
                db.execute(text(f"SET LOCAL ROLE {role}"));db.execute(text(statement))
    functions=db.execute(text("SELECT p.proname,p.proconfig,r.rolname FROM pg_proc p JOIN pg_roles r ON r.oid=p.proowner WHERE p.proname IN ('prediction_evidence_guard','prediction_settlement_evidence_guard','prediction_publication_clock')")).all()
    assert len(functions)==3
    assert all("search_path=pg_catalog, public, pg_temp" in config and owner not in ("ma_ledger_writer","ma_ledger_reader","ma_ledger_verifier") for _,config,owner in functions)
    assert db.scalar(text("SELECT has_function_privilege('ma_ledger_writer','prediction_evidence_guard()','EXECUTE')")) is False


def test_delivery_commit_after_kickoff_rejected(db):
    p,_=candidate(db,seconds=1)
    payload,policy,private=envelope(p)
    db.execute(text("SET LOCAL ROLE ma_ledger_verifier"))
    accept_evidence(db,publication_id=p.id,payload=payload,policy=policy,enabled=True,
        signature=base64.b64encode(private.sign(canonical(payload))).decode())
    time.sleep(1.1)
    with pytest.raises(DBAPIError,match="delivery reached kickoff"): db.commit()
    db.rollback()
    assert db.scalar(select(E.id).where(E.publication_id==p.id)) is None


def test_privileged_runtime_rejected_and_concurrent_receipt(db):
    from app.jobs.prediction_ledger_evidence import require_runtime_role, prepare_publication_for_delivery
    with pytest.raises(ValueError,match="PUBLICATION_INTEGRATION_DISABLED"):
        prepare_publication_for_delivery(db,prediction_id=uuid4(),idempotency_key="disabled")
    with pytest.raises(ValueError,match="UNSAFE_LEDGER_RUNTIME_ROLE"):
        require_runtime_role(db,"ma_ledger_verifier")
    p,_=candidate(db)
    payload,policy,private=envelope(p)
    signature=base64.b64encode(private.sign(canonical(payload))).decode()
    engine,pid=db.get_bind(),p.id
    def submit(_):
        with Session(engine) as session:
            session.execute(text("SET LOCAL ROLE ma_ledger_verifier"))
            evidence=accept_evidence(session,publication_id=pid,payload=payload,signature=signature,policy=policy,enabled=True)
            eid=evidence.id;session.commit();return eid
    with ThreadPoolExecutor(max_workers=2) as pool:
        ids=list(pool.map(submit,range(2)))
    assert ids[0]==ids[1]


def test_cursor_survives_insert_and_rejects_malformed_input(db):
    from app.api.prediction_ledger import ledger
    from fastapi import HTTPException
    p1,_=candidate(db);p2,_=candidate(db)
    kwargs=dict(start=None,end=None,tz="UTC",league="isolated-evidence",market=None,model=None,status=None,limit=1,offset=0,db=db)
    first=ledger(**kwargs)
    assert first["items"][0]["id"]==p2.id and first["next_cursor"]
    p3,_=candidate(db)
    second=ledger(**kwargs,cursor=first["next_cursor"])
    assert second["items"][0]["id"]==p1.id
    with pytest.raises(HTTPException): ledger(**kwargs,cursor="not-a-cursor")
