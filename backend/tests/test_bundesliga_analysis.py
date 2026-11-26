"""Real SQLite fixture tests for the read-only Bundesliga team statistics."""
from datetime import date, datetime, timedelta, timezone
from uuid import uuid4

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.jobs.bundesliga_analysis import AnalysisBlocked, get_team_stats
from app.models import ActiveMatch, MatchResult

TEAM = "FC Bayern München"
START = datetime(2026, 8, 28, 18, 30, tzinfo=timezone.utc)


@pytest.fixture
def db():
    engine = create_engine("sqlite://")
    ActiveMatch.__table__.create(engine)
    MatchResult.__table__.create(engine)
    with Session(engine, expire_on_commit=False) as s:
        yield s
    engine.dispose()


def add_result(db, *, n=0, home=TEAM, away="VfB Stuttgart",
               home_score=5, away_score=1, verified=True, league="de.bl1",
               season="2026-2027"):
    dt = START + timedelta(days=n)
    m = ActiveMatch(id=uuid4(), provider="openligadb",
                    provider_match_id=str(83156+n), league_id=league,
                    league_name="Bundesliga", season=season,
                    home_team=home, away_team=away, start_time=dt,
                    status="FINISHED", expires_at=dt+timedelta(hours=32))
    db.add(m)
    db.flush()
    db.add(MatchResult(match_id=m.id, provider="openligadb",
                       status="FINAL", home_score=home_score,
                       away_score=away_score, result_source="OpenLigaDB",
                       verified_at=dt+timedelta(hours=2) if verified else None))
    db.commit()
    return m


def test_home_win_goals_are_not_double_counted(db):
    add_result(db)
    r = get_team_stats(TEAM, session=db)
    assert (r["played"], r["wins"], r["goals_for"], r["goals_against"]) == (1, 1, 5, 1)
    assert r["goal_difference"] == 4
    assert r["home_played"] == 1 and r["home_wins"] == 1
    assert r["form"] == ["W"]


def test_away_loss_and_form(db):
    add_result(db)
    add_result(db, n=1, home="VfB Stuttgart", away=TEAM, home_score=2, away_score=1)
    r = get_team_stats(TEAM, session=db)
    assert r["played"] == 2
    assert r["wins"] == r["losses"] == 1
    assert r["goals_for"] == 6 and r["goals_against"] == 3
    assert r["away_played"] == 1 and r["away_wins"] == 0
    assert r["form"] == ["L", "W"]


def test_excludes_unverified_and_other_leagues(db):
    add_result(db, verified=False)
    add_result(db, n=1, league="en.pl")
    add_result(db, n=2, season="2025-2026")
    r = get_team_stats(TEAM, session=db)
    assert r["played"] == 0
    assert "NO_VERIFIED_RESULTS" in r["warnings"]


def test_no_session_does_not_create_fake_database():
    r = get_team_stats(TEAM)
    assert r["session_available"] is False
    assert r["played"] == 0
    assert r["warnings"] == ["SQL_SESSION_UNAVAILABLE"]


def test_day_filter_excludes_intervening_dates(db):
    add_result(db, n=0)
    add_result(db, n=1)
    add_result(db, n=2)
    r = get_team_stats(TEAM, days=[date(2026, 8, 28), date(2026, 8, 30)], session=db)
    assert r["played"] == 2


def test_draw_home_away(db):
    add_result(db, home_score=1, away_score=1)
    r = get_team_stats(TEAM, session=db)
    assert r["draws"] == 1 and r["form"] == ["D"]


def test_form_last_five_newest_first(db):
    for n in range(6):
        add_result(db, n=n, home_score=2 if n % 2 else 0, away_score=1)
    r = get_team_stats(TEAM, session=db)
    assert r["played"] == 6
    assert r["form"] == ["W", "L", "W", "L", "W"]


def test_invalid_scope_rejected():
    with pytest.raises(AnalysisBlocked):
        get_team_stats("")
    with pytest.raises(AnalysisBlocked):
        get_team_stats(TEAM, days=[date(2026, 8, 28)] * 2)


def test_read_only_does_not_update_verified_at(db):
    m = add_result(db)
    before = db.query(MatchResult).filter(MatchResult.match_id == m.id).one().verified_at
    r = get_team_stats(TEAM, session=db)
    after = db.query(MatchResult).filter(MatchResult.match_id == m.id).one().verified_at
    assert r["played"] == 1
    assert before == after
