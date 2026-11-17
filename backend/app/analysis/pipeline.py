"""End-to-end deterministic analysis orchestration with fail-closed decisions."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Iterable

from app.analysis.ai import AIProvider, analyze_with_ai
from app.analysis.combined import ScoreWeights, combine_scores
from app.analysis.engine import (SimilarityThresholds, form_analyzer, goals_analyzer,
    historical_similarity, h2h_analyzer, no_bet_reasons, odds_movement, value_analyzer)
from app.analysis.odds import project_odds_snapshots


MODEL_VERSION = "historical-similarity-1"
FEATURE_VERSION = "canonical-features-1"


def analyze_match(match: dict[str, Any], *, statistics_history: Iterable[dict[str, Any]],
                  historical_rows: Iterable[dict[str, Any]], odds_rows: list[Any],
                  as_of: datetime, ai_provider: AIProvider | None = None,
                  weights: ScoreWeights | None = None,
                  thresholds: SimilarityThresholds | None = None) -> dict[str, Any]:
    if as_of.tzinfo is None or as_of.utcoffset() is None:
        raise ValueError("as_of must include a timezone")
    timestamp = as_of.astimezone(timezone.utc)
    required = ("id", "home_team", "away_team", "start_time")
    if any(not match.get(field) for field in required):
        return _unavailable("DATA_MISSING", timestamp, "MATCH_FIELDS_MISSING")
    kickoff = match["start_time"]
    if isinstance(kickoff, str):
        kickoff = datetime.fromisoformat(kickoff.replace("Z", "+00:00"))
    if kickoff.tzinfo is None or kickoff.utcoffset() is None:
        return _unavailable("DATA_MISSING", timestamp, "MATCH_TIMEZONE_MISSING")

    history = [row for row in statistics_history if row.get("verified_at") is not None]
    home, away = match["home_team"], match["away_team"]
    stats = {
        "home_form_5": form_analyzer(home, history, as_of=timestamp, limit=5).to_dict(),
        "away_form_5": form_analyzer(away, history, as_of=timestamp, limit=5).to_dict(),
        "home_at_home_5": form_analyzer(home, history, as_of=timestamp, limit=5, venue="home").to_dict(),
        "away_away_5": form_analyzer(away, history, as_of=timestamp, limit=5, venue="away").to_dict(),
        "home_goals": goals_analyzer(home, history, as_of=timestamp),
        "away_goals": goals_analyzer(away, history, as_of=timestamp),
        "h2h": h2h_analyzer(home, away, history, as_of=timestamp).to_dict(),
    }
    kickoff_utc = kickoff.astimezone(timezone.utc)
    odds_summary, live_odds = project_odds_snapshots(odds_rows, kickoff=kickoff_utc,
        match_status=str(match.get("status", "SCHEDULED")), as_of=timestamp)
    bookmakers: dict[str, dict[str, float]] = {}
    for row in odds_summary:
        if row["market"] == "1X2" and row.get("line") is None and row.get("latest"):
            bookmakers.setdefault(row["bookmaker"], {})[row["selection"]] = float(row["latest"]["price"])
    bookmaker = next((name for name in sorted(bookmakers)
                      if all(selection in bookmakers[name] for selection in ("HOME", "DRAW", "AWAY"))), None)
    if not bookmaker:
        return {**_unavailable("ODDS_MISSING", timestamp, "COMPLETE_PREMATCH_1X2_UNAVAILABLE"),
                "statistics": stats, "odds": {"status": "UNAVAILABLE", "live_count": len(live_odds)}}

    market_odds = bookmakers[bookmaker]
    market_probabilities = [1 / market_odds[s] for s in ("HOME", "DRAW", "AWAY")]
    target = {"league_id": match.get("league_id"), "home_odds": market_odds["HOME"],
              "draw_odds": market_odds["DRAW"], "away_odds": market_odds["AWAY"]}
    target["home_strength"] = stats["home_at_home_5"]["score"]
    target["away_strength"] = stats["away_away_5"]["score"]
    target["home_form"] = stats["home_form_5"]["score"]
    target["away_form"] = stats["away_form_5"]["score"]
    home_goals, away_goals = stats["home_goals"], stats["away_goals"]
    if home_goals["avg_goals_for"] is not None and away_goals["avg_goals_for"] is not None:
        target["expected_goals"] = home_goals["avg_goals_for"] + away_goals["avg_goals_for"]
    target["btts_rate"] = ((home_goals["btts"] + away_goals["btts"]) / 2
                           if home_goals["btts"] is not None and away_goals["btts"] is not None else None)
    selected_odds = {(row["market"], row["selection"]): row for row in odds_summary
                     if row["bookmaker"] == bookmaker}
    for selection in ("HOME", "DRAW", "AWAY"):
        projection = selected_odds.get(("1X2", selection))
        if projection:
            opening = projection.get("opening", {}).get("price")
            latest = projection.get("latest", {}).get("price")
            target[f"opening_{selection.lower()}_odds"] = float(opening) if opening is not None else None
            if selection == "HOME":
                target["odds_movement"] = odds_movement(float(opening) if opening is not None else None,
                    float(latest) if latest is not None else None)["movement_percent"]
    ou = next((row for row in odds_summary if row["bookmaker"] == bookmaker and row["market"] == "OU"
               and row["selection"] == "OVER" and row.get("latest", {}).get("price") is not None), None)
    if ou and ou.get("line") is not None:
        target["ou_line"] = float(ou["line"])
    similarity = historical_similarity(target, historical_rows, as_of=timestamp, thresholds=thresholds)
    rates = similarity.get("distribution")
    sample_size = int(similarity.get("sample_size", 0))
    min_sample = (thresholds or SimilarityThresholds()).weak
    selection = max(rates, key=rates.get) if rates and sample_size >= min_sample else None
    model_probability = float(rates[selection]) if selection else None
    value = None
    if selection and model_probability is not None:
        value = {"status": "AVAILABLE", **value_analyzer(model_probability, market_odds[selection],
                market_probabilities=market_probabilities)}
    else:
        value = {"status": "MODEL_PROBABILITY_UNAVAILABLE", "value": None}

    evidence = [
        {"id": "home_form_5", "feature": "home_last_5_form", "value": stats["home_form_5"]["score"],
         "sample_size": stats["home_form_5"]["sample_size"], "timestamp": timestamp.isoformat(),
         "available_at": timestamp},
        {"id": "away_form_5", "feature": "away_last_5_form", "value": stats["away_form_5"]["score"],
         "sample_size": stats["away_form_5"]["sample_size"], "timestamp": timestamp.isoformat(),
         "available_at": timestamp},
        {"id": "historical_similarity", "feature": "similar_match_distribution", "value": rates,
         "sample_size": sample_size, "timestamp": timestamp.isoformat(), "available_at": timestamp},
        {"id": "bookmaker_market", "feature": "prematch_1x2_odds", "value": market_odds,
         "sample_size": 1, "timestamp": timestamp.isoformat(),
         "available_at": max((odds_summary_row["latest"]["captured_at"] for odds_summary_row in odds_summary
                              if odds_summary_row["bookmaker"] == bookmaker and odds_summary_row["market"] == "1X2"
                              and odds_summary_row["latest"] is not None), default=timestamp)},
    ]
    ai = analyze_with_ai(ai_provider, {"match": {"home": home, "away": away}, "evidence": evidence})
    statistical_score = model_probability * 100 if model_probability is not None else None
    combined = combine_scores({"statistical": statistical_score,
        "historical": similarity.get("similarity_score", None) * 100 if sample_size else None,
        "odds": None, "form": None, "goals": None, "ai": None}, weights=weights)
    reasons = no_bet_reasons(sample_size=sample_size, value=value.get("value"),
        data_status="HEALTHY" if selection else "PARTIAL",
        odds_quality=all(odds > 1 for odds in market_odds.values()))
    if sample_size < min_sample:
        reasons.append("HISTORICAL_SAMPLE_INSUFFICIENT")
    if model_probability is None:
        reasons.append("MODEL_PROBABILITY_UNAVAILABLE")
    if ai.status == "AI_INVALID_OUTPUT":
        reasons.append("AI_OUTPUT_INVALID")
    signal = "SIGNAL" if not reasons else "NO BET"
    return {"analysis_status": "ANALYZED" if model_probability is not None else "HISTORY_INSUFFICIENT",
        "analysis_timestamp": timestamp, "model_version": MODEL_VERSION, "feature_version": FEATURE_VERSION,
        "statistics": stats, "odds": {"status": "AVAILABLE", "bookmaker": bookmaker,
            "market_odds": market_odds, "live_count": len(live_odds)},
        "historical": {"status": similarity["evidence"], **similarity}, "selection": selection,
        "model_probability": model_probability, "market_probability": value.get("no_vig_market_probability"),
        "value": value, "ai": {"status": ai.status, "provider": ai.provider, "output": ai.output},
        "combined": combined, "final": {"signal": signal, "score": combined["score"],
            "confidence": similarity["evidence"], "risk": "LOW" if signal == "SIGNAL" else "HIGH",
            "reasons": reasons}, "feature_snapshot": evidence}


def _unavailable(status: str, timestamp: datetime, reason: str) -> dict[str, Any]:
    return {"analysis_status": status, "analysis_timestamp": timestamp, "model_version": MODEL_VERSION,
            "feature_version": FEATURE_VERSION, "final": {"signal": "NO BET", "score": None,
            "confidence": "INSUFFICIENT", "risk": "UNKNOWN", "reasons": [reason]},
            "feature_snapshot": []}
