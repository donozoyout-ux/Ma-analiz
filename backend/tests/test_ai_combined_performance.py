from datetime import datetime, timedelta, timezone

import pytest

from app.analysis.ai import analyze_with_ai
from app.analysis.combined import ScoreWeights, combine_scores
from app.analysis.performance import performance_metrics


class StubAI:
    name = "test"

    def __init__(self, output):
        self.output = output

    def analyze_match(self, facts):
        return self.output


def _ai_output(evidence_id="form-1"):
    statement = {"text": "The supplied sample is limited", "evidence_ids": [evidence_id]}
    return {"summary": statement, "conclusion": statement, "strengths": [], "weaknesses": [],
            "positive_signals": [], "negative_signals": [], "risks": [], "ai_confidence": .4}


def test_ai_is_optional_and_citations_must_reference_input_evidence():
    assert analyze_with_ai(None, {}) .status == "AI_UNAVAILABLE"
    facts = {"evidence": [{"id": "form-1", "sample_size": 4}]}
    assert analyze_with_ai(StubAI(_ai_output()), facts).status == "AVAILABLE"
    assert analyze_with_ai(StubAI(_ai_output("invented")), facts).status == "AI_INVALID_OUTPUT"


def test_combined_model_reports_missing_components_and_renormalizes_without_tuning():
    result = combine_scores({"statistical": 80, "odds": None, "ai": None})
    assert result["score"] == 80
    assert result["evidence"] == "PARTIAL"
    assert "AI_UNAVAILABLE" in result["warnings"]
    with pytest.raises(ValueError):
        ScoreWeights(statistical=.5, odds=.5, form=0, goals=0, historical=0, ai=.1)


def test_performance_validates_point_in_time_and_scores_paper_only():
    now = datetime(2026, 11, 16, tzinfo=timezone.utc)
    row = {"analysis_timestamp": now, "feature_snapshot": [{"available_at": now - timedelta(minutes=1)}],
           "outcome": True, "probability": .7, "odds": 2, "paper_stake": 5}
    result = performance_metrics([row])
    assert result["sample_size"] == 1
    assert result["brier_score"] == pytest.approx(.09)
    assert result["roi"] == pytest.approx(1)
    row["feature_snapshot"] = [{"available_at": now + timedelta(seconds=1)}]
    with pytest.raises(ValueError, match="LOOKAHEAD_VIOLATION"):
        performance_metrics([row])
