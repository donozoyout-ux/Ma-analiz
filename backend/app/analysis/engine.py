"""Deterministic football analysis over verified, point-in-time rows."""
from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from math import exp
from typing import Any, Iterable


@dataclass(frozen=True)
class AnalysisEvidence:
    score: float | None
    confidence: float
    sample_size: int
    evidence: str
    reasons: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _time(row: dict[str, Any]) -> datetime | None:
    value = (row.get("available_at") or row.get("verified_at") or row.get("settled_at")
             or row.get("captured_at") or row.get("finished_at") or row.get("start_time") or row.get("date"))
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    else:
        return None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        return None
    return parsed.astimezone(timezone.utc)


def _fixture_time(row: dict[str, Any]) -> datetime:
    value = row.get("start_time") or row.get("match_start") or row.get("finished_at")
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str):
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    else:
        return datetime.min.replace(tzinfo=timezone.utc)
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        return datetime.min.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def point_in_time(rows: Iterable[dict[str, Any]], as_of: datetime) -> list[dict[str, Any]]:
    """Only include dated rows available by the prediction timestamp."""
    if as_of.tzinfo is None or as_of.utcoffset() is None:
        raise ValueError("as_of must include a timezone")
    cutoff = as_of.astimezone(timezone.utc)
    return [row for row in rows if (stamp := _time(row)) is not None and stamp <= cutoff]


def _completed(rows: Iterable[dict[str, Any]], as_of: datetime) -> list[dict[str, Any]]:
    return [r for r in point_in_time(rows, as_of)
            if r.get("home_score") is not None and r.get("away_score") is not None
            and r.get("verified_at") is not None
            and str(r.get("status", "FINISHED")).upper() in {"FINISHED", "FT", "FINAL", "AET", "PEN"}]


def _evidence(sample: int, *, score: float | None, reasons: list[str], warnings: list[str] | None = None) -> AnalysisEvidence:
    status = "INSUFFICIENT" if sample < 5 else "WEAK" if sample < 10 else "MODERATE" if sample < 25 else "STRONG"
    cap = 0 if sample == 0 else min(.95, sample / 25) if sample >= 10 else min(.45, sample / 10)
    return AnalysisEvidence(score, cap, sample, status, tuple(reasons), tuple(warnings or ()))


def form_analyzer(team: str, matches: Iterable[dict[str, Any]], *, as_of: datetime,
                  limit: int = 5, venue: str | None = None) -> AnalysisEvidence:
    if limit not in {5, 10, 15}:
        raise ValueError("form window must be 5, 10, or 15")
    candidates = []
    for row in _completed(matches, as_of):
        is_home = row.get("home_team") == team
        if not is_home and row.get("away_team") != team:
            continue
        if venue and ((venue == "home") != is_home):
            continue
        candidates.append((_fixture_time(row), row, is_home))
    candidates.sort(key=lambda item: item[0], reverse=True)
    recent = candidates[:limit]
    points = 0
    for _, row, home in recent:
        scored, conceded = (int(row["home_score"]), int(row["away_score"])) if home else (int(row["away_score"]), int(row["home_score"]))
        points += 3 if scored > conceded else 1 if scored == conceded else 0
    score = points / (3 * len(recent)) if recent else None
    name = f"{team} {venue + ' ' if venue else ''}last {limit} form"
    return _evidence(len(recent), score=score, reasons=[f"{name}: {points}/{3 * len(recent)} points"] if recent else [],
                     warnings=[] if len(recent) >= limit else [f"Only {len(recent)} of {limit} requested matches available"])


def h2h_analyzer(home: str, away: str, matches: Iterable[dict[str, Any]], *, as_of: datetime) -> AnalysisEvidence:
    rows = [r for r in _completed(matches, as_of)
            if {r.get("home_team"), r.get("away_team")} == {home, away}]
    home_wins = sum(1 for r in rows if (r["home_team"] == home and r["home_score"] > r["away_score"])
                    or (r["away_team"] == home and r["away_score"] > r["home_score"]))
    draws = sum(1 for r in rows if r["home_score"] == r["away_score"])
    score = home_wins / len(rows) if rows else None
    return _evidence(len(rows), score=score, reasons=[f"Home-side team won {home_wins}/{len(rows)} H2H matches", f"Draws: {draws}"] if rows else [],
                     warnings=["H2H sample below minimum evidence threshold"] if len(rows) < 5 else [])


def goals_analyzer(team: str, matches: Iterable[dict[str, Any]], *, as_of: datetime,
                   venue: str | None = None) -> dict[str, Any]:
    rows = []
    for row in _completed(matches, as_of):
        is_home = row.get("home_team") == team
        if not is_home and row.get("away_team") != team:
            continue
        if venue and ((venue == "home") != is_home):
            continue
        gf, ga = (int(row["home_score"]), int(row["away_score"])) if is_home else (int(row["away_score"]), int(row["home_score"]))
        rows.append((gf, ga))
    n = len(rows)
    return {"sample_size": n, "evidence": _evidence(n, score=None, reasons=[]).evidence,
            "goals_for": sum(x for x, _ in rows), "goals_against": sum(x for _, x in rows),
            "avg_goals_for": sum(x for x, _ in rows) / n if n else None,
            "avg_goals_against": sum(x for _, x in rows) / n if n else None,
            "over_1_5": sum(x + y > 1 for x, y in rows) / n if n else None,
            "over_2_5": sum(x + y > 2 for x, y in rows) / n if n else None,
            "over_3_5": sum(x + y > 3 for x, y in rows) / n if n else None,
            "btts": sum(x > 0 and y > 0 for x, y in rows) / n if n else None}


def value_analyzer(model_probability: float, decimal_odds: float, *, market_probabilities: Iterable[float] | None = None) -> dict[str, float | str | None]:
    if not 0 < model_probability < 1 or decimal_odds <= 1:
        raise ValueError("model probability must be in (0,1) and decimal odds must exceed 1")
    implied = 1 / decimal_odds
    overround = None
    no_vig_market = implied
    if market_probabilities is not None:
        probabilities = list(market_probabilities)
        if not probabilities or any(p <= 0 or p >= 1 for p in probabilities):
            raise ValueError("market probabilities must be non-empty values in (0,1)")
        total = sum(probabilities)
        overround = total - 1
        no_vig_market = implied / total
    value = model_probability * decimal_odds - 1
    return {"model_probability": model_probability, "implied_probability": implied,
            "no_vig_market_probability": no_vig_market, "overround": overround,
            "value": value, "status": "POTENTIAL_VALUE" if value > 0 else "NO_VALUE"}


def odds_movement(opening: float | None, latest: float | None, *, live: bool = False) -> dict[str, Any]:
    if live or opening is None or latest is None or opening <= 1 or latest <= 1:
        return {"movement_absolute": None, "movement_percent": None, "direction": None,
                "status": "LIVE_EXCLUDED" if live else "INSUFFICIENT_ODDS"}
    delta = latest - opening
    pct = delta / opening * 100
    direction = "SHORTENING" if pct < -0.1 else "DRIFTING" if pct > 0.1 else "STABLE"
    return {"movement_absolute": delta, "movement_percent": pct, "direction": direction, "status": "AVAILABLE"}


def historical_similarity(target: dict[str, Any], matches: Iterable[dict[str, Any]], *, as_of: datetime,
                           thresholds: "SimilarityThresholds | None" = None) -> dict[str, Any]:
    """Compare only observed, shared features from verified outcomes."""
    thresholds = thresholds or SimilarityThresholds()
    history = _completed(matches, as_of)
    scored: list[tuple[float, dict[str, Any]]] = []
    scales = {"home_strength": 1, "away_strength": 1, "form": 1, "home_form": 1, "away_form": 1,
              "home_odds": 2, "draw_odds": 2, "away_odds": 2, "opening_home_odds": 2,
              "closing_home_odds": 2, "odds_movement": 100, "avg_goals": 4,
              "expected_goals": 4, "ou_line": 5, "btts_rate": 1}
    for row in history:
        features = 0
        distance = 0.0
        if target.get("league_id") and row.get("league_id"):
            features += 1
            distance += 0 if str(target["league_id"]) == str(row["league_id"]) else 1
        for feature, scale in scales.items():
            left, right = target.get(feature), row.get(feature)
            if left is None or right is None:
                continue
            try:
                delta = abs(float(left) - float(right)) / scale
            except (TypeError, ValueError, OverflowError):
                continue
            features += 1
            distance += min(delta, 1)
        if features >= 2:
            scored.append((exp(-distance / features), row))
    similar = [(score, row) for score, row in scored if score >= .5]
    n = len(similar)
    if not n:
        return {"sample_size": 0, "evidence": "INSUFFICIENT", "distribution": None, "warning": "HISTORICAL_EVIDENCE_INSUFFICIENT"}
    home = draw = away = 0
    total_weight = 0.0
    for weight, row in similar:
        total_weight += weight
        if row["home_score"] > row["away_score"]: home += weight
        elif row["home_score"] == row["away_score"]: draw += weight
        else: away += weight
    status = ("INSUFFICIENT" if n < thresholds.weak else "WEAK" if n < thresholds.moderate
              else "MODERATE" if n < thresholds.strong else "STRONG")
    return {"sample_size": n, "evidence": status,
            "distribution": {"HOME": home / total_weight, "DRAW": draw / total_weight, "AWAY": away / total_weight},
            "home_rate": home / total_weight, "draw_rate": draw / total_weight, "away_rate": away / total_weight,
            "similarity_score": sum(score for score, _ in similar) / n,
            "warning": None if status != "INSUFFICIENT" else "HISTORICAL_EVIDENCE_INSUFFICIENT"}


@dataclass(frozen=True)
class SimilarityThresholds:
    weak: int = 30
    moderate: int = 100
    strong: int = 500

    def __post_init__(self) -> None:
        if self.weak < 1 or self.moderate <= self.weak or self.strong <= self.moderate:
            raise ValueError("Similarity thresholds must be positive and strictly increasing")


def no_bet_reasons(*, sample_size: int, value: float | None, data_status: str = "HEALTHY",
                   stale: bool = False, disagreement: bool = False, odds_quality: bool = True) -> list[str]:
    reasons = []
    if sample_size < 10: reasons.append("INSUFFICIENT_SAMPLE")
    if value is None or value <= 0: reasons.append("NO_POSITIVE_VALUE")
    if data_status != "HEALTHY": reasons.append(f"DATA_{data_status}")
    if stale: reasons.append("STALE_DATA")
    if disagreement: reasons.append("MODEL_DISAGREEMENT")
    if not odds_quality: reasons.append("POOR_ODDS_QUALITY")
    return reasons


def scan_matches(matches: Iterable[dict[str, Any]], *, as_of: datetime) -> list[dict[str, Any]]:
    """Report data readiness, never fabricated picks."""
    if as_of.tzinfo is None or as_of.utcoffset() is None:
        raise ValueError("as_of must include a timezone")
    cutoff = as_of.astimezone(timezone.utc)
    results = []
    for match in matches:
        # kickoff is a future event time, not an ingestion time; keep the fixture.
        available = match.get("ingested_at") or match.get("created_at") or match.get("updated_at")
        if available is not None and _time({"captured_at": available}) is None:
            continue
        if available is not None and _time({"captured_at": available}) > cutoff:
            continue
        missing = [field for field in ("home_team", "away_team", "start_time") if not match.get(field)]
        odds = [o for o in match.get("odds", []) if str(o.get("period", "")).upper() in {"PREMATCH", "PRE-MATCH"}
                and _time(o) is not None and _time(o) <= cutoff]
        results.append({"match_id": match.get("id") or match.get("provider_match_id"),
                        "status": "READY_FOR_ANALYSIS" if not missing and odds else "DATA_UNAVAILABLE",
                        "missing_fields": missing, "odds_count": len(odds),
                        "signal": "NO BET", "reason": "No validated analysis model output"})
    return results
