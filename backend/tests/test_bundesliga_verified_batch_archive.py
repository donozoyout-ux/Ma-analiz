"""Bounded Bundesliga archive: only independently proven results, no deletion."""
from datetime import date, datetime, timedelta, timezone
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.compiler import compiles

from app.jobs.bundesliga_verified_batch_archive import run, BatchArchiveBlocked
from app.models import ActiveMatch, ArchiveJob, MatchResult, SystemSetting
from app.storage.archive import archive_partition, SQLArchiveTracker
from app.storage.r2 import InMemoryObjectStore

DAY = date(2026, 8, 28)
NOW = datetime(2026, 11, 27, tzinfo=timezone.utc)
KICKOFF = datetime(2026, 8, 28, 18, 30, tzinfo=timezone.utc)


@compiles(JSONB, "sqlite")
def compile_jsonb_for_isolated_sqlite(_type, _compiler, **_kwargs):
    return "JSON"


@pytest.fixture
def setup():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False},
                           poolclass=StaticPool)
    for model in (ActiveMatch, MatchResult, ArchiveJob, SystemSetting):
        model.__table__.create(engine)
    with Session(engine, expire_on_commit=False) as db:
        match = ActiveMatch(id=uuid4(), provider="openligadb", provider_match_id="83156",
            league_id="de.bl1", season="2026-2027", league_name="Bundesliga",
            home_team="FC Bayern München", away_team="VfB Stuttgart",
            start_time=KICKOFF, status="FINISHED",
            # History reads only revisions available at NOW, not the host clock.
            last_updated_at=NOW-timedelta(days=1),
            expires_at=KICKOFF+timedelta(hours=32))
        db.add(match)
        db.flush()
        result = MatchResult(match_id=match.id, provider="openligadb",
            home_score=5, away_score=1, status="FINAL",
            result_source="OpenLigaDB", verified_at=NOW-timedelta(days=1),
            updated_at=NOW-timedelta(days=1))
        db.add(result)
        db.commit()
        store = InMemoryObjectStore()
        yield db, store, match, result
    engine.dispose()


def add_proof(db, store, match, result):
    row = {"match_id": str(match.id), "result_id": str(result.id),
        "decision": "EXACT_IDENTITIES_AND_FINAL_SCORE_AGREE",
        "openligadb_match_id": "83156", "football_data_match_id": "565776",
        "league": "de.bl1", "season": "2026-2027",
        "utc_kickoff": KICKOFF.isoformat(), "home_score": 5, "away_score": 1}
    archive_partition(store, dataset="verification-evidence", season="2026-2027",
        league="de.bl1", day=DAY, rows=[row], part="independent-ci-example",
        tracker=SQLArchiveTracker(db), retries=1)


def test_without_independent_proof_is_held_without_writes(setup):
    db, store, match, result = setup
    out = run(db, store, day=DAY, now=NOW)
    assert out["status"] == "PARTIAL" and out["held"] == 1
    assert out["results"][0]["reason"] == "ONE_INDEPENDENT_EVIDENCE_REQUIRED"
    assert not db.scalars(select(ArchiveJob)).all()
    assert result.verified_at is not None


def test_proven_fixture_dry_run_prepares_only_matching_parts(setup):
    db, store, match, result = setup
    add_proof(db, store, match, result)
    out = run(db, store, day=DAY, now=NOW)
    assert out["status"] == "SUCCESS" and out["archived"] == 0
    assert out["results"][0]["status"] == "DRY_RUN_READY"
    assert {p["dataset"] for p in out["results"][0]["parts"]} == {"matches", "results"}
    assert len(db.scalars(select(ArchiveJob)).all()) == 1
    assert out["sql_delete"] is False


def test_live_requires_explicit_approval(setup):
    db, store, match, result = setup
    with pytest.raises(BatchArchiveBlocked, match="EXPLICIT_BATCH_APPROVAL_REQUIRED"):
        run(db, store, day=DAY, now=NOW, live=True)


def test_live_writes_two_verified_parts_and_history_reads_5_1(setup):
    db, store, match, result = setup
    add_proof(db, store, match, result)
    original = result.verified_at
    out = run(db, store, day=DAY, now=NOW, live=True,
              approval="ARCHIVE_VERIFIED_BATCH_ONLY")
    assert out["status"] == "SUCCESS" and out["archived"] == 1
    receipt = out["results"][0]
    assert receipt["status"] == "ARCHIVED_AND_HISTORY_READBACK_VERIFIED"
    assert {r["dataset"] for r in receipt["receipts"]} == {"matches", "results"}
    assert len(db.scalars(select(ArchiveJob)).all()) == 3
    assert result.verified_at == original
    assert out["result_updates"] == 0 and out["sql_delete"] is False
    retry = run(db, store, day=DAY, now=NOW, live=True,
                approval="ARCHIVE_VERIFIED_BATCH_ONLY")
    assert retry["status"] == "SUCCESS" and retry["archived"] == 1
    assert len(db.scalars(select(ArchiveJob)).all()) == 3


def test_proof_conflicting_score_is_held(setup):
    db, store, match, result = setup
    add_proof(db, store, match, result)
    result.home_score = 4
    db.commit()
    out = run(db, store, day=DAY, now=NOW, live=True,
              approval="ARCHIVE_VERIFIED_BATCH_ONLY")
    assert out["status"] == "PARTIAL" and out["archived"] == 0
    assert out["results"][0]["reason"] == "INDEPENDENT_EVIDENCE_SQL_CONFLICT"
    assert len(db.scalars(select(ArchiveJob)).all()) == 1


def test_no_unbounded_batch(setup):
    db, store, match, result = setup
    with pytest.raises(BatchArchiveBlocked, match="BATCH_LIMIT_EXCEEDED"):
        run(db, store, day=DAY, limit=11, now=NOW)


def test_absent_day_reports_zero_writes(setup):
    db, store, match, result = setup
    out = run(db, store, day=date(2026, 8, 29), now=NOW)
    assert out["status"] == "SUCCESS"
    assert out["processed"] == 0 and out["archived"] == 0
