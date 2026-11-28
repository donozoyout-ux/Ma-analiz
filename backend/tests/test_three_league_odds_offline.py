"""Synthetic-only regression tests; no DB, R2, live provider or API keys."""
import math
import pytest
from app.analytics.three_league_odds_offline import assess

GRANT = {"demo": {"reviewed": True, "analysis": True, "storage": True,
                  "redistribution": True, "commercial": True,
                  "licence_reference": "https://example.invalid/licence"}}


def match(mid="1", league="de.bl1", season="2024-2025", odds=None, **changes):
    row = {"match_id": mid, "league": league, "season": season,
           "source": "demo", "kickoff_utc": "2025-01-01T18:00:00Z",
           "odds_captured_utc": "2025-01-01T17:55:00Z",
           "odds_stage": "closing", "status": "finished",
           "odds": odds or {"home": 2, "draw": 4, "away": 4},
           "home_score": 2, "away_score": 1}
    return {**row, **changes}


def test_empty_and_three_leagues():
    result = assess([], GRANT)
    assert result["odds_api_credits"] == result["source_requests"] == 0
    assert set(result["leagues"]) == {"de.bl1", "es.1", "en.pl"}
    assert all(x["status"] == "HESAPLANAMADI" for x in result["leagues"].values())


def test_probabilities_margin_favourite_draw_and_loss():
    matches = [match("1"), match("2", home_score=1, away_score=1),
               match("3", home_score=0, away_score=2)]
    league = assess(matches, GRANT)["leagues"]["de.bl1"]
    assert league["sample_size"] == 3
    assert league["favourite_win_rate"] == pytest.approx(1 / 3)
    assert league["favourite_draw_rate"] == pytest.approx(1 / 3)
    assert league["favourite_loss_rate"] == pytest.approx(1 / 3)
    assert league["mean_overround"] == pytest.approx(0.0)
    assert league["calibration"]["<60%"]["n"] == 3
    assert math.isfinite(league["brier_multiclass"])
    assert math.isfinite(league["log_loss"])


def test_licence_must_allow_all_uses():
    result = assess([match()], {"demo": {**GRANT["demo"], "redistribution": False}})
    assert result["rejected"]["source_permission_unverified"] == 1
    assert result["leagues"]["de.bl1"]["status"] == "HESAPLANAMADI"


@pytest.mark.parametrize("changes", [
    {"odds_captured_utc": "2025-01-01T18:01:00Z"},
    {"odds_captured_utc": "2025-01-01T17:00:00"},
    {"odds_stage": "opening"},
    {"status": "scheduled"},
    {"home_score": None},
    {"odds": {"home": 0.5, "draw": 4, "away": 4}},
    {"odds": {"home": float("nan"), "draw": 4, "away": 4}},
    {"odds": {"home": 1.1, "draw": 1.1, "away": 1.1}},
    {"kickoff_utc": "2026-11-29T00:00:00Z"},
])
def test_invalid_records_excluded(changes):
    result = assess([match(**changes)], GRANT)
    assert result["leagues"]["de.bl1"]["sample_size"] == 0
    assert result["rejected"]


def test_strict_duplicate_match_identity():
    with pytest.raises(ValueError, match="DUPLICATE_MATCH"):
        assess([match(), match()], GRANT)


def test_season_and_scope_no_leakage():
    result = assess([match("b", league="de.bl1"), match("l", league="es.1"),
                     match("e", league="en.pl"), match("x", league="fr.1")], GRANT)
    assert [result["leagues"][league]["sample_size"] for league in ("de.bl1", "es.1", "en.pl")] == [1, 1, 1]
    assert result["rejected"]["unsupported_scope"] == 1
    assert result["ranking"].startswith("HESAPLANAMADI")
