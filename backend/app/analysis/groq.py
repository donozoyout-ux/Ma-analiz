"""Groq Chat Completions adapter; output remains advisory and evidence-bound."""
from __future__ import annotations

import json
from typing import Any

import httpx
from pydantic import BaseModel, ConfigDict, Field

from app.analysis.ai import MatchAIOutput


class LessonOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    classification: str = Field(pattern="^(OBSERVATION|PROPOSAL)$")
    statement: str = Field(min_length=1, max_length=1000)
    evidence_ids: list[str] = Field(min_length=1, max_length=20)


class GroqProvider:
    name = "groq"

    def __init__(self, *, api_key: str, model: str, base_url: str = "https://api.groq.com/openai/v1",
                 timeout_seconds: float = 30, client: httpx.Client | None = None):
        if not api_key.strip() or not model.strip() or timeout_seconds <= 0:
            raise ValueError("Groq API key, model, and positive timeout are required")
        self._api_key, self.model = api_key.strip(), model.strip()
        self.base_url = base_url.rstrip("/")
        self._client = client or httpx.Client(timeout=timeout_seconds)
        self._owns_client = client is None

    def close(self) -> None:
        if self._owns_client:
            self._client.close()

    def _chat(self, instruction: str, data: dict[str, Any]) -> dict[str, Any]:
        request = {"model": self.model, "temperature": 0.1, "response_format": {"type": "json_object"},
                   "messages": [{"role": "system", "content": instruction},
                                {"role": "user", "content": json.dumps(data, ensure_ascii=False, default=str)}]}
        try:
            response = self._client.post(f"{self.base_url}/chat/completions", json=request,
                headers={"Authorization": f"Bearer {self._api_key}", "Content-Type": "application/json"})
        except httpx.HTTPError:
            raise RuntimeError("AI_UNAVAILABLE: Groq request failed") from None
        if not response.is_success:
            raise RuntimeError(f"AI_UNAVAILABLE: Groq returned HTTP {response.status_code}")
        try:
            envelope = response.json()
            content = envelope["choices"][0]["message"]["content"]
            result = json.loads(content)
        except (ValueError, KeyError, IndexError, TypeError):
            raise RuntimeError("AI_INVALID_OUTPUT: Groq response envelope was invalid") from None
        if not isinstance(result, dict):
            raise RuntimeError("AI_INVALID_OUTPUT: Groq response was not a JSON object")
        return result

    def analyze_match(self, facts: dict[str, Any]) -> dict[str, Any]:
        output = self._chat(
            "Interpret only supplied evidence. Do not invent statistics, odds, samples, or conclusions. "
            "Return JSON with summary and conclusion as {text,evidence_ids}; strengths, weaknesses, "
            "positive_signals, negative_signals, risks as arrays of {text,evidence_ids}; ai_confidence 0..1. "
            "Every statement must cite IDs present in the input. Never recommend a bet as certain.", facts)
        return MatchAIOutput.model_validate(output).model_dump()

    def summarize_analysis(self, facts: dict[str, Any]) -> dict[str, Any]:
        return self.analyze_match(facts)

    def scan_patterns(self, patterns: list[dict[str, Any]]) -> dict[str, Any]:
        evidence_ids = {str(item.get("id")) for item in patterns if item.get("id") is not None}
        result = self._chat(
            "Summarize only the supplied deterministic patterns. Do not change metrics or add patterns. "
            "Return JSON {patterns:[{pattern_id,description,evidence_ids}]}; cite only supplied pattern IDs.",
            {"patterns": patterns})
        rows = result.get("patterns")
        if not isinstance(rows, list):
            raise RuntimeError("AI_INVALID_OUTPUT: pattern scan schema is invalid")
        for row in rows:
            if not isinstance(row, dict) or str(row.get("pattern_id")) not in evidence_ids:
                raise RuntimeError("AI_INVALID_OUTPUT: pattern scan referenced an unknown pattern")
            if not set(row.get("evidence_ids") or []).issubset(evidence_ids):
                raise RuntimeError("AI_INVALID_OUTPUT: pattern scan cited unknown evidence")
        return {"patterns": rows}

    def generate_lesson(self, metrics: dict[str, Any]) -> dict[str, Any]:
        allowed = {str(item["id"]) for item in metrics.get("evidence", []) if isinstance(item, dict) and item.get("id")}
        result = LessonOutput.model_validate(self._chat(
            "Summarize supplied performance measurements only. Classify as OBSERVATION or PROPOSAL. "
            "Do not recommend a production strategy change. Cite supplied evidence IDs.", metrics))
        if not set(result.evidence_ids).issubset(allowed):
            raise RuntimeError("AI_INVALID_OUTPUT: lesson cited unknown evidence")
        return result.model_dump()
