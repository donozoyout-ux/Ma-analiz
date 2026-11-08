from dataclasses import dataclass

from app.analysis.prediction import PredictionDraft


@dataclass(frozen=True)
class PaperCoupon:
    kind: str
    items: tuple[PredictionDraft, ...]
    total_odds: float
    confidence: float
    status: str = "PAPER_PENDING"


def build_coupon(predictions: list[tuple[str, float, PredictionDraft]], *, kind: str,
                 confidence_floor: float = .6, value_floor: float = .02,
                 max_items: int = 4) -> PaperCoupon | None:
    if kind not in {"SAFE", "BALANCED", "VALUE"}:
        raise ValueError("Unsupported coupon type")
    if max_items < 1:
        raise ValueError("max_items must be positive")
    if not (0 <= confidence_floor <= 1 and 0 <= value_floor <= 1):
        raise ValueError("thresholds must be between zero and one")
    selected: list[tuple[str, float, PredictionDraft]] = []
    used_matches: set[str] = set()
    for match_id, odds, prediction in sorted(predictions, key=lambda p: p[2].confidence, reverse=True):
        if match_id in used_matches or odds <= 1:
            continue
        if prediction.confidence < confidence_floor or prediction.value_score < value_floor:
            continue
        if prediction.evidence_status.value not in {"MODERATE", "STRONG"}:
            continue
        selected.append((match_id, odds, prediction)); used_matches.add(match_id)
        if len(selected) >= max_items:
            break
    if not selected:
        return None
    items = tuple(item[2] for item in selected)
    product = 1.0
    for _, odds, _ in selected:
        product *= odds
    return PaperCoupon(kind, items, product, min(p.confidence for p in items))
