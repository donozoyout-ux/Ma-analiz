"""Offline identity-candidate tests; no Cornerflick fetch or database writes."""
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest

from app.providers.cornerflick_identity import SourceIdentity, propose_reconciliation

START = datetime(2026, 11, 20, 19, 30, tzinfo=timezone.utc)
CF = SourceIdentity(
    provider="cornerflick",
    provider_match_id="cornerflick:match:bayern-munchen-vs-1-fc-koln-bundesliga-2026-11-20",
    league_id="de.bl1", season="2026-2027",
    home_team="Bayern München", away_team="1. FC Köln",
    kickoff_at=START,
)
OL = SourceIdentity(
    provider="openligadb", provider_match_id="13579",
    league_id="de.bl1", season="2026-2027",
    home_team="Bayern München", away_team="1. FC Köln",
    kickoff_at=START,
)


def test_unique_cross_provider_candidate_is_still_unverified():
    answer = propose_reconciliation(CF, [OL])
    assert answer.status == "REVIEW_REQUIRED"
    assert answer.openligadb_id == OL.provider_match_id
    assert "review" in answer.explanation.lower()


def test_timezone_aware_same_instant_matches():
    local = START.astimezone(ZoneInfo("Europe/Istanbul"))
    answer = propose_reconciliation(CF, [replace(OL, kickoff_at=local)])
    assert answer.status == "REVIEW_REQUIRED"


@pytest.mark.parametrize("difference", [
    {"away_team": "Different Team"},
    {"home_team": "FC Bayern München"}, # Do not guess source aliases.
    {"home_team": "1. FC Köln", "away_team": "Bayern München"},
    {"season": "2025-2026"},
    {"kickoff_at": START + timedelta(seconds=61)},
])
def test_unknown_alias_wrong_team_season_or_time_never_auto_matches(difference):
    result = propose_reconciliation(CF, [replace(OL, **difference)])
    assert result.status == "NO_MATCH"
    assert result.openligadb_id is None


def test_multiple_ids_are_ambiguous_even_when_both_seem_equal():
    result = propose_reconciliation(CF, [OL, replace(OL, provider_match_id="22222")])
    assert result.status == "AMBIGUOUS"


def test_same_provider_id_with_conflicting_identity_fails_closed():
    result = propose_reconciliation(CF, [OL, replace(OL, away_team="Other")])
    assert result.status == "SOURCE_ID_CONFLICT"


@pytest.mark.parametrize("bad", [
    {"provider_match_id": "http://cornerflick.com/x"},
    {"provider_match_id": "cornerflick:match:../../secret"},
    {"kickoff_at": START.replace(tzinfo=None)},
    {"league_id": "at.bl1"},
    {"season": "2026-2025"},
])
def test_invalid_cornerflick_identity_is_refused(bad):
    with pytest.raises(ValueError):
        propose_reconciliation(replace(CF, **bad), [OL])


def test_invalid_competing_provider_is_rejected():
    with pytest.raises(ValueError, match="OPENLIGADB_IDENTITY_REQUIRED"):
        propose_reconciliation(CF, [CF])


@pytest.mark.parametrize("tolerance", [-1, 61, 1.0, True])
def test_unsafe_tolerance_rejected(tolerance):
    with pytest.raises(ValueError, match="INVALID_TOLERANCE"):
        propose_reconciliation(CF, [OL], tolerance_seconds=tolerance)
