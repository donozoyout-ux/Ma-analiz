from datetime import date, datetime, timezone
from decimal import Decimal

import httpx
import pytest

from app.providers.base import ProviderAuthError, ProviderInvalidResponse, ProviderRateLimited, ProviderUnavailable
from app.providers.normalize import normalize_odds
from app.providers.registry import ProviderCaller
from app.providers.sportmonks import SportmonksProvider


TOKEN = "test-token-do-not-log"
BASE = "https://api.sportmonks.test/v3/football"


def _client(handler):
    return httpx.Client(transport=httpx.MockTransport(handler))


def _fixture(state="FT"):
    return {
        "id": 19146700, "league_id": 501, "season_id": 23690,
        "starting_at_timestamp": 1722780000,
        "league": {"id": 501, "name": "Super Lig"},
        "season": {"id": 23690, "name": "2024/2025"},
        "participants": [
            {"name": "Home FC", "meta": {"location": "home"}},
            {"name": "Away FC", "meta": {"location": "away"}},
        ],
        "state": {"short_name": state, "name": "Finished" if state == "FT" else "Not Started"},
        "scores": [
            {"description": "CURRENT", "score": {"participant": "home", "goals": 2}},
            {"description": "CURRENT", "score": {"participant": "away", "goals": 1}},
        ],
    }


def _response(request, payload, status=200, headers=None):
    return httpx.Response(status, json=payload, headers=headers, request=request)


def test_health_check_uses_authorization_header_and_never_puts_token_in_url():
    def handler(request):
        assert request.headers["Authorization"] == TOKEN
        assert TOKEN not in str(request.url)
        return _response(request, {"data": []})

    client = _client(handler)
    provider = SportmonksProvider(token=TOKEN, base_url=BASE, client=client)
    result = provider.health_check()
    assert result["provider"] == "SPORTMONKS" and result["status"] == "OK"
    assert result["latency_ms"] >= 0
    assert provider.last_successful_request is not None
    client.close()


def test_documented_error_statuses_are_classified_without_echoing_token():
    cases = [(401, ProviderAuthError), (403, ProviderAuthError), (429, ProviderRateLimited),
             (503, ProviderUnavailable), (400, ProviderInvalidResponse)]
    for status, exception in cases:
        client = _client(lambda request, status=status: _response(request, {"message": TOKEN}, status=status,
                                                                  headers={"Retry-After": "0"}))
        provider = SportmonksProvider(token=TOKEN, base_url=BASE, client=client)
        with pytest.raises(exception) as raised:
            provider.health_check()
        assert TOKEN not in str(raised.value)
        client.close()


def test_timeout_retries_are_bounded_and_return_sanitized_unavailable():
    calls = 0

    def handler(request):
        nonlocal calls
        calls += 1
        raise httpx.ReadTimeout("contains no credentials", request=request)

    client = _client(handler)
    provider = SportmonksProvider(token=TOKEN, base_url=BASE, client=client)
    caller = ProviderCaller(provider, requests_per_minute=6000, max_retries=2,
                            sleep=lambda _: None, monotonic=lambda: 1)
    with pytest.raises(ProviderUnavailable, match="timed out") as raised:
        caller.call("health_check")
    assert calls == 2
    assert TOKEN not in str(raised.value)
    client.close()


def test_league_discovery_follows_documented_pagination_and_includes_country():
    pages = []

    def handler(request):
        page = int(request.url.params["page"])
        pages.append(page)
        assert request.url.params["include"] == "country;currentSeason"
        if page == 1:
            row = {"id": 501, "name": "Super Lig", "country": {"name": "Turkey"},
                   "currentSeason": {"id": 23690, "name": "2024/2025"}}
            return _response(request, {"data": [row], "meta": {"pagination": {"has_more": True}}})
        return _response(request, {"data": [{"id": 502, "name": "Cup"}],
                                   "meta": {"pagination": {"has_more": False}}})

    client = _client(handler)
    provider = SportmonksProvider(token=TOKEN, base_url=BASE, client=client)
    leagues = provider.get_leagues()
    assert pages == [1, 2]
    assert leagues[0] == {"id": "501", "name": "Super Lig", "country": "Turkey",
                          "current_season": {"id": "23690", "name": "2024/2025"}}
    client.close()


def test_fixture_is_normalized_from_documented_league_season_participants_state_and_utc_timestamp():
    client = _client(lambda request: _response(request, {"data": [_fixture("NS")],
                                                            "meta": {"pagination": {"has_more": False}}}))
    provider = SportmonksProvider(token=TOKEN, base_url=BASE, client=client)
    matches = provider.get_matches(date(2024, 8, 4))
    assert len(matches) == 1
    match = matches[0]
    assert match["provider"] == "sportmonks"
    assert match["provider_match_id"] == "19146700"
    assert match["league_id"] == "501" and match["league_name"] == "Super Lig"
    assert match["season"] == "2024/2025" and match["provider_season_id"] == "23690"
    assert match["start_time"] == datetime.fromtimestamp(1722780000, timezone.utc)
    assert match["home_team"] == "Home FC" and match["away_team"] == "Away FC"
    assert match["status"] == "SCHEDULED"
    client.close()


def test_malformed_fixture_is_rejected_and_never_becomes_an_empty_success():
    invalid = _fixture("NS")
    invalid["participants"] = []
    client = _client(lambda request: _response(request, {"data": [invalid],
                                                            "meta": {"pagination": {"has_more": False}}}))
    provider = SportmonksProvider(token=TOKEN, base_url=BASE, client=client)
    with pytest.raises(ProviderInvalidResponse, match="rejected all 1"):
        provider.get_matches(date(2024, 8, 4))
    assert provider.last_records_received == 1 and provider.last_records_rejected == 1
    client.close()


def test_premium_odds_keep_provider_ids_and_every_timestamped_history_snapshot():
    item = {"id": 80, "fixture_id": 19146700, "market_id": 1, "bookmaker_id": 130,
            "label": "Home", "value": "2.0", "market_description": "Fulltime Result",
            "market": {"name": "Fulltime Result"}, "bookmaker": {"name": "Bookmaker A"},
            "updated_at": "2024-08-04T13:00:00Z",
            "history": [
                {"id": 1, "odd_id": 80, "value": "2.2", "bookmaker_update": "2024-08-04T12:00:00Z"},
                {"id": 2, "odd_id": 80, "value": "2.0", "bookmaker_update": "2024-08-04T13:00:00Z"},
            ]}
    client = _client(lambda request: _response(request, {"data": [item]}))
    provider = SportmonksProvider(token=TOKEN, base_url=BASE, client=client)
    odds = provider.get_odds("19146700")
    assert len(odds) == 2
    assert [row["captured_at"].isoformat() for row in odds] == [
        "2024-08-04T12:00:00+00:00", "2024-08-04T13:00:00+00:00"]
    assert [row["provider_odds_id"] for row in odds] == ["80:1", "80:2"]
    assert {row["provider_market_id"] for row in odds} == {"1"}
    assert {row["provider_bookmaker_id"] for row in odds} == {"130"}
    assert {row["market"] for row in odds} == {"1X2"}
    normalized = normalize_odds(odds[0], match_id="match-1")
    assert normalized["provider"] == "sportmonks" and normalized["odds"] == Decimal("2.2")
    assert normalized["provider_market_id"] == "1" and normalized["provider_bookmaker_id"] == "130"
    client.close()


def test_premium_odds_reject_ambiguous_timezone_in_provider_timestamp():
    item = {"id": 80, "market_id": 1, "bookmaker_id": 130, "label": "Home", "value": "2.0",
            "market": {"name": "Fulltime Result"}, "bookmaker": {"name": "Bookmaker A"},
            "latest_bookmaker_update": "2024-08-04 13:00:00"}
    client = _client(lambda request: _response(request, {"data": [item]}))
    provider = SportmonksProvider(token=TOKEN, base_url=BASE, client=client)
    with pytest.raises(ProviderInvalidResponse, match="no timezone"):
        provider.get_odds("19146700")
    client.close()


def test_results_include_only_finished_fixtures_and_require_complete_current_scores():
    finished, scheduled = _fixture("FT"), _fixture("NS")
    scheduled["id"] = 19146701
    client = _client(lambda request: _response(request, {"data": [finished, scheduled],
                                                            "meta": {"pagination": {"has_more": False}}}))
    provider = SportmonksProvider(token=TOKEN, base_url=BASE, client=client)
    results = provider.get_results(date(2024, 8, 4))
    assert results == [{"provider": "sportmonks", "provider_match_id": "19146700", "home_score": 2,
                        "away_score": 1, "status": "FINAL", "finished_at": None,
                        "result_source": "SPORTMONKS"}]
    client.close()


def test_invalid_response_envelope_fails_closed():
    client = _client(lambda request: _response(request, {"items": []}))
    provider = SportmonksProvider(token=TOKEN, base_url=BASE, client=client)
    with pytest.raises(ProviderInvalidResponse):
        provider.health_check()
    client.close()


def test_smoke_league_discovery_requires_one_turkish_super_lig():
    from app.jobs.provider_smoke_test import _find_turkish_super_lig

    assert _find_turkish_super_lig([{"id": "17", "name": "Trendyol Süper Lig", "country": "Türkiye"}])["id"] == "17"
    with pytest.raises(ValueError, match="AMBIGUOUS"):
        _find_turkish_super_lig([
            {"id": "17", "name": "Super Lig", "country": "Turkey"},
            {"id": "18", "name": "Super Lig 2", "country": "Turkey"},
        ])
    with pytest.raises(ValueError, match="NOT_FOUND"):
        _find_turkish_super_lig([{"id": "20", "name": "Premier League", "country": "England"}])
