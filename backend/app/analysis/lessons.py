from dataclasses import dataclass


@dataclass(frozen=True)
class StrategyProposal:
    current_parameter: str
    proposed_parameter: str
    reason: str
    evidence: str
    expected_effect: str
    status: str = "PROPOSAL"


@dataclass(frozen=True)
class LessonDraft:
    category: str
    title: str
    summary: str
    recommendation: str
    confidence: float
    evidence_r2_key: str | None = None


def generate_lessons(outcomes: list[dict], *, minimum_sample: int = 20) -> list[LessonDraft]:
    """Create conservative structured lessons from archived evaluation outcomes."""
    if len(outcomes) < minimum_sample:
        return []
    high_conf_losses = [r for r in outcomes if not r.get("won") and r.get("confidence", 0) >= .8]
    lessons = []
    if len(high_conf_losses) >= max(5, minimum_sample // 4):
        fraction = len(high_conf_losses) / len(outcomes)
        lessons.append(LessonDraft(
            category="OVERCONFIDENCE",
            title="High confidence losses need calibration",
            summary=f"{len(high_conf_losses)} of {len(outcomes)} reviewed predictions were losses with confidence at or above 0.80.",
            recommendation="Review calibration by market and league before changing confidence thresholds.",
            confidence=min(.75, fraction),
        ))
    return lessons


def propose_parameter_change(*, parameter: str, current_value: str, proposed_value: str,
                             reason: str, evidence: str, expected_effect: str) -> StrategyProposal:
    """Record a reviewable proposal; applying it requires backtest and human approval."""
    return StrategyProposal(parameter, proposed_value, reason, evidence, expected_effect)
