from datetime import datetime, timedelta, timezone
from decimal import Decimal
from types import SimpleNamespace

from app.analysis.engine import SimilarityThresholds
from app.analysis.pipeline import analyze_match


def test_pipeline_uses_r2_evidence_and_keeps_running_without_ai():
    now = datetime(2026, 11, 16, 12, tzinfo=timezone.utc)
    kickoff = now + timedelta(hours=3)
    history = [{"home_team": "H", "away_team": f"A{i}", "league_id": "36",
            "home_score": 2 if i < 8 else 0, "away_score": 0 if i < 8 else 1,
        "status": "FINISHED", "verified_at": now - timedelta(days=i + 1),
        "start_time": now - timedelta(days=i + 1), "home_odds": 2.0, "draw_odds": 3.5,
        "away_odds": 4.0, "expected_goals": 2.5} for i in range(12)]
    odds = []
    for selection, price in (("HOME", "2.0"), ("DRAW", "3.5"), ("AWAY", "4.0")):
        for kind, offset in (("OPENING", -20), ("LATEST", -2)):
            odds.append(SimpleNamespace(bookmaker="B365", market="1X2", selection=selection, line=None,
                line_key="", odds=Decimal(price), captured_at=now + timedelta(minutes=offset),
                period="PREMATCH", status="AVAILABLE", source="goaloo", snapshot_type=kind))
    result = analyze_match({"id": "m1", "home_team": "H", "away_team": "A", "league_id": "36",
        "start_time": kickoff, "status": "SCHEDULED"}, statistics_history=history,
        historical_rows=history, odds_rows=odds, as_of=now,
        thresholds=SimilarityThresholds(weak=10, moderate=20, strong=30))
    assert result["analysis_status"] == "ANALYZED"
    assert result["selection"] == "HOME"
    assert result["value"]["status"] == "POTENTIAL_VALUE"
    assert result["ai"]["status"] == "AI_UNAVAILABLE"
    assert result["final"]["signal"] == "SIGNAL"


def test_pipeline_fails_closed_when_odds_or_historical_evidence_is_absent():
    now = datetime(2026, 11, 16, 12, tzinfo=timezone.utc)
    match = {"id": "m1", "home_team": "H", "away_team": "A", "start_time": now + timedelta(hours=2)}
    missing = analyze_match(match, statistics_history=[], historical_rows=[], odds_rows=[], as_of=now)
    assert missing["analysis_status"] == "ODDS_MISSING"
    assert missing["final"]["signal"] == "NO BET"
