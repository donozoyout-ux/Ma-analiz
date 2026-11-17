from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from app.analysis.engine import (
    form_analyzer, goals_analyzer, historical_similarity, h2h_analyzer,
    no_bet_reasons, odds_movement, point_in_time, scan_matches, value_analyzer,
)


NOW = datetime(2026, 11, 16, 12, tzinfo=timezone.utc)


def _match(day: int, home: str, away: str, hs: int, aws: int, **extra):
    return {"start_time": NOW - timedelta(days=day), "verified_at": NOW - timedelta(days=day), "status": "FINISHED",
            "home_team": home, "away_team": away, "home_score": hs, "away_score": aws, **extra}


def test_point_in_time_excludes_future_odds_and_rejects_naive_cutoff():
    rows = [{"captured_at": NOW - timedelta(seconds=1)}, {"captured_at": NOW + timedelta(seconds=1)}]
    assert point_in_time(rows, NOW) == rows[:1]
    with pytest.raises(ValueError):
        point_in_time(rows, datetime(2026, 11, 16))


def test_unverified_historical_results_do_not_enter_statistics():
    row = _match(2, "A", "B", 3, 0)
    row.pop("verified_at")
    assert form_analyzer("A", [row], as_of=NOW, limit=5).sample_size == 0


def test_form_and_goals_use_only_finished_past_fixtures():
    rows = [_match(1, "A", "B", 2, 0), _match(2, "C", "A", 1, 1),
            _match(3, "A", "D", 0, 1), _match(-1, "A", "B", 4, 0)]
    form = form_analyzer("A", rows, as_of=NOW, limit=5)
    assert form.sample_size == 3
    assert form.score == pytest.approx(4 / 9)
    assert "Only 3" in form.warnings[0]
    goals = goals_analyzer("A", rows, as_of=NOW, venue="home")
    assert goals["sample_size"] == 2
    assert goals["avg_goals_for"] == 1
    assert goals["btts"] == 0


def test_h2h_small_sample_is_explicitly_insufficient():
    result = h2h_analyzer("A", "B", [_match(1, "A", "B", 1, 0)], as_of=NOW)
    assert result.sample_size == 1
    assert result.evidence == "INSUFFICIENT"
    assert result.warnings


def test_value_reports_margin_and_no_vig_market_probability():
    result = value_analyzer(.55, 2.0, market_probabilities=[.50, .28, .24])
    assert result["value"] == pytest.approx(.10)
    assert result["overround"] == pytest.approx(.02)
    assert result["no_vig_market_probability"] == pytest.approx(.50 / 1.02)
    assert result["status"] == "POTENTIAL_VALUE"


def test_odds_movement_and_live_exclusion():
    result = odds_movement(2.2, 1.92)
    assert result["direction"] == "SHORTENING"
    assert result["movement_percent"] == pytest.approx(-12.7272727)
    assert odds_movement(2.2, 1.92, live=True)["movement_percent"] is None


def test_live_snapshot_never_enters_closing_projection():
    from decimal import Decimal
    from types import SimpleNamespace
    from app.analysis.odds import project_odds_snapshots

    kickoff = NOW - timedelta(hours=1)
    rows = [
        SimpleNamespace(bookmaker="Book", market="1X2", selection="HOME", line=None, line_key="",
                        odds=Decimal("2.0"), captured_at=kickoff - timedelta(minutes=2), period="PREMATCH",
                        snapshot_type="LATEST", status="AVAILABLE", source="fixture"),
        SimpleNamespace(bookmaker="Book", market="1X2", selection="HOME", line=None, line_key="",
                        odds=Decimal("1.2"), captured_at=NOW, period="LIVE", snapshot_type="LIVE",
                        status="AVAILABLE", source="fixture"),
    ]
    projected, live = project_odds_snapshots(rows, kickoff=kickoff, match_status="FINISHED", as_of=NOW)
    assert projected[0]["closing"] is None
    assert len(live) == 1 and live[0]["price"] == Decimal("1.2")


def test_provider_canonical_odds_dicts_are_projected_without_orm_conversion():
    from app.analysis.odds import project_odds_snapshots

    kickoff = NOW + timedelta(days=2)
    rows = [{"bookmaker": "Bet365", "market": "1X2", "selection": selection,
             "line": None, "line_key": "", "odds": Decimal(price),
             "captured_at": NOW, "period": "PREMATCH", "snapshot_type": "LATEST",
             "status": "AVAILABLE", "source": "goaloo"}
            for selection, price in (("HOME", "2.0"), ("DRAW", "3.2"), ("AWAY", "4.0"))]
    projected, live = project_odds_snapshots(rows, kickoff=kickoff, match_status="SCHEDULED", as_of=NOW)
    assert len(projected) == 3 and not live
    assert {row["selection"] for row in projected} == {"HOME", "DRAW", "AWAY"}


def test_similarity_is_weighted_and_future_results_are_excluded():
    rows = [_match(i + 1, "H", "A", 2, 0, league_id="L", home_odds=2.0, expected_goals=2.5) for i in range(4)]
    rows.append(_match(-1, "H", "A", 0, 4, league_id="L", home_odds=2.0, expected_goals=2.5))
    result = historical_similarity({"league_id": "L", "home_odds": 2.0, "expected_goals": 2.5}, rows, as_of=NOW)
    assert result["sample_size"] == 4
    assert result["distribution"]["HOME"] == pytest.approx(1)
    assert result["warning"] == "HISTORICAL_EVIDENCE_INSUFFICIENT"


def test_scanner_keeps_upcoming_fixtures_but_filters_future_odds_and_never_signals():
    fixture = {"id": "m1", "home_team": "H", "away_team": "A", "start_time": NOW + timedelta(hours=2),
               "odds": [{"captured_at": NOW + timedelta(minutes=1), "period": "PREMATCH"}]}
    rows = scan_matches([fixture], as_of=NOW)
    assert len(rows) == 1
    assert rows[0]["status"] == "DATA_UNAVAILABLE"
    assert rows[0]["odds_count"] == 0
    assert rows[0]["signal"] == "NO BET"


def test_no_bet_reasons_covers_low_sample_data_and_value_gates():
    reasons = no_bet_reasons(sample_size=3, value=-.1, data_status="PARTIAL", stale=True, disagreement=True)
    assert {"INSUFFICIENT_SAMPLE", "NO_POSITIVE_VALUE", "DATA_PARTIAL", "STALE_DATA", "MODEL_DISAGREEMENT"} == set(reasons)
