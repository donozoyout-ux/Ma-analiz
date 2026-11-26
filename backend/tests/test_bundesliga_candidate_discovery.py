"""Read-only candidate discovery regression and bounds."""
from datetime import date, datetime, timedelta, timezone
from uuid import uuid4
import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool
from app.models import ActiveMatch, MatchResult
from app.jobs.bundesliga_candidate_discovery import run, parse_days, DiscoveryBlocked

NOW = datetime(2026, 11, 26, 18, tzinfo=timezone.utc)
DAY = date(2026, 11, 20)
KICKOFF = datetime(2026, 11, 20, 19, 30, tzinfo=timezone.utc)


@pytest.fixture
def db():
    engine = create_engine("sqlite://", connect_args={"check_same_thread":False},
                           poolclass=StaticPool)
    for model in (ActiveMatch, MatchResult):
        model.__table__.create(engine)
    with Session(engine, expire_on_commit=False) as s:
        m = ActiveMatch(id=uuid4(), provider="openligadb",
            provider_match_id="83156", league_id="de.bl1", season="2026-2027",
            league_name="Bundesliga", home_team="FC Bayern München",
            away_team="VfB Stuttgart", start_time=KICKOFF, status="FINISHED",
            expires_at=KICKOFF+timedelta(hours=32))
        s.add(m); s.flush()
        r = MatchResult(match_id=m.id, provider="openligadb",
            home_score=5, away_score=1, status="FINAL", result_source="OpenLigaDB")
        s.add(r); s.commit()
        yield s, m, r
    engine.dispose()


def observations(*, score=(5, 1), stale=False):
    when = NOW-timedelta(minutes=30 if stale else 1)
    common = dict(league="de.bl1",home_team="FC Bayern München",
        away_team="VfB Stuttgart", start_time=KICKOFF.isoformat(),
        source_status="FINISHED", reported_score=list(score),
        final_confirmed=True, observed_at=when.isoformat())
    return [
        {**common,"provider":"openligadb", "source_match_id":"83156"},
    ], [
        {**common,"provider":"football-data.org","source_match_id":"565776"}
    ]


def test_exact_two_source_sql_final_is_candidate_without_writes(db):
    s,m,r = db
    out=run(s, days=[DAY], matchday=11,
            collector=lambda day, md, now: observations(), now=NOW)
    assert out["status"] == "SUCCESS" and out["candidate_count"] == 1
    assert out["candidates"][0]["status"] == "CANDIDATE_FOR_EXPLICIT_VERIFICATION"
    assert out["r2_writes"] == out["sql_writes"] == 0
    assert out["sql_delete"] is False and r.verified_at is None


def test_conflicting_sql_score_held(db):
    s,m,r = db
    r.home_score=4; s.commit()
    out=run(s, days=[DAY], matchday=11,
            collector=lambda day, md, now: observations(), now=NOW)
    assert out["candidate_count"] == 0 and out["held_count"] == 1
    assert out["held"][0]["reason"] == "SQL_FINAL_OR_IDENTITY_CONFLICT"


def test_stale_observations_held(db):
    s,m,r = db
    out=run(s, days=[DAY], matchday=11,
            collector=lambda day, md, now: observations(stale=True), now=NOW)
    assert out["candidate_count"] == 0
    assert out["held"][0]["reason"] == "STALE_OR_AMBIGUOUS_EVIDENCE"


def test_verified_result_reported_without_writing(db):
    s,m,r=db
    r.verified_at=NOW-timedelta(hours=1);s.commit()
    out=run(s, days=[DAY],matchday=11,
            collector=lambda day, md, now: observations(), now=NOW)
    assert out["candidates"][0]["status"] == "ALREADY_VERIFIED"
    assert r.verified_at is not None


def test_invalid_or_unbounded_dates_rejected():
    with pytest.raises(DiscoveryBlocked):
        parse_days("2026-11-20,2026-11-20",today=NOW.date())
    with pytest.raises(DiscoveryBlocked):
        parse_days("2026-11-20,2026-11-21,2026-11-22,2026-11-23",today=NOW.date())
    with pytest.raises(DiscoveryBlocked):
        parse_days("2027-11-20",today=NOW.date())
    with pytest.raises(DiscoveryBlocked):
        parse_days("2026-01-01",today=NOW.date())


def test_different_source_ids_counted_before_sql_lookup(db):
    s, m, r = db
    # Valid cross-provider IDs are intentionally different.
    out = run(s, days=[DAY], matchday=11,
              collector=lambda day, md, now: observations(), now=NOW)
    assert out["matched_both_sources"] == 1
    assert out["in_postgresql"] == 1
    assert out["candidate_count"] == 1
    assert out["candidates"][0]["openligadb_match_id"] == "83156"
    assert out["candidates"][0]["football_data_match_id"] == "565776"


def test_corrob_exception_is_held_without_sql_writes(db, monkeypatch):
    import app.jobs.bundesliga_candidate_discovery as module
    s, m, r = db

    def broken(*args):
        raise ValueError("BAD_SOURCE_PAYLOAD")

    monkeypatch.setattr(module, "corroborate", broken)
    out = run(s, days=[DAY], matchday=11,
              collector=lambda day, md, now: observations(), now=NOW)
    assert out["candidate_count"] == 0
    assert out["held"][0]["reason"] == "SOURCE_CORROBORATION_FAILED"
    assert out["sql_writes"] == out["r2_writes"] == 0
    assert out["sql_delete"] is False


def test_source_match_count_independent_of_missing_sql(db):
    s, m, r = db
    s.delete(r)
    s.delete(m)
    s.commit()
    out = run(s, days=[DAY], matchday=11,
              collector=lambda day, md, now: observations(), now=NOW)
    assert out["matched_both_sources"] == 1
    assert out["in_postgresql"] == 0
    assert out["not_in_postgresql"] == 1
    assert out["held"][0]["reason"] == "SQL_MATCH_NOT_FOUND"


def test_multiple_sql_results_held(db):
    s, m, r = db
    s.add(MatchResult(match_id=m.id, provider="openligadb",
                      home_score=5, away_score=1, status="FINAL",
                      result_source="OpenLigaDB"))
    s.commit()
    out = run(s, days=[DAY], matchday=11,
              collector=lambda day, md, now: observations(), now=NOW)
    assert out["candidate_count"] == 0
    assert out["held"][0]["reason"] == "SQL_MULTIPLE_RESULTS"
