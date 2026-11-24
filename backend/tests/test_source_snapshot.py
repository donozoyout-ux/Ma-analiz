"""Offline safety/contract checks for scheduled source snapshots."""
from datetime import date, datetime, timezone

from app.jobs.source_snapshot import COMPETITIONS, collect


class FakeProvider:
    requested = []
    def __init__(self, max_retries=1):
        assert max_retries == 1
    def __enter__(self):
        return self
    def __exit__(self, *args):
        return False
    def get_competition_window(self, shortcut, start, end):
        self.requested.append((shortcut, start.isoformat(), end.isoformat()))
        return [{
            "provider_match_id": f"{shortcut}-11",
            "league_id": {"bl1":"de.bl1","ucl":"uefa.cl",
                          "uel2026":"uefa.el","uecl2026":"uefa.ecl"}[shortcut],
            "league_name": shortcut,
            "season":"2026-2027",
            "home_team":"Home","away_team":"Away",
            "start_time":datetime(2026,11,28,18,30,tzinfo=timezone.utc),
            "status":"SCHEDULED","score":None,
        }]


def test_four_leagues_are_isolated_and_paced():
    delays=[]
    FakeProvider.requested=[]
    result=collect(date(2026,11,25), provider_factory=FakeProvider,
                   sleep=delays.append, pace_seconds=60)
    assert result["counts"]["matches"]==4
    assert result["counts"]["scheduled"]==4
    assert result["failures"]==[]
    assert [x[0] for x in FakeProvider.requested]==list(COMPETITIONS)
    assert delays==[60,60,60]
    assert result["postgres_written"] is False
    assert result["r2_written"] is False


def test_source_failure_is_not_reported_as_zero_fixtures():
    class Broken(FakeProvider):
        def get_competition_window(self, shortcut, start, end):
            if shortcut=="ucl":
                raise RuntimeError("BLOCKED")
            return super().get_competition_window(shortcut,start,end)
    result=collect(date(2026,11,25), provider_factory=Broken,
                   sleep=lambda _:None,pace_seconds=0)
    assert result["counts"]["matches"]==3
    assert result["failures"]==[{"competition":"ucl","code":"RuntimeError"}]


def test_unsupported_dates_are_rejected():
    import pytest
    with pytest.raises(ValueError,match="OUTSIDE"):
        collect(date(2028,1,1), provider_factory=FakeProvider,
                sleep=lambda _:None,pace_seconds=0)


def test_invalid_scores_do_not_enter_snapshot():
    class Invalid(FakeProvider):
        def get_competition_window(self,shortcut,start,end):
            rows=super().get_competition_window(shortcut,start,end)
            rows[0]["score"]=[-3,0]
            return rows
    result=collect(date(2026,11,25),provider_factory=Invalid,
                   sleep=lambda _:None,pace_seconds=0)
    assert not result["matches"]
    assert len(result["failures"])==4
