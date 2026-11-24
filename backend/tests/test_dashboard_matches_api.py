"""Dashboard contract coverage using an isolated in-memory database only."""
from datetime import datetime, timedelta, timezone
from uuid import uuid4

from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event, select, func
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.main import app
from app.db.session import get_db
from app.models import ActiveMatch, MatchResult, MatchAnalysis


def test_match_list_preserves_fields_and_adds_real_score_and_latest_analysis():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    for table in (ActiveMatch.__table__, MatchResult.__table__, MatchAnalysis.__table__):
        table.create(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    now = datetime.now(timezone.utc)
    match_id, empty_id, expired_id = uuid4(), uuid4(), uuid4()
    with factory() as db:
        for key, status, expiry in [(match_id, "FINISHED", 1), (empty_id, "SCHEDULED", 1), (expired_id, "FINISHED", -1)]:
            db.add(ActiveMatch(id=key, provider="test", provider_match_id=str(key),
                league_name="Test league", home_team="Test home", away_team="Test away",
                start_time=now-timedelta(hours=2), status=status, expires_at=now+timedelta(days=expiry)))
        db.add(MatchResult(match_id=match_id, provider="test", home_score=0, away_score=0,
            status="FINAL", verified_at=now-timedelta(minutes=1), result_source="test-provider"))
        for delta, status in [(-60, "HISTORY_INSUFFICIENT"), (-30, "ANALYZED"), (60, "FAILED")]:
            db.add(MatchAnalysis(match_id=match_id, analysis_timestamp=now+timedelta(minutes=delta),
                analysis_status=status, final_decision="NO BET", model_version="test-v1",
                feature_version="test-v1", analysis_output="{}"))
        db.commit()
    def dependency():
        with factory() as db:
            yield db
    app.dependency_overrides[get_db] = dependency
    statements = []
    def capture(conn, cursor, statement, parameters, context, executemany):
        statements.append(statement)
    try:
        event.listen(engine, "before_cursor_execute", capture)
        response = TestClient(app).get("/api/v1/matches?limit=500")
        assert response.status_code == 200
        rows = response.json()
        assert len(rows) == 2
        assert len(statements) == 3  # No request per match and no database writes.
        assert all(statement.lstrip().upper().startswith("SELECT") for statement in statements)
        result = next(row for row in rows if row["id"] == str(match_id))
        assert result["home_team"] == "Test home" and result["provider"] == "test"
        assert result["result"]["home_score"] == 0 and result["result"]["away_score"] == 0
        assert result["result"]["verified_at"] and result["result"]["result_source"] == "test-provider"
        assert result["analysis_summary"]["analysis_status"] == "ANALYZED"
        assert result["analysis_summary"]["signal"] == "NO BET"
        empty = next(row for row in rows if row["id"] == str(empty_id))
        assert empty["result"] is None and empty["analysis_summary"] is None
        assert len(TestClient(app).get("/api/v1/matches?limit=1").json()) == 1
        assert TestClient(app).get("/api/matches").status_code == 200
        with factory() as db:
            assert db.scalar(select(func.count()).select_from(MatchResult)) == 1
            assert db.scalar(select(func.count()).select_from(MatchAnalysis)) == 3
    finally:
        app.dependency_overrides.pop(get_db, None)
        engine.dispose()
