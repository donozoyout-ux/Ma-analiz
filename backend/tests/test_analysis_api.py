from datetime import datetime, timedelta, timezone
from uuid import uuid4

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.db.base import Base
from app.db.session import get_db
from app.main import app
from app.models import ActiveMatch, ActiveOdds, MatchAnalysis, MatchResult


def test_analysis_api_uses_only_verified_past_results_and_scanner_defaults_to_no_bet():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    for table in (ActiveMatch.__table__, ActiveOdds.__table__, MatchResult.__table__, MatchAnalysis.__table__):
        table.create(engine)
    session_factory = sessionmaker(bind=engine, expire_on_commit=False)
    now = datetime.now(timezone.utc)
    match_id = uuid4()
    past_id = uuid4()

    def dependency():
        with session_factory() as session:
            yield session

    app.dependency_overrides[get_db] = dependency
    try:
        with session_factory() as session:
            session.add(ActiveMatch(id=match_id, provider="test", provider_match_id="upcoming",
                league_id="L", home_team="Home", away_team="Away", start_time=now + timedelta(hours=2),
                status="SCHEDULED", expires_at=now + timedelta(days=1)))
            session.add(ActiveMatch(id=past_id, provider="test", provider_match_id="past",
                league_id="L", home_team="Home", away_team="Other", start_time=now - timedelta(days=1),
                status="FINISHED", expires_at=now + timedelta(days=1)))
            session.add(MatchResult(match_id=past_id, provider="test", home_score=2, away_score=0,
                status="FINISHED", result_source="verified-test", verified_at=now - timedelta(hours=1)))
            session.commit()
        client = TestClient(app)
        stats = client.get(f"/api/v1/analysis/{match_id}/statistics")
        assert stats.status_code == 200
        assert stats.json()["home"]["form_5"]["sample_size"] == 1
        analysis = client.get(f"/api/analysis/{match_id}")
        assert analysis.status_code == 200
        assert analysis.json()["final"]["signal"] == "NO BET"
        scan = client.get("/api/scanner/results")
        assert scan.status_code == 200
        assert scan.json()[0]["signal"] == "NO BET"
        assert scan.json()[0]["status"] == "DATA_UNAVAILABLE"
        run = client.post(f"/api/v1/analysis/{match_id}/run")
        assert run.status_code == 200
        assert run.json()["analysis_status"] == "ODDS_MISSING"
        assert client.get(f"/api/analysis/{match_id}").json()["analysis_status"] == "ODDS_MISSING"
        scanner = client.post("/api/scanner/run")
        assert scanner.status_code == 200
        assert scanner.json()["matches_scanned"] == 1
    finally:
        app.dependency_overrides.pop(get_db, None)
        for table in (MatchAnalysis.__table__, MatchResult.__table__, ActiveOdds.__table__, ActiveMatch.__table__):
            table.drop(engine)
        engine.dispose()
