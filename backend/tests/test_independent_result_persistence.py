"""Independent FINAL-score SQL write requires complete R2 proof and explicit approval."""
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.jobs.independent_result_persistence import (
    VerificationBlocked, verify_bundesliga_result,
)
from app.models import ActiveMatch, ArchiveJob, MatchResult
from app.storage.r2 import InMemoryObjectStore

NOW = datetime(2026, 11, 25, 18, 0, tzinfo=timezone.utc)
KICKOFF = datetime(2026, 8, 28, 18, 30, tzinfo=timezone.utc)


def evidence(*, score=(5, 1), kickoff=KICKOFF, changed=False):
    observed = NOW.isoformat()
    base = {"league": "de.bl1", "home_team": "FC Bayern München",
            "away_team": "VfB Stuttgart", "start_time": kickoff.isoformat(),
            "final_confirmed": True, "reported_score": list(score), "observed_at": observed}
    primary = {**base, "provider": "openligadb", "source_match_id": "83156",
               "source_url": "https://api.openligadb.de/getmatchdata/bl1/2026/1"}
    secondary = {**base, "provider": "football-data.org", "source_match_id": "565776",
                 "source_status": "FINISHED",
                 "source_url": "https://api.football-data.org/v4/competitions/BL1/matches"}
    if changed:
        secondary["home_team"] = "Bayern Munich"
    return [primary], [secondary]


@pytest.fixture
def sql():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False},
                           poolclass=StaticPool)
    for model in (ActiveMatch, MatchResult, ArchiveJob):
        model.__table__.create(engine)
    with Session(engine, expire_on_commit=False) as db:
        match = ActiveMatch(id=uuid4(), provider="openligadb", provider_match_id="83156",
            league_id="de.bl1", season="2026-2027", league_name="Bundesliga",
            home_team="FC Bayern München", away_team="VfB Stuttgart",
            start_time=KICKOFF, status="FINISHED", expires_at=KICKOFF+timedelta(hours=32))
        db.add(match)
        db.flush()
        db.add(MatchResult(match_id=match.id, provider="openligadb",
            home_score=5, away_score=1, result_source="OpenLigaDB", status="FINAL",
            verified_at=None))
        db.commit()
        yield db, match.id
    engine.dispose()


def saved(db, uid):
    return db.scalar(select(MatchResult).where(MatchResult.match_id == uid))


def test_dry_run_is_read_only(sql):
    db, uid = sql
    p, s = evidence()
    outcome = verify_bundesliga_result(db, None, p, s, now=NOW)
    assert outcome["status"] == "DRY_RUN_MATCHED"
    assert saved(db, uid).verified_at is None
    assert not db.scalars(select(ArchiveJob)).all()


def test_explicit_live_approval_requires_r2(sql):
    db, uid = sql
    p, s = evidence()
    with pytest.raises(VerificationBlocked, match="LIVE_VERIFICATION_NEEDS"):
        verify_bundesliga_result(db, None, p, s, now=NOW, dry_run=False, live_approved=True)
    assert saved(db, uid).verified_at is None


def test_verified_proof_written_and_sql_persisted_atomically(sql):
    db, uid = sql
    store = InMemoryObjectStore()
    p, s = evidence()
    out = verify_bundesliga_result(db, store, p, s, now=NOW,
                                   dry_run=False, live_approved=True)
    assert out["status"] == "VERIFIED_SQL_UPDATED"
    assert saved(db, uid).verified_at is not None
    ledger = db.scalar(select(ArchiveJob))
    assert ledger.status == "VERIFIED" and ledger.row_count == 1
    assert store.head_object(out["evidence_key"]).size == ledger.file_size_bytes
    assert out["lifecycle_archive"] == "NOT_YET_ARCHIVED"
    retry = verify_bundesliga_result(db, store, p, s, now=NOW,
                                    dry_run=False, live_approved=True)
    assert retry["status"] == "ALREADY_VERIFIED_NO_WRITE"


def test_mismatched_score_is_never_saved(sql):
    db, uid = sql
    p, s = evidence()
    s[0]["reported_score"] = [4, 1]
    with pytest.raises(VerificationBlocked, match="SOURCE_IDENTITY_OR_SCORE"):
        verify_bundesliga_result(db, InMemoryObjectStore(), p, s, now=NOW,
                                dry_run=False, live_approved=True)
    assert saved(db, uid).verified_at is None


def test_mismatched_team_or_kickoff_is_never_saved(sql):
    db, uid = sql
    p, s = evidence(changed=True)
    with pytest.raises(VerificationBlocked):
        verify_bundesliga_result(db, InMemoryObjectStore(), p, s, now=NOW,
                                dry_run=False, live_approved=True)
    p, s = evidence(kickoff=KICKOFF+timedelta(minutes=5))
    with pytest.raises(VerificationBlocked, match="SQL_FIXTURE_TIME_CONFLICT"):
        verify_bundesliga_result(db, InMemoryObjectStore(), p, s, now=NOW,
                                dry_run=False, live_approved=True)
    assert saved(db, uid).verified_at is None


def test_stale_observation_is_rejected(sql):
    db, uid = sql
    p, s = evidence()
    s[0]["observed_at"] = (NOW-timedelta(hours=2)).isoformat()
    with pytest.raises(VerificationBlocked, match="SOURCE_EVIDENCE_STALE"):
        verify_bundesliga_result(db, InMemoryObjectStore(), p, s, now=NOW,
                                dry_run=False, live_approved=True)
    assert saved(db, uid).verified_at is None


def test_archive_failure_cannot_write_sql(sql):
    db, uid = sql
    class BrokenStore(InMemoryObjectStore):
        def get_object(self, key):
            raise IOError("R2_READBACK_FAILED")
    p, s = evidence()
    with pytest.raises(RuntimeError):
        verify_bundesliga_result(db, BrokenStore(), p, s, now=NOW,
                                 dry_run=False, live_approved=True)
    db.rollback()
    assert saved(db, uid).verified_at is None
    assert not db.scalars(select(ArchiveJob)).all()


def test_sql_score_mismatch_is_rejected(sql):
    db, uid = sql
    saved(db, uid).home_score = 4
    db.commit()
    p, s = evidence()
    with pytest.raises(VerificationBlocked, match="SQL_FINAL_RESULT_CONFLICT"):
        verify_bundesliga_result(db, InMemoryObjectStore(), p, s, now=NOW,
                                 dry_run=False, live_approved=True)
    assert saved(db, uid).verified_at is None
