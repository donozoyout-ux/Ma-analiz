"""Idempotent storage for analysis runs and virtual paper predictions."""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.analysis.performance import validate_prediction_snapshot
from app.models import MatchAnalysis, Prediction


def record_analysis(session: Session, *, match_id: UUID, result: dict[str, Any]) -> MatchAnalysis:
    timestamp = result.get("analysis_timestamp")
    if not isinstance(timestamp, datetime) or timestamp.tzinfo is None or timestamp.utcoffset() is None:
        raise ValueError("Analysis record requires a timezone-aware timestamp")
    model_version = str(result.get("model_version") or "unknown")
    existing = session.scalar(select(MatchAnalysis).where(
        MatchAnalysis.match_id == match_id,
        MatchAnalysis.analysis_timestamp == timestamp,
        MatchAnalysis.model_version == model_version,
    ))
    if existing:
        return existing
    row = MatchAnalysis(match_id=match_id, analysis_timestamp=timestamp,
        analysis_status=str(result.get("analysis_status", "FAILED")),
        final_decision=str((result.get("final") or {}).get("signal", "NO BET")),
        model_version=model_version, feature_version=str(result.get("feature_version") or "canonical-1"),
        analysis_output=json.dumps(result, ensure_ascii=False, default=str, separators=(",", ":")))
    try:
        with session.begin_nested():
            session.add(row)
            session.flush()
    except IntegrityError:
        existing = session.scalar(select(MatchAnalysis).where(
            MatchAnalysis.match_id == match_id,
            MatchAnalysis.analysis_timestamp == timestamp,
            MatchAnalysis.model_version == model_version,
        ))
        if existing is None:
            raise
        return existing
    return row


def persist_paper_prediction(session: Session, *, match_id: UUID, kickoff: datetime,
                             result: dict[str, Any]) -> Prediction | None:
    final = result.get("final") or {}
    selection = result.get("selection")
    probability = result.get("model_probability")
    value = result.get("value") or {}
    odds = (result.get("odds") or {}).get("market_odds") or {}
    if final.get("signal") != "SIGNAL" or not selection or probability is None or value.get("value") is None:
        return None
    timestamp = result.get("analysis_timestamp")
    if not isinstance(timestamp, datetime) or timestamp.tzinfo is None or timestamp.utcoffset() is None:
        raise ValueError("Paper prediction requires a timezone-aware analysis timestamp")
    decimal_odds = odds.get(selection)
    market_probability = value.get("no_vig_market_probability", result.get("market_probability"))
    if decimal_odds is None or market_probability is None:
        raise ValueError("Paper prediction requires verified odds and market probability")
    version = str(result.get("model_version") or "unknown")
    existing = session.scalar(select(Prediction).where(
        Prediction.match_id == match_id, Prediction.market == "1X2",
        Prediction.selection == selection, Prediction.model_version == version))
    if existing:
        return existing
    similarity = result.get("historical") or {}
    validate_prediction_snapshot({"analysis_timestamp": timestamp,
                                  "feature_snapshot": result.get("feature_snapshot", [])})
    confidence = min(Decimal("0.95"), Decimal(str(similarity.get("similarity_score") or 0)))
    final_score = final.get("score")
    prediction = Prediction(match_id=match_id, market="1X2", selection=selection,
        probability=Decimal(str(probability)), confidence=confidence,
        value_score=Decimal(str(value["value"])), evidence_status=str(similarity.get("evidence", "INSUFFICIENT")),
        model_version=version, decimal_odds=Decimal(str(decimal_odds)),
        market_probability=Decimal(str(market_probability)),
        final_score=Decimal(str(final_score)) if final_score is not None else None,
        risk_level=str(final.get("risk", "UNKNOWN")), analysis_timestamp=timestamp,
        feature_version=str(result.get("feature_version") or "canonical-1"),
        feature_snapshot=json.dumps(result.get("feature_snapshot", []), ensure_ascii=False,
                                    default=str, separators=(",", ":")),
        expires_at=kickoff + timedelta(days=30))
    try:
        with session.begin_nested():
            session.add(prediction)
            session.flush()
    except IntegrityError:
        return session.scalar(select(Prediction).where(
            Prediction.match_id == match_id, Prediction.market == "1X2",
            Prediction.selection == selection, Prediction.model_version == version))
    return prediction
