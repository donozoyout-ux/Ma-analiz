"""Reusable, persisted scanner operations shared by HTTP and worker callers."""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from typing import Any, Callable

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.analysis.engine import (SimilarityThresholds, form_analyzer, goals_analyzer,
    historical_similarity, h2h_analyzer, no_bet_reasons)
from app.analysis.technical import technical_summary
from app.analysis.ai import create_ai_provider
from app.analysis.pipeline import analyze_match
from app.analysis.persistence import persist_paper_prediction, record_analysis
from app.core.config import get_settings
from app.historical.repository import HistoricalRepository
from app.models import ActiveMatch, ActiveOdds, MatchAnalysis, MatchResult
from app.storage.r2 import R2NotConfigured, R2ObjectStore


def _serialize(model) -> dict:
    return {column.name: getattr(model, column.name) for column in model.__table__.columns}
def _verified_history(db: Session, *, cutoff: datetime) -> list[dict]:
    rows = db.execute(select(ActiveMatch, MatchResult).join(
        MatchResult, MatchResult.match_id == ActiveMatch.id
    ).where(MatchResult.status.in_(("FINISHED", "FINAL")), MatchResult.verified_at.is_not(None),
            MatchResult.verified_at <= cutoff, ActiveMatch.start_time <= cutoff)).all()
    return [{"provider": match.provider, "provider_match_id": match.provider_match_id, "home_team": match.home_team, "away_team": match.away_team,
             "home_score": result.home_score, "away_score": result.away_score,
             "status": "FINISHED", "verified_at": (result.verified_at.replace(tzinfo=timezone.utc)
                                                        if result.verified_at.tzinfo is None else result.verified_at),
             "start_time": match.start_time, "league_id": match.league_id}
            for match, result in rows if result.home_score is not None and result.away_score is not None]

def _statistics_for_match(match: ActiveMatch, history: list[dict], *, as_of: datetime) -> dict:
    unique: dict[str, dict] = {}
    for row in history:
        event_key = str((row.get("provider"), row.get("provider_match_id")) if row.get("provider_match_id") else (
            row.get("home_team"), row.get("away_team"), row.get("start_time")))
        if event_key not in unique or row.get("history_source") == "R2":
            unique[event_key] = row
    history = list(unique.values())
    return {
        "status": "R2_VERIFIED" if any(row.get("history_source") == "R2" for row in history)
                  else "PARTIAL" if history else "HISTORICAL_EVIDENCE_INSUFFICIENT",
        "as_of": as_of,
        "home": {"technical": technical_summary(match.home_team, history, as_of=as_of),
                 "form_5": form_analyzer(match.home_team, history, as_of=as_of, limit=5).to_dict(),
                 "form_10": form_analyzer(match.home_team, history, as_of=as_of, limit=10).to_dict(),
                 "form_15": form_analyzer(match.home_team, history, as_of=as_of, limit=15).to_dict(),
                 "home_last_5": form_analyzer(match.home_team, history, as_of=as_of, limit=5, venue="home").to_dict(),
                 "goals": goals_analyzer(match.home_team, history, as_of=as_of)},
        "away": {"technical": technical_summary(match.away_team, history, as_of=as_of),
                 "form_5": form_analyzer(match.away_team, history, as_of=as_of, limit=5).to_dict(),
                 "form_10": form_analyzer(match.away_team, history, as_of=as_of, limit=10).to_dict(),
                 "form_15": form_analyzer(match.away_team, history, as_of=as_of, limit=15).to_dict(),
                 "away_last_5": form_analyzer(match.away_team, history, as_of=as_of, limit=5, venue="away").to_dict(),
                 "goals": goals_analyzer(match.away_team, history, as_of=as_of)},
        "h2h": h2h_analyzer(match.home_team, match.away_team, history, as_of=as_of).to_dict(),
        "sample_size": len(history),
    }

def _r2_history(match: ActiveMatch, *, as_of: datetime, odds_rows: list[ActiveOdds] | None = None) -> dict:
    if not match.league_id or not match.season:
        return {"status": "R2_PARTITION_KEYS_MISSING", "rows": [], "similarity": None}
    kickoff = match.start_time
    if kickoff.tzinfo is None or kickoff.utcoffset() is None:
        kickoff = kickoff.replace(tzinfo=timezone.utc)
    start = kickoff - timedelta(days=365 * 5)
    try:
        repository = HistoricalRepository(R2ObjectStore())
        from app.providers.identity import LEAGUES, REVIEWED_IMPORT_KEY
        canonical_league = LEAGUES.get((match.provider, match.league_id), match.league_id)
        rows = []
        for league in dict.fromkeys((canonical_league, match.league_id)):
            rows.extend(repository.get_similarity_history(league=league, season=match.season,
                start=start, end=kickoff, before=as_of,
                bookmaker=(next((row.bookmaker for row in odds_rows or [] if row.market == "1X2" and row.selection == "HOME"), None))))
        for row in rows:
            row["canonical_league_id"] = canonical_league
            row["league_id"] = match.league_id  # reviewed competition ID mapping only
            if (row.get("identity_review") == REVIEWED_IMPORT_KEY and row.get("operational_provider") == match.provider):
                snapshot = json.loads(row["sql_snapshot_json"])
                # This fixture's persisted, reviewed aliases survive SQL cleanup.
                # Never turn this into a fuzzy or global club-name transformation.
                for field in ("provider", "provider_match_id", "home_team", "away_team"):
                    row[field] = snapshot[field]
    except R2NotConfigured:
        return {"status": "R2_NOT_CONFIGURED", "rows": [], "similarity": None}
    except Exception as exc:
        return {"status": "R2_UNAVAILABLE", "error_code": type(exc).__name__, "rows": [], "similarity": None}
    for row in rows:
        row["history_source"] = "R2"
    target: dict[str, Any] = {"league_id": match.league_id}
    home_quote = next((row for row in odds_rows or [] if row.market == "1X2" and row.selection == "HOME"
                       and row.period == "PREMATCH" and row.captured_at <= as_of), None)
    if home_quote:
        target["home_odds"] = float(home_quote.odds)
    thresholds = get_settings()
    similarity = historical_similarity(target, rows, as_of=as_of, thresholds=SimilarityThresholds(
        weak=thresholds.similarity_weak_threshold, moderate=thresholds.similarity_moderate_threshold,
        strong=thresholds.similarity_strong_threshold))
    return {"status": "AVAILABLE" if rows else "HISTORICAL_EVIDENCE_INSUFFICIENT",
            "sample_size": len(rows), "rows": rows, "similarity": similarity}

def _run_analysis(db: Session, match: ActiveMatch, *, as_of: datetime | None = None) -> dict:
    cutoff = as_of or datetime.now(timezone.utc)
    pg_history = _verified_history(db, cutoff=cutoff)
    odds_rows = db.scalars(select(ActiveOdds).where(ActiveOdds.match_id == match.id,
        ActiveOdds.captured_at <= cutoff).order_by(ActiveOdds.captured_at.desc()).limit(1000)).all()
    r2 = _r2_history(match, as_of=cutoff, odds_rows=odds_rows)
    match_data = _serialize(match)
    match_data["id"] = str(match.id)
    if match_data["start_time"].tzinfo is None:
        match_data["start_time"] = match_data["start_time"].replace(tzinfo=timezone.utc)
    ai_provider = create_ai_provider()
    try:
        # R2 is the authoritative historical copy when both stores contain a fixture.
        merged_history = {}
        for row in pg_history + r2["rows"]:
            key = str((row.get("provider"), row.get("provider_match_id") or row.get("id")))
            if key:
                merged_history[key] = row
        result = analyze_match(match_data, statistics_history=list(merged_history.values()),
            historical_rows=r2["rows"], odds_rows=odds_rows, as_of=cutoff, ai_provider=ai_provider,
            thresholds=SimilarityThresholds(
                weak=get_settings().similarity_weak_threshold,
                moderate=get_settings().similarity_moderate_threshold,
                strong=get_settings().similarity_strong_threshold))
    finally:
        close = getattr(ai_provider, "close", None)
        if callable(close):
            close()
    result["historical_source_status"] = r2["status"]
    record_analysis(db, match_id=match.id, result=result)
    persist_paper_prediction(db, match_id=match.id, kickoff=match_data["start_time"], result=result)
    db.commit()
    return result

def scan_upcoming_matches(db: Session, *, analyze_one: Callable[..., dict], limit: int = 50,
                          as_of: datetime | None = None,
                          scheduled_slot_start: datetime | None = None) -> dict[str, Any]:
    now = as_of or datetime.now(timezone.utc)
    bounded_limit = max(1, min(limit, 100))
    matches = db.scalars(select(ActiveMatch).where(
        ActiveMatch.start_time >= now, ActiveMatch.expires_at > now
    ).order_by(ActiveMatch.start_time).limit(bounded_limit)).all()

    recent: dict[str, MatchAnalysis] = {}
    if matches and scheduled_slot_start is not None:
        match_ids = [match.id for match in matches]
        rows = db.scalars(select(MatchAnalysis).where(
            MatchAnalysis.match_id.in_(match_ids),
            MatchAnalysis.analysis_timestamp >= scheduled_slot_start,
            MatchAnalysis.analysis_timestamp <= now,
        ).order_by(MatchAnalysis.analysis_timestamp.desc())).all()
        for row in rows:
            recent.setdefault(str(row.match_id), row)

    results = []
    skipped = 0
    for match in matches:
        stored = recent.get(str(match.id))
        if stored is not None:
            results.append(json.loads(stored.analysis_output))
            skipped += 1
            continue
        results.append(analyze_one(db, match, as_of=now))

    return {
        "status": "COMPLETED" if matches else "NO_UPCOMING_MATCHES",
        "as_of": now,
        "matches_scanned": len(matches),
        "matches_skipped": skipped,
        "signals": sum(result.get("final", {}).get("signal") == "SIGNAL" for result in results),
        "no_bet": sum(result.get("final", {}).get("signal") == "NO BET" for result in results),
        "unavailable": sum(result.get("analysis_status") in {"DATA_MISSING", "ODDS_MISSING", "HISTORY_INSUFFICIENT"}
                            for result in results),
        "records_seen": len(matches),
        "records_written": len(matches) - skipped,
        "records_skipped": skipped,
        "records_failed": 0,
    }
