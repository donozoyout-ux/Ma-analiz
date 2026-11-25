"""No credentials/network: official API schema and fail-closed comparison."""
from datetime import date, datetime, timezone

import pytest

from app.providers.football_data_org import (
    FootballDataFailure, parse_bundesliga_finals, fetch_bundesliga_finals,
)
from app.analysis.football_data_corroboration import corroborate
from app.jobs.football_data_probe import run

NOW = datetime(2026, 11, 25, 12, tzinfo=timezone.utc)
DAY = date(2026, 8, 28)


def secondary(score=(5, 1), *, status="FINISHED", competition="BL1",
              kickoff="2026-08-28T18:30:00Z"):
    return {"competition": {"code": competition}, "matches": [{
        "competition": {"code": competition}, "id": 99999,
        "utcDate": kickoff, "status": status,
        "homeTeam": {"id": 5, "name": "FC Bayern München"},
        "awayTeam": {"id": 6, "name": "VfB Stuttgart"},
        "score": {"fullTime": {"home": score[0], "away": score[1]}},
    }]}


def primary():
    return [{"provider": "openligadb", "league": "de.bl1",
        "source_match_id": "123", "home_team": "FC Bayern München",
        "away_team": "VfB Stuttgart", "start_time": "2026-08-28T18:30:00+00:00",
        "final_confirmed": True, "reported_score": [5, 1]}]


def test_valid_official_api_result_is_only_a_candidate():
    rows = parse_bundesliga_finals(secondary(), day=DAY, observed_at=NOW)
    assert rows[0]["source_match_id"] == "99999"
    result = corroborate(primary(), rows)
    assert result["status"] == "MATCHED_CANDIDATES_NOT_PERSISTED"
    assert len(result["matches"]) == 1
    assert result["writes_allowed"] is False and result["persistence_ready"] is False


def test_score_disagreement_blocks_verification():
    rows = parse_bundesliga_finals(secondary(score=(4, 1)), day=DAY, observed_at=NOW)
    result = corroborate(primary(), rows)
    assert result["status"] == "CONFLICT"
    assert not result["matches"]


def test_kickoff_and_team_identity_must_match_exactly():
    rows = parse_bundesliga_finals(secondary(kickoff="2026-08-28T19:30:00Z"), day=DAY, observed_at=NOW)
    assert corroborate(primary(), rows)["status"] == "PENDING_INDEPENDENT_VERIFICATION"
    rows[0]["start_time"] = primary()[0]["start_time"]
    rows[0]["home_team"] = "Bayern Munich"
    assert corroborate(primary(), rows)["status"] == "PENDING_INDEPENDENT_VERIFICATION"


def test_invalid_competition_is_rejected():
    with pytest.raises(FootballDataFailure, match="LEAGUE_MISMATCH"):
        parse_bundesliga_finals(secondary(competition="PL"), day=DAY, observed_at=NOW)


def test_unfinished_match_is_not_a_final():
    assert parse_bundesliga_finals(secondary(status="IN_PLAY"), day=DAY, observed_at=NOW) == []


def test_token_missing_never_issues_request(monkeypatch):
    monkeypatch.delenv("FOOTBALL_DATA_ORG_API_TOKEN", raising=False)
    class Forbidden:
        def get(self, *args, **kwargs):
            raise AssertionError("unexpected network access")
    with pytest.raises(FootballDataFailure, match="TOKEN_NOT_CONFIGURED"):
        fetch_bundesliga_finals(DAY, client=Forbidden(), observed_at=NOW)


def test_authenticated_probe_does_not_log_token():
    class Response:
        status_code = 401
        content = b"unauthorized"
    class Client:
        def get(self, url, headers, params):
            assert headers == {"X-Auth-Token": "secret-token"}
            assert params["dateTo"] == "2026-08-29"
            return Response()
    with pytest.raises(FootballDataFailure, match="REMOTE_UNAVAILABLE") as err:
        fetch_bundesliga_finals(DAY, token="secret-token", client=Client(), observed_at=NOW)
    assert "secret-token" not in str(err.value)


def test_readonly_probe_with_injected_source_payloads():
    openligadb = [{
        "matchID": 123, "leagueShortcut": "bl1",
        "matchDateTimeUTC": "2026-08-28T18:30:00Z", "matchIsFinished": True,
        "team1": {"teamId": 40, "teamName": "FC Bayern München"},
        "team2": {"teamId": 134, "teamName": "VfB Stuttgart"},
        "matchResults": [{"resultTypeID": 2, "pointsTeam1": 5, "pointsTeam2": 1}],
    }]
    report = run(DAY, 1, openligadb_payload=openligadb,
                 secondary_payload=secondary())
    assert report["decision"]["status"] == "MATCHED_CANDIDATES_NOT_PERSISTED"
    assert report["verified_at_changed"] is False
    assert report["sql"] == "NOT_USED" and report["r2"] == "NOT_USED"
