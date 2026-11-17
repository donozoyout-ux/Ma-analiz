"""Point-in-time model metrics and fixed-model ablation summaries."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Iterable


MODEL_VARIANTS = ("STATISTICAL_ONLY", "ODDS_ONLY", "STATISTICS_ODDS", "STATISTICS_ODDS_HISTORICAL", "COMBINED")


def validate_prediction_snapshot(prediction: dict[str, Any]) -> None:
    at = prediction.get("analysis_timestamp") or prediction.get("created_at")
    if not isinstance(at, datetime) or at.tzinfo is None or at.utcoffset() is None:
        raise ValueError("Prediction requires a timezone-aware analysis timestamp")
    for feature in prediction.get("feature_snapshot", []):
        available = feature.get("available_at")
        if not isinstance(available, datetime) or available.tzinfo is None or available.utcoffset() is None:
            raise ValueError("Every feature requires a timezone-aware available_at timestamp")
        if available.astimezone(timezone.utc) > at.astimezone(timezone.utc):
            raise ValueError("LOOKAHEAD_VIOLATION: feature was not available at prediction time")


def performance_metrics(rows: Iterable[dict[str, Any]]) -> dict[str, Any]:
    settled = []
    for row in rows:
        validate_prediction_snapshot(row)
        if row.get("outcome") is None:
            continue
        probability = float(row["probability"])
        if not 0 <= probability <= 1:
            raise ValueError("probability must be in [0,1]")
        settled.append((row, probability, bool(row["outcome"])))
    n = len(settled)
    if not n:
        return {"sample_size": 0, "accuracy": None, "precision": None, "recall": None,
                "brier_score": None, "roi": None, "yield": None, "clv": None}
    tp = sum(p >= .5 and outcome for _, p, outcome in settled)
    fp = sum(p >= .5 and not outcome for _, p, outcome in settled)
    fn = sum(p < .5 and outcome for _, p, outcome in settled)
    correct = tp + sum(p < .5 and not outcome for _, p, outcome in settled)
    brier = sum((p - int(outcome)) ** 2 for _, p, outcome in settled) / n
    profit = 0.0
    stake_total = 0.0
    for row, _, outcome in settled:
        stake, odds = float(row.get("paper_stake", 1)), float(row["odds"])
        if stake <= 0 or odds <= 1:
            raise ValueError("paper stake and settlement odds must be positive valid values")
        stake_total += stake
        profit += stake * (odds - 1) if outcome else -stake
    return {"sample_size": n, "accuracy": correct / n,
            "precision": tp / (tp + fp) if tp + fp else None,
            "recall": tp / (tp + fn) if tp + fn else None,
            "brier_score": brier, "roi": profit / stake_total if stake_total else None,
            "yield": profit / stake_total if stake_total else None,
            "clv": None, "clv_status": "UNAVAILABLE_WITHOUT_VERIFIED_CLOSING_ODDS"}


def compare_models(predictions: Iterable[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    groups: dict[str, list[dict[str, Any]]] = {name: [] for name in MODEL_VARIANTS}
    for row in predictions:
        model = str(row.get("model_variant", "")).upper()
        if model not in groups:
            continue
        groups[model].append(row)
    return {name: performance_metrics(rows) for name, rows in groups.items()}
