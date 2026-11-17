"""Optional AI interpretation constrained to supplied deterministic evidence."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator
from app.core.config import get_settings


class AIUnavailable(RuntimeError):
    pass


class CitedStatement(BaseModel):
    model_config = ConfigDict(extra="forbid")
    text: str = Field(min_length=1, max_length=600)
    evidence_ids: list[str] = Field(min_length=1, max_length=10)


class MatchAIOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    summary: CitedStatement
    strengths: list[CitedStatement] = Field(default_factory=list, max_length=8)
    weaknesses: list[CitedStatement] = Field(default_factory=list, max_length=8)
    positive_signals: list[CitedStatement] = Field(default_factory=list, max_length=8)
    negative_signals: list[CitedStatement] = Field(default_factory=list, max_length=8)
    risks: list[CitedStatement] = Field(default_factory=list, max_length=8)
    conclusion: CitedStatement
    ai_confidence: float = Field(ge=0, le=1)


class AIProvider(Protocol):
    name: str

    def analyze_match(self, facts: dict[str, Any]) -> dict[str, Any]: ...
    def scan_patterns(self, patterns: list[dict[str, Any]]) -> dict[str, Any]: ...
    def summarize_analysis(self, facts: dict[str, Any]) -> dict[str, Any]: ...
    def generate_lesson(self, metrics: dict[str, Any]) -> dict[str, Any]: ...


@dataclass(frozen=True)
class AIAnalysis:
    status: str
    provider: str | None
    output: dict[str, Any] | None
    error: str | None = None


def analyze_with_ai(provider: AIProvider | None, facts: dict[str, Any]) -> AIAnalysis:
    if provider is None:
        return AIAnalysis("AI_UNAVAILABLE", None, None, "AI provider is not configured")
    evidence = facts.get("evidence")
    if not isinstance(evidence, list) or any(not isinstance(x, dict) or not x.get("id") for x in evidence):
        raise ValueError("AI input requires identified deterministic evidence rows")
    allowed = {str(item["id"]) for item in evidence}
    try:
        result = MatchAIOutput.model_validate(provider.analyze_match(facts))
        citations = [result.summary, result.conclusion]
        citations.extend(citation for group in (result.strengths, result.weaknesses, result.positive_signals,
                                                 result.negative_signals, result.risks) for citation in group)
        unknown = {fact_id for item in citations for fact_id in item.evidence_ids} - allowed
        if unknown:
            raise ValueError("AI output cites evidence IDs absent from its input")
    except (ValidationError, ValueError):
        return AIAnalysis("AI_INVALID_OUTPUT", provider.name, None, "AI output failed evidence validation")
    except Exception as exc:
        status = "AI_INVALID_OUTPUT" if "AI_INVALID_OUTPUT" in str(exc) else "AI_UNAVAILABLE"
        return AIAnalysis(status, provider.name, None, f"{status}: {type(exc).__name__}")
    return AIAnalysis("AVAILABLE", provider.name, result.model_dump(), None)


def create_ai_provider() -> AIProvider | None:
    """Return only explicitly configured providers; never put credentials in API output."""
    cfg = get_settings()
    if cfg.groq_configured:
        from app.analysis.groq import GroqProvider
        key = cfg.groq_api_key if (cfg.groq_api_key or "").strip() else cfg.ai_api_key
        return GroqProvider(api_key=key or "", model=cfg.groq_model or "", base_url=cfg.groq_base_url)
    return None
