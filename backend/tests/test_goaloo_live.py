"""Opt-in live Goaloo smoke check; never substitutes fixture data for live results."""
from datetime import datetime, timezone

import pytest

from app.core.config import get_settings
from app.providers.base import ProviderUnavailable
from app.providers.normalize import normalize_matches
from app.providers.registry import create_provider


def test_goaloo_live_league_match_and_odds_smoke():
    settings = get_settings()
    if (settings.provider_id or "").lower() != "goaloo" or not settings.enabled_league_ids:
        pytest.skip("NOT_TESTED: Goaloo is not selected or ENABLED_LEAGUES is empty")

    provider = create_provider(settings)
    try:
        try:
            leagues = provider.get_leagues()
            if not leagues:
                pytest.skip("NOT_TESTED: Goaloo returned no configured leagues")

            fixtures = []
            for league_id in settings.enabled_league_ids:
                for season in provider._season_choices(league_id):
                    fixtures.extend(provider._match_rows(league_id, season))
                if fixtures:
                    break
            if not fixtures:
                pytest.skip("NOT_TESTED: no real Goaloo fixtures were available for configured leagues")

            parsed = normalize_matches(fixtures, provider=provider.name)
            assert parsed, "Goaloo returned fixtures but none passed the production normalizer"
            fixture = next((item for item in fixtures if item["status"] == "SCHEDULED"), fixtures[0])
            real_day = fixture["start_time"].astimezone(timezone.utc).date()
            matches = provider.get_matches(real_day)
            match = next((item for item in matches if item["provider_match_id"] == fixture["provider_match_id"]), None)
            assert match is not None, "Goaloo day query did not return its real fixture"

            odds = provider.get_odds(match["provider_match_id"])
            assert isinstance(odds, list)
            assert all(row.get("source") == "goaloo" and row.get("market") in {"1X2", "AH", "OU"}
                       and row.get("period") in {"PREMATCH", "LIVE"}
                       and row.get("snapshot_type") in {"OPENING", "LATEST", "CLOSING", "LIVE"}
                       for row in odds)
        except ProviderUnavailable as exc:
            pytest.skip(f"NOT_TESTED: Goaloo was unavailable during live smoke check ({exc.code})")
    finally:
        close = getattr(provider, "close", None)
        if callable(close):
            close()
