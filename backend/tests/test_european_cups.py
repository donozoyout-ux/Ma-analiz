"""Isolated Europe 2026/27 fixture tests; no external or production writes."""
from datetime import date
from unittest.mock import MagicMock

import pytest
from fastapi import HTTPException

from app.providers.openligadb import OpenLigaDBProvider
from app.providers.european_cups import EUROPEAN_CUPS
from app.api import european_cups as api


@pytest.fixture
def source_fixture():
    return [{
        "matchID": 991234,
        "leagueSeason": 2026,
        "matchDateTimeUTC": "2026-11-24T20:00:00Z",
        "matchIsFinished": False,
        "group": {"groupName": "5. Spieltag", "groupOrderID": 5},
        "team1": {"teamName": "Team Alpha", "teamId": 11},
        "team2": {"teamName": "Team Beta", "teamId": 22},
        "matchResults": [],
    }]


@pytest.mark.parametrize("slug,expected", [
    ("champions-league", ("ucl", "uefa.cl")),
    ("europa-league", ("uel2026", "uefa.el")),
    ("conference-league", ("uecl2026", "uefa.ecl")),
])
def test_uefa_competition_identity_and_openligadb_path(slug, expected, source_fixture):
    shortcut, league_id = expected
    fake = MagicMock()
    fake.get.return_value.status_code = 200
    fake.get.return_value.json.return_value = source_fixture
    provider = OpenLigaDBProvider(client=fake, requests_per_minute=60)
    result = provider.get_competition_matches(shortcut, date(2026, 11, 24))
    assert len(result) == 1
    match = result[0]
    assert match["league_id"] == league_id
    assert match["league_name"] == EUROPEAN_CUPS[slug]["name"]
    assert match["country"] == "Europe"
    assert match["provider_match_id"] == "991234"
    assert match["match_date"] == date(2026, 11, 24)
    assert match["score"] is None
    assert "getmatchdata/" + shortcut + "/2026" in fake.get.call_args.args[0]
    assert provider.get_odds("991234") == []


def test_uefa_rejects_unknown_source_and_season_without_io():
    fake = MagicMock()
    provider = OpenLigaDBProvider(client=fake)
    with pytest.raises(ValueError, match="Unsupported European competition"):
        provider.get_competition_matches("fake", date(2026, 11, 24))
    with pytest.raises(ValueError, match="2026/27"):
        provider.get_competition_matches("ucl", date(2028, 1, 1))
    fake.get.assert_not_called()


def test_bundesliga_parse_unchanged(source_fixture):
    fake = MagicMock()
    fake.get.return_value.status_code = 200
    fake.get.return_value.json.return_value = source_fixture
    provider = OpenLigaDBProvider(client=fake)
    data = provider.get_matches(date(2026, 11, 24))
    assert data[0]["league_id"] == "de.bl1"
    assert data[0]["league_name"] == "Bundesliga"
    assert data[0]["country"] == "Germany"


def test_catalog_does_not_call_provider(monkeypatch):
    monkeypatch.setattr(api, "OpenLigaDBProvider", lambda **kw: pytest.fail("source must not be called"))
    result = api.european_competitions()
    assert len(result["competitions"]) == 3
    assert len({c["league_id"] for c in result["competitions"]}) == 3
    assert result["odds_available"] is False


def test_daily_api_only_returns_requested_competition(monkeypatch):
    class FakeProvider:
        def __init__(self, **kwargs): pass
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def get_competition_matches(self, shortcut, day):
            assert shortcut == "ucl"
            assert day == date(2026, 11, 24)
            return [{"league_id": "uefa.cl", "provider_match_id": "1"}]

    monkeypatch.setattr(api, "OpenLigaDBProvider", FakeProvider)
    api._LAST_SOURCE_CALL.clear()
    api._CACHE.clear()
    data = api.european_matches("champions-league", date(2026, 11, 24))
    assert data["odds_available"] is False
    assert data["matches"][0]["league_id"] == "uefa.cl"
    assert api.european_matches("champions-league", date(2026, 11, 24)) == data
    with pytest.raises(HTTPException) as unknown:
        api.european_matches("not-a-competition", date(2026, 11, 24))
    assert unknown.value.status_code == 404
    # Separate UEFA competitions have separate source request budgets.
    # Same competition on another date still respects the 60-second guard.
    api._CACHE.clear()
    with pytest.raises(HTTPException) as quota:
        api.european_matches("champions-league", date(2026, 11, 25))
    assert quota.value.status_code == 429
    api._LAST_SOURCE_CALL.clear()


def test_bundesliga_day_endpoint_is_read_only_and_isolated(monkeypatch):
    api._CACHE.clear()
    api._LAST_SOURCE_CALL.clear()
    class FakeProvider:
        def __init__(self, **kwargs): pass
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def get_matches(self, day):
            assert day == date(2026, 11, 24)
            return [{"league_id": "de.bl1", "provider_match_id": "83237",
                     "home_team": "Home", "away_team": "Away"}]
    monkeypatch.setattr(api, "OpenLigaDBProvider", FakeProvider)
    response = api.bundesliga_matches(date(2026, 11, 24))
    assert response["league_id"] == "de.bl1"
    assert response["matches"][0]["provider_match_id"] == "83237"
    assert response["odds_available"] is False
    with pytest.raises(HTTPException) as invalid:
        api.bundesliga_matches(date(2028, 1, 1))
    assert invalid.value.status_code == 422
    api._CACHE.clear()
    api._LAST_SOURCE_CALL.clear()
