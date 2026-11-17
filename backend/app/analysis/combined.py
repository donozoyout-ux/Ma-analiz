"""Configurable, auditable score combiner; never trains or tunes weights."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping


@dataclass(frozen=True)
class ScoreWeights:
    statistical: float = .30
    odds: float = .20
    form: float = .15
    goals: float = .10
    historical: float = .15
    ai: float = .10

    def __post_init__(self) -> None:
        values = tuple(vars(self).values())
        if any(value < 0 or value > 1 for value in values) or abs(sum(values) - 1) > 1e-9:
            raise ValueError("Configured score weights must be in [0,1] and sum to 1")


def combine_scores(scores: Mapping[str, float | None], *, weights: ScoreWeights | None = None) -> dict:
    configured = weights or ScoreWeights()
    available: dict[str, float] = {}
    for key, weight in vars(configured).items():
        value = scores.get(key)
        if value is None:
            continue
        if not 0 <= value <= 100:
            raise ValueError(f"{key} score must be between 0 and 100")
        available[key] = float(value)
    total_weight = sum(getattr(configured, key) for key in available)
    if total_weight == 0:
        return {"score": None, "confidence": "INSUFFICIENT", "evidence": "MISSING",
                "components": {}, "warnings": ["NO_VERIFIED_COMPONENT_SCORES"]}
    score = sum(value * getattr(configured, key) for key, value in available.items()) / total_weight
    warnings = [f"{key.upper()}_UNAVAILABLE" for key in vars(configured) if key not in available]
    count = len(available)
    confidence = "LOW" if count < 2 else "MODERATE" if count < 4 or warnings else "HIGH"
    return {"score": round(score, 2), "confidence": confidence,
            "evidence": "PARTIAL" if warnings else "COMPLETE", "components": available,
            "weights_used": {key: getattr(configured, key) / total_weight for key in available},
            "warnings": warnings}
