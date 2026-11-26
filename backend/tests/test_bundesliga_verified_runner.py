"""The runner is inert by default and checks existing SQL against independently fetched finals."""
from datetime import date, datetime, timedelta, timezone
from uuid import uuid4
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.jobs.bundesliga_verified_runner import run, collect
from app.jobs.independent_result_persistence import VerificationBlocked
from app.models import ActiveMatch, MatchResult
from app.storage.r2 import InMemoryObjectStore

NOW = datetime(2026, 11, 25, 19, tzinfo=timezone.utc)
DAY = date(2026, 8, 28)
KICKOFF = datetime(2026, 8, 28, 18, 30, tzinfo=timezone.utc)


def sources():
    shared = {"league":"de.bl1", "home_team":"FC Bayern München",
        "away_team":"VfB Stuttgart", "start_time":KICKOFF.isoformat(),
        "reported_score":[5,1], "final_confirmed":True, "observed_at":NOW.isoformat()}
    return ([{**shared,"provider":"openligadb","source_match_id":"83156",
            "source_url":"https://api.openligadb.de/getmatchdata/bl1/2026/1"}],
            [{**shared,"provider":"football-data.org","source_match_id":"565776",
              "source_status":"FINISHED",
              "source_url":"https://api.football-data.org/v4/competitions/BL1/matches"}])


@pytest.fixture
def db():
    engine = create_engine("sqlite://")
    ActiveMatch.__table__.create(engine)
    MatchResult.__table__.create(engine)
    with Session(engine, expire_on_commit=False) as session:
        match = ActiveMatch(id=uuid4(), provider="openligadb", provider_match_id="83156",
            league_id="de.bl1", league_name="Bundesliga", season="2026-2027",
            home_team="FC Bayern München", away_team="VfB Stuttgart",
            start_time=KICKOFF, status="FINISHED",
            expires_at=KICKOFF+timedelta(hours=32))
        session.add(match)
        session.flush()
        session.add(MatchResult(match_id=match.id, provider="openligadb",
            status="FINAL", home_score=5, away_score=1,
            verified_at=None, result_source="OpenLigaDB"))
        session.commit()
        yield session
    engine.dispose()


def test_dry_run_does_not_write_existing_final(db):
    p, s = sources()
    report = run(DAY, 1, session=db, primary=p, secondary=s, now=NOW)
    assert report["result"]["status"] == "DRY_RUN_MATCHED"
    assert db.query(MatchResult).one().verified_at is None


def test_live_requires_explicit_operator_approval(db):
    p, s = sources()
    with pytest.raises(VerificationBlocked, match="LIVE_OPERATOR_APPROVAL_REQUIRED"):
        run(DAY, 1, session=db, store=InMemoryObjectStore(),
            primary=p, secondary=s, now=NOW, dry_run=False)
    assert db.query(MatchResult).one().verified_at is None


def test_live_requires_r2_even_if_explicitly_approved(db):
    p, s = sources()
    with pytest.raises(VerificationBlocked, match="LIVE_R2_REQUIRED"):
        run(DAY, 1, session=db, primary=p, secondary=s,
            now=NOW, dry_run=False, live_approved=True)


def test_multiple_matches_fail_closed(db):
    p, s = sources()
    with pytest.raises(VerificationBlocked, match="ONE_FINISHED_MATCH"):
        run(DAY, 1, session=db, primary=p+p, secondary=s, now=NOW)
    assert db.query(MatchResult).one().verified_at is None


def test_wrong_matchday_fails_before_http():
    with pytest.raises(ValueError, match="INVALID_BUNDESLIGA_SCOPE"):
        collect(DAY, 35)


def test_no_database_session_fails_closed():
    p, s = sources()
    with pytest.raises(VerificationBlocked, match="POSTGRES_SESSION_REQUIRED"):
        run(DAY, 1, primary=p, secondary=s, now=NOW)
