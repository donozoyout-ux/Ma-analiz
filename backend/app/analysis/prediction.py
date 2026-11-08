from dataclasses import dataclass

from app.analysis.evidence import EvidenceStatus, classify_evidence, evidence_confidence


@dataclass(frozen=True)
class PredictionDraft:
    market: str
    selection: str
    probability: float
    confidence: float
    sample_size: int
    evidence_status: EvidenceStatus
    value_score: float
    risk_level: str
    model_version: str = "baseline-1"


def make_prediction(*, market: str, selection: str, probability: float, raw_confidence: float,
                    sample_size: int, decimal_odds: float) -> PredictionDraft | None:
    if not (0 < probability < 1 and decimal_odds > 1):
        raise ValueError("Probability must be in (0,1) and decimal odds must exceed 1")
    status = classify_evidence(sample_size, raw_confidence)
    if status == EvidenceStatus.INSUFFICIENT:
        return None
    confidence = evidence_confidence(sample_size, raw_confidence)
    value = probability - (1 / decimal_odds)
    risk = "HIGH" if confidence < .45 else "MEDIUM" if confidence < .7 else "LOW"
    return PredictionDraft(market, selection, probability, confidence, sample_size, status, value, risk)
