import json
from datetime import datetime, timedelta, timezone
from uuid import uuid4

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.analysis.scanning import scan_upcoming_matches
from app.core.config import Settings
from app.models import ActiveMatch, ActiveOdds, MatchAnalysis


def _settings():
    return Settings(match_provider="goaloo", enabled_leagues="36", enabled_seasons="2026-2027",
                    scanner_lookahead_minutes=180, scanner_max_matches_per_run=20)


def _match(*, league="36", season="2026-2027", status="SCHEDULED", kickoff=None):
    now = datetime.now(timezone.utc)
    return ActiveMatch(id=uuid4(), provider="goaloo", provider_match_id=str(uuid4()),
        league_id=league, season=season, home_team="Home", away_team="Away",
        start_time=kickoff or now + timedelta(minutes=60), status=status,
        expires_at=now + timedelta(days=1))


def test_scanner_only_evaluates_configured_future_valid_season_fixtures(monkeypatch):
    monkeypatch.setattr("app.analysis.scanning.get_settings", _settings)
    engine = create_engine("sqlite://")
    for table in (ActiveMatch.__table__, ActiveOdds.__table__, MatchAnalysis.__table__):
        table.create(engine)
    now = datetime.now(timezone.utc)
    accepted = _match()
    rows = [accepted, _match(league="other"), _match(season="2026-2028"),
            _match(status="CANCELLED"), _match(kickoff=now - timedelta(minutes=1)),
            _match(kickoff=now + timedelta(hours=4))]
    called = []
    try:
        with Session(engine) as session:
            session.add_all(rows)
            session.commit()
            report = scan_upcoming_matches(session,
                analyze_one=lambda _db, match, **kwargs: called.append((match.id, kwargs["as_of"])) or {
                    "analysis_status": "ODDS_MISSING", "final": {"signal": "NO BET", "reasons": ["ODDS_INSUFFICIENT"]}
                }, as_of=now)
        assert len(called) == 1 and called[0][0] == accepted.id
        assert report["matches_scanned"] == 1
        assert report["no_bet"] == 1
    finally:
        engine.dispose()


def test_scanner_skips_duplicate_analysis_until_new_observed_odds(monkeypatch):
    monkeypatch.setattr("app.analysis.scanning.get_settings", _settings)
    engine = create_engine("sqlite://")
    for table in (ActiveMatch.__table__, ActiveOdds.__table__, MatchAnalysis.__table__):
        table.create(engine)
    now = datetime.now(timezone.utc)
    match = _match()
    analysis_time = now - timedelta(minutes=4)
    calls = []
    try:
        with Session(engine) as session:
            session.add(match)
            session.add(MatchAnalysis(id=uuid4(), match_id=match.id,
                analysis_timestamp=analysis_time, analysis_status="ODDS_MISSING",
                final_decision="NO BET", model_version="test", feature_version="test",
                analysis_output=json.dumps({"analysis_timestamp": analysis_time.isoformat(),
                                            "final": {"signal": "NO BET", "reasons": ["ODDS_INSUFFICIENT"]}})))
            session.commit()
            first = scan_upcoming_matches(session,
                analyze_one=lambda *_args, **_kwargs: calls.append("unexpected"), as_of=now)
            session.add(ActiveOdds(id=uuid4(), match_id=match.id, provider="goaloo",
                bookmaker="Bet365", market="1X2", selection="HOME", line=None,
                line_key="", odds=2.1, captured_at=now - timedelta(minutes=1),
                source="goaloo", period="PREMATCH", snapshot_type="LATEST",
                status="AVAILABLE", expires_at=now + timedelta(days=1)))
            session.commit()
            second = scan_upcoming_matches(session,
                analyze_one=lambda _db, _match, **_kwargs: calls.append("new_odds") or {
                    "analysis_status": "ODDS_MISSING", "final": {"signal": "NO BET", "reasons": ["ODDS_INSUFFICIENT"]}
                }, as_of=now)
        assert first["matches_skipped"] == 1
        assert calls == ["new_odds"]
        assert second["matches_scanned"] == 1
    finally:
        engine.dispose()


def test_empty_league_configuration_fails_closed(monkeypatch):
    monkeypatch.setattr("app.analysis.scanning.get_settings", lambda: Settings(match_provider="goaloo"))
    engine = create_engine("sqlite://")
    ActiveMatch.__table__.create(engine)
    try:
        with Session(engine) as session:
            result = scan_upcoming_matches(session, analyze_one=lambda *_: (_ for _ in ()).throw(
                AssertionError("no configured league should be scanned")))
        assert result["status"] == "NO_CONFIGURED_LEAGUES"
        assert result["records_written"] == 0
    finally:
        engine.dispose()


def test_unconfigured_provider_fails_closed(monkeypatch):
    monkeypatch.setattr("app.analysis.scanning.get_settings", lambda: Settings(enabled_leagues="36"))
    engine = create_engine("sqlite://")
    ActiveMatch.__table__.create(engine)
    try:
        with Session(engine) as session:
            result = scan_upcoming_matches(session, analyze_one=lambda *_: (_ for _ in ()).throw(
                AssertionError("scanner must not evaluate matches without a provider")))
        assert result["status"] == "NO_CONFIGURED_PROVIDER"
        assert result["records_written"] == 0
    finally:
        engine.dispose()
