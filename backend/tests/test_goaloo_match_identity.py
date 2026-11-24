"""Offline cross-provider safety tests: never infer match from date alone."""
from datetime import datetime, timezone

import pytest

from app.providers.goaloo_match_identity import map_bundesliga, summary


def open_match(match_id, home, away, kickoff="2026-08-28T18:30:00Z"):
    return {"matchID": match_id, "team1": {"teamName": home},
            "team2": {"teamName": away}, "matchDateTimeUTC": kickoff}


def goaloo(rows):
    return {"LeagueInfo": [8, "German Bundesliga", "2026-2027"],
            "TeamInfo": [[88, "Bayern Munchen"], [155, "VfB Stuttgart"],
                         [99, "Borussia Dortmund"], [160, "Hamburger SV"]],
            "ScheduleList": {"1": rows}}


def row(match_id, home=88, away=155, at="2026-08-29 02:30"):
    return [match_id, 8, -1, at, home, away, "5-1", "1-0"]


def test_reviewed_bayern_stuttgart_is_matched():
    result = map_bundesliga([open_match(83156, "FC Bayern München", "VfB Stuttgart")],
                            goaloo([row(3018786)]))
    assert summary(result) == {"MATCHED": 1, "NEEDS_REVIEW": 0, "NO_MATCH": 0}
    assert result[0].openligadb_id == "83156"
    assert result[0].evidence["kickoff_delta_seconds"] == 0


def test_date_only_with_wrong_clubs_never_matches():
    result = map_bundesliga([open_match(83156, "FC Bayern München", "VfB Stuttgart")],
                            goaloo([row(3018788, home=99, away=160)]))
    assert result[0].status == "NO_MATCH"
    assert result[0].openligadb_id is None


def test_swapped_home_away_never_matches():
    result = map_bundesliga([open_match(83156, "FC Bayern München", "VfB Stuttgart")],
                            goaloo([row(3018786, home=155, away=88)]))
    assert result[0].status == "NO_MATCH"


def test_kickoff_difference_over_30_minutes_blocks_match():
    result = map_bundesliga([open_match(83156, "FC Bayern München", "VfB Stuttgart")],
                            goaloo([row(3018786, at="2026-08-29 03:15")]))
    assert result[0].status == "NO_MATCH"


def test_duplicate_openliga_candidate_requires_review():
    m = open_match(83156, "FC Bayern München", "VfB Stuttgart")
    n = open_match(99888, "FC Bayern München", "VfB Stuttgart")
    result = map_bundesliga([m, n], goaloo([row(3018786)]))
    assert result[0].status == "NEEDS_REVIEW"
    assert result[0].openligadb_id is None


def test_duplicate_goaloo_candidate_requires_review():
    m = open_match(83156, "FC Bayern München", "VfB Stuttgart")
    result = map_bundesliga([m], goaloo([row(3018786), row(3018787)]))
    assert summary(result)["MATCHED"] == 0
    assert all(item.openligadb_id is None for item in result)


def test_unknown_team_does_not_match_even_if_date_identical():
    data = goaloo([row(3018786, home=777)])
    data["TeamInfo"].append([777, "Unknown Football Club"])
    result = map_bundesliga([open_match(83156, "FC Bayern München", "VfB Stuttgart")], data)
    assert result[0].status == "NO_MATCH"


def test_wrong_season_or_league_rejected():
    payload = goaloo([row(3018786)])
    payload["LeagueInfo"][0] = 113
    with pytest.raises(ValueError, match="UNREVIEWED"):
        map_bundesliga([], payload)


def test_conflicting_duplicate_source_id_not_auto_joined():
    payload = goaloo([row(3018786), row(3018786, at="2026-08-29 02:00")])
    result = map_bundesliga([open_match(83156, "FC Bayern München", "VfB Stuttgart")], payload)
    assert result[0].status == "NEEDS_REVIEW"
    assert result[0].openligadb_id is None
