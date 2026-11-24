"""Read-only bulletin aggregation contract; no SQL or R2 writes."""
from datetime import date, datetime, timedelta, timezone
from unittest.mock import MagicMock

from app.api import source_bulletin as bulletin


def test_bulletin_collects_four_leagues_and_reports_missing_source(monkeypatch):
    bulletin._CACHE.clear()
    bulletin._LAST_ATTEMPT.clear()
    called = []
    class Stub:
        def __init__(self, **kwargs): pass
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def get_competition_window(self, shortcut, start, end):
            called.append(shortcut)
            assert start == date(2026, 11, 21)
            assert end == date(2026, 12, 1)
            if shortcut == "uecl2026":
                raise RuntimeError("provider offline")
            league = next(key for short,key,_ in bulletin.SOURCES if short == shortcut)
            return [{
                "provider_match_id": "99" + shortcut,
                "league_id": league,
                "home_team": "Home", "away_team": "Away",
                "start_time": datetime(2026, 11, 24, 20, tzinfo=timezone.utc),
                "status": "SCHEDULED", "score": None,
            }]
    report = bulletin.build_bulletin(date(2026, 11, 24), provider_factory=Stub)
    assert called == ["bl1", "ucl", "uel2026", "uecl2026"]
    assert len(report["matches"]) == 3
    assert len(report["failures"]) == 1
    assert report["failures"][0]["league_id"] == "uefa.ecl"
    assert report["live_guaranteed"] is False
    assert report["odds_available"] is False
    assert report["statistics_available"] is False
    again = bulletin.build_bulletin(date(2026, 11, 24), provider_factory=Stub)
    assert len(again["matches"]) == 3
    assert len(again["failures"]) == 1
    assert called == ["bl1", "ucl", "uel2026", "uecl2026"]
    bulletin._CACHE.clear()
    bulletin._LAST_ATTEMPT.clear()


def test_bulletin_does_not_call_unknown_competitions_or_exceed_scope():
    bulletin._CACHE.clear()
    bulletin._LAST_ATTEMPT.clear()
    from app.providers.openligadb import OpenLigaDBProvider
    fake = MagicMock()
    provider = OpenLigaDBProvider(client=fake)
    import pytest
    with pytest.raises(ValueError, match="Unsupported"):
        provider.get_competition_window("not-a-league", date(2026, 11, 24), date(2026, 11, 25))
    with pytest.raises(ValueError, match="too large"):
        provider.get_competition_window("bl1", date(2026, 11, 1), date(2026, 11, 30))
    fake.get.assert_not_called()


def test_bulletin_matches_actual_source_league_identity():
    bulletin._CACHE.clear()
    bulletin._LAST_ATTEMPT.clear()
    class Stub:
        def __init__(self, **kwargs): pass
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def get_competition_window(self, shortcut, start, end):
            return [{
                "provider_match_id": "42",
                "league_id": "de.bl1" if shortcut == "ucl" else next(key for short,key,_ in bulletin.SOURCES if short==shortcut),
                "home_team": "A", "away_team": "B",
                "start_time": datetime(2026, 11, 24, tzinfo=timezone.utc),
                "status": "FINISHED", "score": [1, 0],
            }]
    report=bulletin.build_bulletin(date(2026, 11, 24), provider_factory=Stub)
    assert "uefa.cl" not in report["available_leagues"]
    assert any(f["league_id"] == "uefa.cl" for f in report["failures"])
    bulletin._CACHE.clear()
    bulletin._LAST_ATTEMPT.clear()
