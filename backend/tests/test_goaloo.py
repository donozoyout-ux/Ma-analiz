"""Tests use explicitly test-only wire fixtures; no fixture data is ingested."""
from datetime import date, datetime, timezone
from decimal import Decimal
import json

import httpx
import pytest

from app.providers.base import ProviderInvalidResponse, ProviderUnavailable
from app.providers.goaloo import GoalooProvider
from app.providers.normalize import normalize_odds


# TEST FIXTURE: reduced Goaloo schedule schema based on the documented row layout.
SCHEDULE_FIXTURE = {
    "LeagueInfo": ["36", "Example League", "2024-2025"],
    "TeamInfo": [[27, "Home FC"], [29, "Away FC"]],
    "ScheduleList": {"R_1": [[2590898, 36, -1, "2024-08-17 03:00", 27, 29, "1-0", "0-0"]]},
}

# TEST FIXTURE: shape and observed market values from a Goaloo odds response.
ODDS_FIXTURE = {
    "ErrCode": 0, "Data": {"mixodds": [{"cid": 8, "cn": "Bet365",
      "euro": {"f": {"u": "1.62", "g": "4", "d": "5"},
               "l": {"u": "1.66", "g": "4.1", "d": "5"},
               "r": {"u": "1", "g": "26", "d": "501"}},
      "ou": {"f": {"u": "1.02", "g": "3.25", "d": "0.88"},
             "l": {"u": "1.05", "g": "3", "d": "0.85"},
             "r": {"u": "7.5", "g": "1.5", "d": "0.08"}},
      "ah": {"f": {"u": "1.05", "g": "1", "d": "0.85"},
             "l": {"u": "0.85", "g": "0.75", "d": "1.05"},
             "r": {"u": "0.65", "g": "0", "d": "1.35"}}}]},
    "MatchState": 0,
}


def test_goaloo_seasons_schedule_timezone_and_results_are_normalized():
    request_counts = {"seasons": 0, "schedule": 0}
    def handler(request):
        if request.url.path.endswith("sea36.json"):
            request_counts["seasons"] += 1
            return httpx.Response(200, content=b"\xef\xbb\xbf" + json.dumps({"SeasonList": ["2024-2025"]}).encode(), request=request)
        request_counts["schedule"] += 1
        return httpx.Response(200, json=SCHEDULE_FIXTURE, request=request)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    provider = GoalooProvider(base_url="https://football.goaloo.test", league_ids=("36",), client=client)
    assert provider.get_seasons("36") == [{"id": "2024-2025", "name": "2024-2025", "league_id": "36"}]
    matches = provider.get_matches(date(2024, 8, 16))
    assert matches[0]["status"] == "FINISHED"
    assert matches[0]["start_time"] == datetime(2024, 8, 16, 19, tzinfo=timezone.utc)
    assert provider.get_results(date(2024, 8, 16))[0]["home_score"] == 1
    assert provider.get_leagues()[0] == {
        "id": "36", "name": "Example League", "season": "2024-2025", "source": "goaloo"
    }
    assert request_counts == {"seasons": 1, "schedule": 1}
    client.close()


def test_goaloo_odds_map_verified_bookmakers_markets_hk_lines_and_live_separately():
    def handler(request):
        assert request.headers["Referer"].endswith("oddscomp-2590898")
        assert request.headers["X-Requested-With"] == "XMLHttpRequest"
        assert request.url.params["h"] == "0"
        return httpx.Response(200, json=ODDS_FIXTURE, request=request)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    provider = GoalooProvider(client=client)
    provider._fixture_cache["2590898"] = {"status": "SCHEDULED"}
    odds = provider.get_odds("2590898")
    euro_open = next(row for row in odds if row["market"] == "1X2" and row["selection"] == "HOME" and row["snapshot_type"] == "OPENING")
    assert euro_open["bookmaker"] == "Bet365" and euro_open["odds"] == Decimal("1.62")
    normalized = normalize_odds(euro_open, match_id="m", provider="goaloo")
    assert normalized["period"] == "PREMATCH" and normalized["snapshot_type"] == "OPENING"
    assert normalized["raw_value"] == '{"u":"1.62","g":"4","d":"5"}'
    ah_open = next(row for row in odds if row["market"] == "AH" and row["selection"] == "HOME" and row["snapshot_type"] == "OPENING")
    assert ah_open["odds"] == Decimal("2.05") and ah_open["line"] == -1
    ou_open = next(row for row in odds if row["market"] == "OU" and row["selection"] == "OVER" and row["snapshot_type"] == "OPENING")
    assert ou_open["odds"] == Decimal("2.02") and ou_open["line"] == "3.25"
    live = next(row for row in odds if row["market"] == "1X2" and row["selection"] == "AWAY" and row["period"] == "LIVE")
    assert live["snapshot_type"] == "LIVE"
    assert not any(row["odds"] <= 1 for row in odds)
    client.close()


def test_goaloo_error_envelope_and_unknown_schedule_status_never_claim_success():
    def error_handler(request):
        return httpx.Response(200, json={"code": 1002}, request=request)

    client = httpx.Client(transport=httpx.MockTransport(error_handler))
    provider = GoalooProvider(client=client)
    provider._fixture_cache["1"] = {"status": "SCHEDULED"}
    with pytest.raises(ProviderUnavailable, match="error envelope"):
        provider.get_odds("1")
    client.close()

    fixture = {**SCHEDULE_FIXTURE, "ScheduleList": {"R_1": [[1, 36, 1, "2024-08-17 03:00", 27, 29, "", ""]]}}
    def status_handler(request):
        if request.url.path.endswith("sea36.json"):
            return httpx.Response(200, json={"SeasonList": ["2024-2025"]}, request=request)
        return httpx.Response(200, json=fixture, request=request)
    client = httpx.Client(transport=httpx.MockTransport(status_handler))
    provider = GoalooProvider(base_url="https://football.goaloo.test", league_ids=("36",), client=client)
    with pytest.raises(ProviderUnavailable, match="unverified Goaloo match status"):
        provider.get_matches(date(2024, 8, 16))
    client.close()


def test_goaloo_bookmaker_unknown_is_ignored_not_relabelled():
    payload = {"ErrCode": 0, "Data": {"mixodds": [{"cid": 999, "cn": "Unknown",
                "euro": {"f": {"u": "2", "g": "3", "d": "4"}}}]}}
    client = httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(200, json=payload, request=request)))
    provider = GoalooProvider(client=client)
    provider._fixture_cache["m"] = {"status": "SCHEDULED"}
    assert provider.get_odds("m") == []
    client.close()


def test_goaloo_latest_snapshot_is_never_inferred_to_be_closing():
    payload = {"ErrCode": 0, "Data": {"mixodds": [{"cid": 8, "cn": "Bet365",
        "euro": {"l": {"u": "1.8", "g": "3.5", "d": "4.5"},
                 "r": {"u": "2", "g": "4", "d": "4"}}}]}}
    client = httpx.Client(transport=httpx.MockTransport(
        lambda request: httpx.Response(200, json=payload, request=request)))
    provider = GoalooProvider(client=client)
    provider._fixture_cache["finished-fixture"] = {"status": "FINISHED"}
    odds = provider.get_odds("finished-fixture")
    assert {row["snapshot_type"] for row in odds if row["period"] == "PREMATCH"} == {"LATEST"}
    assert all(row["snapshot_type"] != "CLOSING" for row in odds)
    assert all(row["snapshot_type"] == "LIVE" for row in odds if row["period"] == "LIVE")
    client.close()


def test_goaloo_uefa_cup_contract_uses_c_schedule_and_skips_round_metadata():
    # TEST FIXTURE: reduced shape based on the live Goaloo cup contract. No
    # fixture rows from this test are written to production storage.
    cup_schedule = {
        "LeagueInfo": [103, "UEFA Champions League", "UEFA CL", "2026-2027", "", "", "", ""],
        "TeamList": [[166, "Home FC", "HOM", ""], [254, "Away FC", "AWY", ""]],
        "ScheduleList": {
            "sub_500": {"G1": [
                [21328, 383, 4, 1, []],
                [21329, 384, 4, 1, [], []],
                [2659257, 103, -1, "2024-09-18 00:45", 166, 254, "3-1", "2-0"],
            ]}
        },
    }

    def handler(request):
        if request.url.path.endswith("sea103.json"):
            return httpx.Response(200, json={"SeasonList": ["2024-2025"]}, request=request)
        assert request.url.path.endswith("/2024-2025/c103_en.json")
        return httpx.Response(200, json=cup_schedule, request=request)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    provider = GoalooProvider(
        base_url="https://football.goaloo.test", league_ids=("103",),
        season_ids=("2024-2025",), client=client,
    )
    assert provider.get_leagues() == [{
        "id": "103", "name": "UEFA Champions League", "season": "2024-2025", "source": "goaloo"
    }]
    fixtures = provider._match_rows("103", "2024-2025")
    assert len(fixtures) == 1
    assert fixtures[0]["provider_match_id"] == "2659257"
    assert fixtures[0]["home_team"] == "Home FC"
    client.close()


def test_goaloo_rejects_fixture_outside_requested_season():
    outside = {
        **SCHEDULE_FIXTURE,
        "ScheduleList": {"R_1": [[1, 36, -1, "2025-08-17 03:00", 27, 29, "1-0", "0-0"]]},
    }

    def handler(request):
        if request.url.path.endswith("sea36.json"):
            return httpx.Response(200, json={"SeasonList": ["2024-2025"]}, request=request)
        return httpx.Response(200, json=outside, request=request)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    provider = GoalooProvider(base_url="https://football.goaloo.test", league_ids=("36",), client=client)
    with pytest.raises(ProviderInvalidResponse, match="no verified fixtures"):
        provider.get_matches(date(2025, 8, 16))
    client.close()
