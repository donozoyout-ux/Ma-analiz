"""Daily runner contracts with fake read-only providers and isolated DB."""
from datetime import date, datetime, timezone, timedelta
from uuid import uuid4

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.db.base import Base
from app.models import ActiveMatch
from app.jobs.goaloo_daily_collect import map_observed_rows, collect_one_day


DAY = date(2026, 8, 28)
KICKOFF = datetime(2026, 8, 28, 18, 30, tzinfo=timezone.utc)


def ol(id="83156", home="FC Bayern München", away="VfB Stuttgart"):
    return {"provider":"openligadb","provider_match_id":id,"league_id":"de.bl1",
            "season":"2026-2027","home_team":home,"away_team":away,"start_time":KICKOFF}


def go(id="3018786", home="Bayern Munchen", away="VfB Stuttgart"):
    return {"provider":"goaloo","provider_match_id":id,"league_id":"8",
            "season":"2026-2027","home_team":home,"away_team":away,"start_time":KICKOFF}


def quote():
    return {"provider":"goaloo", "source":"goaloo", "provider_odds_id":"8:euro:f:u",
            "provider_bookmaker_id":"8", "bookmaker":"Bet365", "market":"1X2",
            "selection":"HOME","odds":"1.22","snapshot_type":"OPENING",
            "captured_at":datetime(2026, 11, 24, 17, tzinfo=timezone.utc)}


def test_single_fixture_identity():
    r=map_observed_rows([go()], [ol()])
    assert len(r)==1 and r[0].status=="MATCHED" and r[0].openligadb_id=="83156"


def test_same_date_wrong_teams_not_auto_matched():
    r=map_observed_rows([go(home="Borussia Dortmund")], [ol()])
    assert r[0].status=="NO_MATCH"


def test_duplicate_target_is_quarantined():
    r=map_observed_rows([go(),go(id="3018787")],[ol()])
    assert [x.status for x in r]==["NEEDS_REVIEW","NEEDS_REVIEW"]


def test_invalid_provider_never_links():
    fake=go()
    fake["provider"]="unknown"
    assert map_observed_rows([fake],[ol()])==[]


def test_runner_dryrun_only_reads_and_bounds_request_count():
    class Open:
        def get_matches(self, day):
            assert day==DAY
            return [ol()]
    class Goal:
        calls=[]
        def get_matches_on_day(self, day, league, season):
            assert (day,league,season)==(DAY,"8","2026-2027")
            return [go()]
        def get_odds(self, match_id):
            self.calls.append(match_id)
            return [quote()]
    engine=create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as s:
        s.add(ActiveMatch(id=uuid4(),provider="openligadb",provider_match_id="83156",
                          league_id="de.bl1",league_name="Bundesliga",season="2026-2027",
                          home_team="FC Bayern München",away_team="VfB Stuttgart",
                          start_time=KICKOFF,status="SCHEDULED",expires_at=KICKOFF+timedelta(days=200)))
        s.commit()
        goal=Goal()
        result=collect_one_day(DAY,openliga=Open(),goaloo=goal,session=s,dry_run=True)
        assert result["mapping"]["MATCHED"]==1
        assert result["odds_pages_checked"]==1
        assert result["import"]["counts"]["eligible_quotes"]==1
        assert result["import"]["sql_inserted"]==0
        assert goal.calls==["3018786"]
    engine.dispose()


def test_scope_rejects_mass_requests():
    with pytest.raises(ValueError):
        collect_one_day(DAY,openliga=None,goaloo=None,session=None,max_odds_requests=50)
