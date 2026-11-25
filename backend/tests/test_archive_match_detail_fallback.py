"""No production secrets or data deletion: archived match-detail HTTP contract."""
from datetime import datetime, timedelta, timezone
from uuid import uuid4

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.main import app
from app.db.session import get_db
from app.models import ActiveMatch


def _client(monkeypatch, archived=None, error=None, sql_match=False):
    from app.api import routes
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    ActiveMatch.__table__.create(engine)
    factory = sessionmaker(bind=engine)
    match_id = uuid4()
    if sql_match:
        with factory() as db:
            db.add(ActiveMatch(id=match_id, provider="test", provider_match_id=str(match_id),
                league_id="de.bl1", home_team="SQL HOME", away_team="SQL AWAY",
                start_time=datetime.now(timezone.utc), status="FINISHED",
                expires_at=datetime.now(timezone.utc)+timedelta(hours=32)))
            db.commit()

    def dependency():
        with factory() as db:
            yield db

    class Repository:
        def __init__(self, store):
            pass

        def get_verified_match_from_archive(self, session, identifier):
            assert identifier == match_id
            if error:
                raise error
            return archived

    monkeypatch.setattr(routes, "R2ObjectStore", lambda: object())
    monkeypatch.setattr(routes, "HistoricalRepository", Repository)
    app.dependency_overrides[get_db] = dependency
    return TestClient(app), match_id, engine


def test_missing_sql_reads_only_verification_complete_archive(monkeypatch):
    match_id = uuid4()
    archived = {"match": {"id": str(match_id), "home_team": "R2 HOME",
                          "away_team": "R2 AWAY", "status": "FINISHED"},
                "result": {"status": "FINAL", "home_score": 2, "away_score": 1,
                           "verified_at": "2026-11-24T20:00:00+00:00",
                           "result_source": "independent"}}
    client, generated, engine = _client(monkeypatch, archived=archived)
    archived["match"]["id"] = str(generated)
    try:
        result = client.get(f"/api/v1/matches/{generated}")
        assert result.status_code == 200
        data = result.json()
        assert data["archive_source"] == "R2_VERIFIED"
        assert data["result"]["home_score"] == 2
        assert data["odds"] == [] and data["live_odds"] == []
    finally:
        app.dependency_overrides.clear()
        engine.dispose()


def test_unverified_or_missing_archive_returns_404(monkeypatch):
    client, match_id, engine = _client(monkeypatch, archived=None)
    try:
        result = client.get(f"/api/v1/matches/{match_id}")
        assert result.status_code == 404
        assert result.json()["detail"] == "MATCH_NOT_FOUND"
    finally:
        app.dependency_overrides.clear()
        engine.dispose()


def test_corrupt_archive_returns_safe_503(monkeypatch):
    client, match_id, engine = _client(monkeypatch, error=IOError("SECRET_PRIVATE_R2_KEY"))
    try:
        result = client.get(f"/api/v1/matches/{match_id}")
        assert result.status_code == 503
        assert result.json()["detail"] == "HISTORICAL_ARCHIVE_UNAVAILABLE"
        assert "SECRET" not in result.text
    finally:
        app.dependency_overrides.clear()
        engine.dispose()


def test_sql_match_keeps_existing_detail_path(monkeypatch):
    client, match_id, engine = _client(monkeypatch, error=IOError("R2 SHOULD NOT RUN"), sql_match=True)
    try:
        result = client.get(f"/api/v1/matches/{match_id}")
        assert result.status_code == 200
        assert result.json()["home_team"] == "SQL HOME"
        assert "archive_source" not in result.json()
    finally:
        app.dependency_overrides.clear()
        engine.dispose()
