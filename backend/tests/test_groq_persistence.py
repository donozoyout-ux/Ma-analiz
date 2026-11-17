from datetime import datetime, timedelta, timezone
from decimal import Decimal
from uuid import uuid4

import httpx
import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.analysis.groq import GroqProvider
from app.analysis.persistence import persist_paper_prediction, record_analysis
from app.analysis.performance import validate_prediction_snapshot
from app.db.base import Base
from app.models import ActiveMatch, MatchAnalysis, Prediction


def test_groq_uses_json_mode_and_sanitizes_http_errors():
    secret = "test-only-key"
    output = {"summary": {"text": "Limited sample", "evidence_ids": ["f1"]},
        "conclusion": {"text": "No certainty", "evidence_ids": ["f1"]},
        "strengths": [], "weaknesses": [], "positive_signals": [], "negative_signals": [],
        "risks": [], "ai_confidence": .2}

    def handler(request):
        assert request.headers["Authorization"] == f"Bearer {secret}"
        body = request.read().decode()
        assert '"type":"json_object"' in body
        return httpx.Response(200, json={"choices": [{"message": {"content": __import__("json").dumps(output)}}], "model": "fixture"}, request=request)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    provider = GroqProvider(api_key=secret, model="fixture-model", base_url="https://groq.test/v1", client=client)
    assert provider.analyze_match({"evidence": [{"id": "f1"}]})["ai_confidence"] == .2
    client.close()


def test_analysis_and_signal_paper_prediction_are_idempotent_and_snapshot_reproducible():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    for table in (ActiveMatch.__table__, Prediction.__table__, MatchAnalysis.__table__):
        table.create(engine)
    match_id = uuid4()
    now = datetime(2026, 11, 16, 12, tzinfo=timezone.utc)
    result = {"analysis_timestamp": now, "analysis_status": "ANALYZED", "model_version": "model-1",
        "feature_version": "features-1", "feature_snapshot": [{"id": "f1", "value": .6, "available_at": now}],
        "final": {"signal": "SIGNAL", "score": 72.5, "risk": "MEDIUM"}, "selection": "HOME",
        "model_probability": .6, "market_probability": .53, "value": {"value": .2},
        "historical": {"sample_size": 120, "similarity_score": .81, "evidence": "MODERATE"},
        "odds": {"market_odds": {"HOME": 2.0, "DRAW": 3.2, "AWAY": 4.0}}}
    with Session(engine) as session:
        session.add(ActiveMatch(id=match_id, provider="test", provider_match_id="fx-1", home_team="H",
            away_team="A", start_time=now + timedelta(hours=2), status="SCHEDULED", expires_at=now + timedelta(days=5)))
        session.flush()
        first_analysis = record_analysis(session, match_id=match_id, result=result)
        again_analysis = record_analysis(session, match_id=match_id, result=result)
        assert first_analysis.id == again_analysis.id
        prediction = persist_paper_prediction(session, match_id=match_id,
            kickoff=now + timedelta(hours=2), result=result)
        again_prediction = persist_paper_prediction(session, match_id=match_id,
            kickoff=now + timedelta(hours=2), result=result)
        assert prediction.id == again_prediction.id
        session.commit()
        saved = session.get(Prediction, prediction.id)
        assert saved.analysis_timestamp.replace(tzinfo=timezone.utc) == now
        assert saved.market_probability == Decimal("0.53")
        assert '"id":"f1"' in saved.feature_snapshot
        assert session.scalar(select(func.count()).select_from(Prediction)) == 1
        no_bet = {**result, "final": {"signal": "NO BET"}}
        assert persist_paper_prediction(session, match_id=match_id, kickoff=now, result=no_bet) is None
    for table in (MatchAnalysis.__table__, Prediction.__table__, ActiveMatch.__table__):
        table.drop(engine)
    engine.dispose()


def test_prediction_snapshot_rejects_features_available_after_prediction_time():
    at = datetime(2026, 11, 16, 12, tzinfo=timezone.utc)
    with pytest.raises(ValueError, match="LOOKAHEAD_VIOLATION"):
        validate_prediction_snapshot({"analysis_timestamp": at, "feature_snapshot": [
            {"id": "future-odds", "available_at": at + timedelta(minutes=1)}]})
