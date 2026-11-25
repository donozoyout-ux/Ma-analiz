"""Isolated SQL/R2 lifecycle regression for verified Bayern-Stuttgart, no deletion."""
from datetime import datetime, timedelta, timezone
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool
import pytest

from app.jobs.verified_lifecycle_archive import (
    MATCH_ID, LifecycleBlocked, run,
)
from app.models import ActiveMatch, MatchResult, ArchiveJob, SystemSetting
from app.storage.r2 import InMemoryObjectStore

NOW = datetime(2026, 11, 26, 0, tzinfo=timezone.utc)
KICKOFF = datetime(2026, 8, 28, 18, 30, tzinfo=timezone.utc)


@pytest.fixture
def session():
    engine = create_engine("sqlite://", connect_args={"check_same_thread":False}, poolclass=StaticPool)
    for entity in (ActiveMatch, MatchResult, ArchiveJob, SystemSetting):
        entity.__table__.create(engine)
    with Session(engine, expire_on_commit=False) as db:
        db.add(ActiveMatch(id=MATCH_ID, provider="openligadb",
            provider_match_id="83156", league_id="de.bl1", league_name="Bundesliga",
            season="2026-2027", home_team="FC Bayern München",
            away_team="VfB Stuttgart", start_time=KICKOFF, status="FINISHED",
            expires_at=KICKOFF+timedelta(hours=32)))
        db.flush()
        db.add(MatchResult(match_id=MATCH_ID, provider="openligadb",
            home_score=5, away_score=1, status="FINAL", result_source="OpenLigaDB",
            verified_at=NOW-timedelta(hours=1)))
        db.commit()
        yield db
    engine.dispose()


def test_dry_run_prepares_exact_archive_parts_without_writes(session):
    report = run(session, now=NOW)
    assert report["status"] == "DRY_RUN_READY"
    assert {r["dataset"] for r in report["records"]} == {"matches", "results"}
    assert not session.scalars(select(ArchiveJob)).all()
    assert not session.scalars(select(SystemSetting)).all()
    assert report["sql_delete"] is False


def test_requires_explicit_live_approval(session):
    with pytest.raises(LifecycleBlocked, match="EXPLICIT_ARCHIVE_APPROVAL"):
        run(session, InMemoryObjectStore(), live=True, now=NOW)


def test_corrupt_or_unverified_final_is_refused(session):
    match = session.get(ActiveMatch, MATCH_ID)
    result = session.scalar(select(MatchResult).where(MatchResult.match_id == MATCH_ID))
    result.verified_at = None
    session.commit()
    with pytest.raises(LifecycleBlocked, match="NOT_INDEPENDENTLY_VERIFIED"):
        run(session, now=NOW)
    assert not session.scalars(select(ArchiveJob)).all()


def test_scope_rejects_other_match_identity(session):
    session.get(ActiveMatch, MATCH_ID).provider_match_id = "wrong"
    session.commit()
    with pytest.raises(LifecycleBlocked, match="TARGET_MATCH_IDENTITY"):
        run(session, now=NOW)


def test_real_parquet_archive_is_verified_and_readable_through_history_api(session):
    store = InMemoryObjectStore()
    before = session.scalar(select(MatchResult).where(MatchResult.match_id == MATCH_ID)).verified_at
    result = run(session, store, live=True,
                 approval="ARCHIVE_ONE_VERIFIED_MATCH_ONLY", now=NOW)
    assert result["status"] == "ARCHIVED_AND_HISTORY_READBACK_VERIFIED"
    assert {r["dataset"] for r in result["receipts"]} == {"matches", "results"}
    assert all(r["row_count"] == 1 for r in result["receipts"])
    assert len(session.scalars(select(ArchiveJob)).all()) == 2
    assert session.scalar(select(MatchResult).where(MatchResult.match_id == MATCH_ID)).verified_at == before
    assert result["sql_result_writes"] == 0 and result["sql_delete"] is False
    again = run(session, store, live=True,
                approval="ARCHIVE_ONE_VERIFIED_MATCH_ONLY", now=NOW)
    assert again["status"] == "ARCHIVED_AND_HISTORY_READBACK_VERIFIED"
    assert len(session.scalars(select(ArchiveJob)).all()) == 2
