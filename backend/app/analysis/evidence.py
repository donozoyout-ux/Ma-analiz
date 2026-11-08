from dataclasses import dataclass
from enum import StrEnum


class EvidenceStatus(StrEnum):
    INSUFFICIENT = "INSUFFICIENT"
    WEAK = "WEAK"
    MODERATE = "MODERATE"
    STRONG = "STRONG"


@dataclass(frozen=True)
class Evidence:
    feature: str
    score: float
    confidence: float
    sample_size: int
    evidence_status: EvidenceStatus


def classify_evidence(sample_size: int, raw_confidence: float) -> EvidenceStatus:
    if sample_size < 5:
        return EvidenceStatus.INSUFFICIENT
    if sample_size < 10:
        return EvidenceStatus.WEAK
    if sample_size < 25:
        return EvidenceStatus.MODERATE
    return EvidenceStatus.STRONG


def evidence_confidence(sample_size: int, raw_confidence: float) -> float:
    if not 0 <= raw_confidence <= 1:
        raise ValueError("confidence must be between zero and one")
    cap = min(0.45, sample_size / 20) if sample_size < 10 else min(0.70, sample_size / 25) if sample_size < 25 else 0.95
    return min(raw_confidence, cap)
