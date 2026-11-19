from datetime import datetime, timedelta, timezone
import json
import os
import secrets
from typing import Any
from uuid import UUID

import httpx
from fastapi import APIRouter, Depends, Header, HTTPException
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.db.session import engine, get_db
from app.analysis.odds import project_odds_snapshots
from app.analysis.scanning import (_r2_history, _run_analysis, _statistics_for_match, _valid_season,
                                   _verified_history)
from app.analysis.engine import (SimilarityThresholds, form_analyzer, goals_analyzer,
    historical_similarity, h2h_analyzer, no_bet_reasons)
from app.analysis.ai import create_ai_provider
from app.analysis.pipeline import analyze_match
from app.analysis.persistence import persist_paper_prediction, record_analysis
from app.historical.repository import HistoricalRepository
from app.models import ActiveMatch, ActiveOdds, ArchiveJob, Bookmaker, Coupon, CouponItem, DailyMetric, JobRun, Lesson, MatchAnalysis, MatchResult, Prediction
from app.providers.base import ProviderFailure
from app.providers.registry import ProviderCaller, create_provider, provider_health
from app.storage.r2 import R2NotConfigured, R2ObjectStore
from app.storage.monitor import StorageMonitor

router = APIRouter()
admin_router = APIRouter()


def _serialize(model) -> dict:
    return {column.name: getattr(model, column.name) for column in model.__table__.columns}


def _component_status(configured: bool, connected: bool | None = None, *, error: bool = False) -> str:
    if not configured:
        return "NOT_CONFIGURED"
    if error:
        return "ERROR"
    if connected is None:
        return "CONFIGURED"
    return "CONNECTED" if connected else "UNAVAILABLE"


def health() -> dict[str, Any]:
    settings = get_settings()
    database = ("NOT_CONFIGURED" if settings.database_url_missing else
                "ERROR" if not settings.database_url_valid else "UNAVAILABLE")
    worker, archive = "UNKNOWN", "NO_VERIFIED_ARCHIVES"
    if engine is not None and settings.database_url_valid:
        try:
            with engine.connect() as connection:
                connection.execute(text("SELECT 1"))
                latest_job = connection.execute(select(JobRun.status).order_by(JobRun.started_at.desc()).limit(1)).scalar_one_or_none()
                latest_archive = connection.execute(select(ArchiveJob.status).order_by(ArchiveJob.created_at.desc()).limit(1)).scalar_one_or_none()
            database = "CONNECTED"
            worker = latest_job or "NO_JOB_RUNS"
            archive = latest_archive or "NO_ARCHIVES"
        except Exception:
            database = "UNAVAILABLE"
            worker = "UNKNOWN"
            archive = "UNKNOWN"
    provider_info = provider_health()
    provider_connected = provider_info.get("status") == "PROVIDER_OK"
    provider_configured = provider_info.get("status") != "PROVIDER_NOT_CONFIGURED"
    provider_component = _component_status(provider_configured, provider_connected,
                                           error=provider_info.get("status") == "PROVIDER_ERROR")

    selected_provider = (settings.provider_id or "").lower()
    goaloo_configured = selected_provider == "goaloo" and bool(settings.enabled_league_ids)
    sportmonks_configured = selected_provider == "sportmonks" and bool(settings.provider_api_token)
    goaloo_status = (provider_component if goaloo_configured and selected_provider == "goaloo"
                     else _component_status(goaloo_configured))
    sportmonks_status = provider_component if selected_provider == "sportmonks" else _component_status(sportmonks_configured)

    r2_configured = settings.r2_configured
    r2_status = ("NOT_CONFIGURED" if settings.r2_missing_env else
                 "ERROR" if settings.r2_invalid_env else "CONFIGURED")
    if r2_configured:
        try:
            store = R2ObjectStore()
            store.client.list_objects_v2(Bucket=store.bucket, Prefix="", MaxKeys=1)
            r2_status = "CONNECTED"
        except Exception:
            r2_status = "UNAVAILABLE"

    groq_configured = settings.groq_configured
    groq_status = ("NOT_CONFIGURED" if settings.groq_missing_env else
                   "ERROR" if settings.groq_invalid_env else "CONFIGURED")
    if groq_configured:
        try:
            groq_key = settings.groq_api_key if (settings.groq_api_key or "").strip() else settings.ai_api_key
            response = httpx.get(f"{settings.groq_base_url.rstrip('/')}/models",
                headers={"Authorization": f"Bearer {groq_key}"}, timeout=5)
            groq_status = "CONNECTED" if response.is_success else "UNAVAILABLE"
        except httpx.HTTPError:
            groq_status = "UNAVAILABLE"

    telegram_configured = bool(settings.telegram_bot_token and settings.telegram_chat_id)
    return {
        "status": "ok" if database == "CONNECTED" else "degraded",
        "provider": provider_info["status"],
        "provider_status": provider_component,
        "provider_health": provider_info,
        "odds_provider": provider_info["status"],
        "database": database,
        "r2": r2_status,
        "worker": worker,
        "archive": archive,
        "telegram": _component_status(telegram_configured),
        "ai": groq_status if (settings.ai_provider or "").lower() == "groq" else
              _component_status(bool(settings.ai_provider and settings.ai_api_key)),
        "services": {"goaloo": goaloo_status, "sportmonks": sportmonks_status,
                     "r2": r2_status, "postgres": database, "groq": groq_status,
                     "telegram": _component_status(telegram_configured),
                     "scanner": "NOT_CONFIGURED" if not provider_configured else "CONFIGURED",
                     "historical_data": "CONNECTED" if r2_status == "CONNECTED" else r2_status,
                     "provider": provider_component,
                     "odds": "CONNECTED" if provider_connected else provider_component,
                     "ai": groq_status},
    }


@router.get("/health")
def health_endpoint() -> dict[str, Any]:
    return health()


@router.get("/system/status")
def system_status() -> dict:
    readiness = health()
    return {
        "status": readiness["status"],
        "provider_mode": readiness["provider"],
        "services": {**readiness["services"],
                     "odds_provider": readiness["provider_status"],
                     "database": readiness["database"], "worker": readiness["worker"],
                     "archive": readiness["archive"]},
    }


@admin_router.post("/admin/production-readiness", tags=["admin"])
def production_readiness_endpoint(
    ops_token: str | None = Header(default=None, alias="X-Production-Ops-Token"),
) -> dict[str, Any]:
    """Run protected, read-only production dependency gates.

    No migration or backfill is invoked. The response intentionally contains
    only status fields, never details returned by provider/database clients.
    """
    configured_token = os.environ.get("PRODUCTION_OPS_TOKEN")
    if not configured_token or not configured_token.strip():
        raise HTTPException(status_code=503, detail="PRODUCTION_OPS_UNAVAILABLE")
    if not ops_token or not secrets.compare_digest(ops_token, configured_token):
        raise HTTPException(status_code=401, detail="INVALID_PRODUCTION_OPS_TOKEN")

    from app.jobs.production_readiness import run_process_environment_readiness

    try:
        report = run_process_environment_readiness()
    except Exception:
        # Do not serialize exception text: clients/drivers may include secrets.
        return {
            "postgresql": "FAIL", "alembic": "FAIL", "r2": "FAIL", "goaloo": "FAIL",
            "all_gates_pass": False, "backfill_allowed": False,
        }
    alembic_details = report.get("alembic_details", {})
    alembic_status = ("NEEDS_MIGRATION"
                      if alembic_details.get("status") == "NEEDS_MIGRATION"
                      else report.get("alembic", "FAIL"))
    statuses = {
        "postgresql": report.get("postgres", "FAIL"),
        "alembic": alembic_status,
        "r2": report.get("r2", "FAIL"),
        "goaloo": report.get("goaloo", "FAIL"),
    }
    all_pass = all(value == "PASS" for value in statuses.values())
    return {**statuses, "all_gates_pass": all_pass, "backfill_allowed": False}


@router.get("/storage")
@router.get("/system/storage")
def storage() -> dict:
    result = StorageMonitor(engine).snapshot()
    result["r2"]["daily_growth_bytes"] = None
    if engine is not None:
        try:
            with engine.connect() as connection:
                metrics = connection.execute(
                    select(DailyMetric.date, DailyMetric.r2_bytes)
                    .where(DailyMetric.r2_bytes.is_not(None))
                    .order_by(DailyMetric.date.desc()).limit(2)
                ).all()
            if len(metrics) == 2:
                result["r2"]["daily_growth_bytes"] = int(metrics[0].r2_bytes) - int(metrics[1].r2_bytes)
        except Exception:
            pass
    return result


@router.get("/matches")
def list_matches(db: Session = Depends(get_db), limit: int = 100) -> list[dict]:
    now = datetime.now(timezone.utc)
    rows = db.scalars(select(ActiveMatch).where(ActiveMatch.expires_at > now).order_by(ActiveMatch.start_time).limit(max(1, min(limit, 500)))).all()
    return [_serialize(row) for row in rows]


@router.get("/matches/{match_id}")
def match_detail(match_id: UUID, db: Session = Depends(get_db)) -> dict:
    match = db.get(ActiveMatch, match_id)
    if match is None:
        raise HTTPException(status_code=404, detail="MATCH_NOT_FOUND")
    data = _serialize(match)
    now = datetime.now(timezone.utc)
    odds_rows = db.scalars(select(ActiveOdds).where(
        ActiveOdds.match_id == match_id, ActiveOdds.captured_at <= now
    ).order_by(ActiveOdds.captured_at.desc()).limit(1000)).all()
    bookmaker_ids = {row.bookmaker_entity_id for row in odds_rows if row.bookmaker_entity_id is not None}
    bookmakers = {row.id: row for row in db.scalars(select(Bookmaker).where(Bookmaker.id.in_(bookmaker_ids))).all()} if bookmaker_ids else {}
    odds = []
    for row in odds_rows:
        snapshot = _serialize(row)
        snapshot["price"] = snapshot["odds"]
        entity = bookmakers.get(row.bookmaker_entity_id)
        snapshot["bookmaker_entity"] = ({"bookmaker_id": str(entity.id), "name": entity.name,
                                         "source": entity.source, "country": entity.country,
                                         "active": entity.active} if entity else None)
        odds.append(snapshot)
    summary, live = project_odds_snapshots(odds_rows, kickoff=match.start_time,
                                           match_status=match.status, as_of=now)
    data["odds"] = summary
    data["odds_summary"] = summary
    data["live_odds"] = live
    data["current_odds"] = [item["latest"] for item in summary]
    data["opening_odds"] = [item["opening"] for item in summary]
    data["closing_odds"] = [item["closing"] for item in summary if item["closing"] is not None]
    data["odds_history"] = odds
    return data








@router.get("/analysis/{match_id}/statistics")
def match_statistics(match_id: UUID, db: Session = Depends(get_db)) -> dict:
    match = db.get(ActiveMatch, match_id)
    if match is None:
        raise HTTPException(status_code=404, detail="MATCH_NOT_FOUND")
    cutoff = datetime.now(timezone.utc)
    history = _verified_history(db, cutoff=cutoff)
    r2 = _r2_history(match, as_of=cutoff)
    return _statistics_for_match(match, history + r2["rows"], as_of=cutoff)


@router.get("/analysis/{match_id}/historical")
def match_historical(match_id: UUID, db: Session = Depends(get_db)) -> dict:
    match = db.get(ActiveMatch, match_id)
    if match is None:
        raise HTTPException(status_code=404, detail="MATCH_NOT_FOUND")
    cutoff = datetime.now(timezone.utc)
    odds = db.scalars(select(ActiveOdds).where(ActiveOdds.match_id == match_id,
        ActiveOdds.captured_at <= cutoff).order_by(ActiveOdds.captured_at.desc()).limit(1000)).all()
    result = _r2_history(match, as_of=cutoff, odds_rows=odds)
    result.pop("rows", None)
    return result


@router.get("/analysis/{match_id}")
def match_analysis(match_id: UUID, db: Session = Depends(get_db)) -> dict:
    match = db.get(ActiveMatch, match_id)
    if match is None:
        raise HTTPException(status_code=404, detail="MATCH_NOT_FOUND")
    stored = db.scalar(select(MatchAnalysis).where(MatchAnalysis.match_id == match_id)
                       .order_by(MatchAnalysis.analysis_timestamp.desc()).limit(1))
    if stored is not None:
        import json
        return json.loads(stored.analysis_output)
    cutoff = datetime.now(timezone.utc)
    history = _verified_history(db, cutoff=cutoff)
    odds_rows = db.scalars(select(ActiveOdds).where(ActiveOdds.match_id == match_id,
        ActiveOdds.captured_at <= cutoff).order_by(ActiveOdds.captured_at.desc()).limit(1000)).all()
    r2 = _r2_history(match, as_of=cutoff, odds_rows=odds_rows)
    statistics = _statistics_for_match(match, history + r2["rows"], as_of=cutoff)
    return {"match_id": str(match_id), "analysis_timestamp": cutoff,
            "model_version": None, "feature_version": "canonical-1", "feature_snapshot": [],
            "statistics": statistics,
            "historical": {"status": r2["status"], "sample_size": r2.get("sample_size", 0),
                           "distribution": (r2.get("similarity") or {}).get("distribution")},
            "value": {"status": "MODEL_PROBABILITY_UNAVAILABLE", "value": None},
            "ai": {"status": "AI_UNAVAILABLE", "summary": None},
            "final": {"signal": "NO BET", "score": None, "confidence": "INSUFFICIENT",
                      "risk": "UNKNOWN", "reasons": no_bet_reasons(
                          sample_size=statistics["home"]["form_5"]["sample_size"], value=None,
                          data_status="HEALTHY" if statistics["status"] == "AVAILABLE" else "PARTIAL",
                          ) + ["NO_VERIFIED_MODEL_OUTPUT"] + ([] if r2.get("status") == "AVAILABLE" else [r2["status"]])}}




@router.post("/analysis/{match_id}/run")
def run_match_analysis(match_id: UUID, db: Session = Depends(get_db)) -> dict:
    match = db.get(ActiveMatch, match_id)
    if match is None:
        raise HTTPException(status_code=404, detail="MATCH_NOT_FOUND")
    return _run_analysis(db, match)


@router.get("/scanner/status")
def scanner_status(db: Session = Depends(get_db)) -> dict:
    now = datetime.now(timezone.utc)
    settings = get_settings()
    interval = max(60, settings.worker_scanner_interval_seconds)
    configured = bool(settings.provider_id and settings.enabled_league_ids)
    lookahead = min(7 * 24 * 60, max(0, settings.scanner_lookahead_minutes))
    latest_run = db.scalar(select(JobRun).where(JobRun.job_type == "scheduled_scan")
                           .order_by(JobRun.started_at.desc()).limit(1))
    last_success = db.scalar(select(JobRun.finished_at).where(
        JobRun.job_type == "scheduled_scan", JobRun.status == "SUCCESS",
        JobRun.finished_at.is_not(None)).order_by(JobRun.finished_at.desc()).limit(1))
    upcoming_query = select(func.count()).select_from(ActiveMatch).where(
        ActiveMatch.start_time >= now,
        ActiveMatch.start_time <= now + timedelta(minutes=lookahead),
        ActiveMatch.expires_at > now,
        ActiveMatch.status.in_(("SCHEDULED", "NOT_STARTED", "PREMATCH")),
    )
    if settings.enabled_league_ids:
        upcoming_query = upcoming_query.where(ActiveMatch.league_id.in_(settings.enabled_league_ids))
    else:
        upcoming_query = upcoming_query.where(False)
    if settings.provider_id:
        upcoming_query = upcoming_query.where(ActiveMatch.provider == settings.provider_id.lower())
    upcoming = db.scalar(upcoming_query) or 0
    since = now - timedelta(hours=24)
    analysis_counts = db.execute(select(MatchAnalysis.final_decision, MatchAnalysis.analysis_status,
        func.count()).where(MatchAnalysis.analysis_timestamp >= since,
                            MatchAnalysis.analysis_timestamp <= now)
        .group_by(MatchAnalysis.final_decision, MatchAnalysis.analysis_status)).all()
    signals = sum(count for decision, _status, count in analysis_counts if decision == "SIGNAL")
    no_bet = sum(count for decision, _status, count in analysis_counts if decision == "NO BET")
    analyzed = sum(count for _decision, status, count in analysis_counts if status == "ANALYZED")
    recent_total = sum(count for _decision, _status, count in analysis_counts)
    latest_started = latest_run.started_at if latest_run is not None else None
    if latest_started is not None and latest_started.tzinfo is None:
        latest_started = latest_started.replace(tzinfo=timezone.utc)
    latest_status = latest_run.status if latest_run is not None else None
    recent_success = bool(last_success and (now - (last_success.replace(tzinfo=timezone.utc)
        if last_success.tzinfo is None else last_success)).total_seconds() <= interval * 2)
    state = ("ERROR" if latest_status in {"FAILED", "PARTIAL"} else
             "ACTIVE" if configured and (latest_status == "RUNNING" or recent_success) else "IDLE")
    return {
        "enabled": configured,
        "status": state,
        "last_run": latest_started,
        "next_run": datetime.fromtimestamp((int(now.timestamp() // interval) + 1) * interval,
                                            tz=timezone.utc) if configured else None,
        "last_success": last_success,
        "last_error": latest_run.error_code if latest_status in {"FAILED", "PARTIAL"} else None,
        "matches_scanned": int(latest_run.records_seen or 0) if latest_run else 0,
        "upcoming_matches": int(upcoming),
        "signals": int(signals),
        "no_bet": int(no_bet),
        "provider": settings.provider_id or "NOT_CONFIGURED",
        "provider_latency_ms": None,
        "data_quality": {"analyzed": int(analyzed), "records_24h": int(recent_total)},
        "metrics_window_hours": 24,
    }


@router.get("/scanner/results")
def scanner_results(db: Session = Depends(get_db), limit: int = 200) -> list[dict]:
    now = datetime.now(timezone.utc)
    settings = get_settings()
    query = select(ActiveMatch).where(
        ActiveMatch.start_time >= now,
        ActiveMatch.start_time <= now + timedelta(minutes=min(
            7 * 24 * 60, max(0, settings.scanner_lookahead_minutes))),
        ActiveMatch.expires_at > now,
        ActiveMatch.status.in_(("SCHEDULED", "NOT_STARTED", "PREMATCH")),
    )
    if not settings.provider_id or not settings.enabled_league_ids:
        return []
    query = query.where(ActiveMatch.league_id.in_(settings.enabled_league_ids))
    if settings.enabled_season_ids:
        query = query.where(ActiveMatch.season.in_(settings.enabled_season_ids))
    if settings.provider_id:
        query = query.where(ActiveMatch.provider == settings.provider_id.lower())
    matches = db.scalars(query.order_by(ActiveMatch.start_time).limit(
        max(1, min(limit, settings.scanner_max_matches_per_run, 100)))).all()
    matches = [match for match in matches if _valid_season(match.season)]
    match_ids = [match.id for match in matches]
    analyses = db.scalars(select(MatchAnalysis).where(MatchAnalysis.match_id.in_(match_ids),
        MatchAnalysis.analysis_timestamp <= now).order_by(MatchAnalysis.analysis_timestamp.desc())).all() if match_ids else []
    latest: dict[str, MatchAnalysis] = {}
    for analysis in analyses:
        latest.setdefault(str(analysis.match_id), analysis)
    output = []
    for match in matches:
        stored = latest.get(str(match.id))
        if stored is None:
            observed_odds = db.scalars(select(ActiveOdds).where(
                ActiveOdds.match_id == match.id, ActiveOdds.captured_at <= now,
                ActiveOdds.expires_at > now,
                ActiveOdds.period.in_(("PREMATCH", "PRE-MATCH", "LIVE")),
                ActiveOdds.status.in_(("AVAILABLE", "ACTIVE", "OPEN")),
            ).order_by(ActiveOdds.captured_at)).all()
            prematch_odds = [row for row in observed_odds if row.period in {"PREMATCH", "PRE-MATCH"}]
            projected, live = project_odds_snapshots(observed_odds, kickoff=match.start_time,
                match_status=match.status, as_of=now)
            reason = "ODDS_INSUFFICIENT" if not prematch_odds else "ANALYSIS_NOT_RUN"
            latest_home = next((row.get("latest", {}).get("price") for row in projected
                                if row.get("market") == "1X2" and row.get("selection") == "HOME"
                                and row.get("latest")), None)
            output.append({
                "id": str(match.id), "match_id": str(match.id),
                "provider_match_id": match.provider_match_id, "provider": match.provider,
                "home_team": match.home_team, "away_team": match.away_team,
                "league": match.league_name or match.league_id, "league_id": match.league_id,
                "season": match.season,
                "start_time": match.start_time.replace(tzinfo=timezone.utc)
                    if match.start_time.tzinfo is None else match.start_time,
                "market": None, "selection": None, "odds": latest_home, "value": None,
                "historical_sample_size": 0, "snapshot_type": None,
                "markets": projected, "live_odds": live, "status": "NO BET", "signal": "NO BET",
                "reason": [reason], "data_quality": "DATA_UNAVAILABLE",
            })
            continue
        analysis = json.loads(stored.analysis_output)
        cutoff = analysis.get("analysis_timestamp")
        cutoff = datetime.fromisoformat(cutoff.replace("Z", "+00:00")) if isinstance(cutoff, str) else now
        if cutoff.tzinfo is None:
            cutoff = cutoff.replace(tzinfo=timezone.utc)
        odds = db.scalars(select(ActiveOdds).where(ActiveOdds.match_id == match.id,
            ActiveOdds.captured_at <= min(now, cutoff), ActiveOdds.expires_at > now,
            ActiveOdds.status.in_(("AVAILABLE", "ACTIVE", "OPEN"))).order_by(ActiveOdds.captured_at)).all()
        projected, live = project_odds_snapshots(odds, kickoff=match.start_time,
            match_status=match.status, as_of=min(now, cutoff))
        final = analysis.get("final") or {}
        selected = analysis.get("selection")
        market_odds = (analysis.get("odds") or {}).get("market_odds") or {}
        value = analysis.get("value") or {}
        history = analysis.get("historical") or {}
        output.append({
            "id": str(match.id), "match_id": str(match.id),
            "provider_match_id": match.provider_match_id, "provider": match.provider,
            "home_team": match.home_team, "away_team": match.away_team,
            "league": match.league_name or match.league_id, "league_id": match.league_id,
            "season": match.season,
            "start_time": match.start_time.replace(tzinfo=timezone.utc)
                if match.start_time.tzinfo is None else match.start_time,
            "analysis_timestamp": stored.analysis_timestamp,
            "market": "1X2" if selected else None, "selection": selected,
            "odds": market_odds.get(selected) if selected else None,
            "value": value.get("value"), "model_probability": analysis.get("model_probability"),
            "implied_probability": analysis.get("market_probability"),
            "historical_sample_size": int(history.get("sample_size", 0) or 0),
            "snapshot_type": next((row.get("latest", {}).get("snapshot_type") for row in projected
                                   if row.get("market") == "1X2" and row.get("selection") == selected), None),
            "markets": projected, "live_odds": live,
            "status": "SIGNAL" if final.get("signal") == "SIGNAL" else "NO BET",
            "signal": final.get("signal", "NO BET"),
            "reason": final.get("reasons", []),
            "data_quality": analysis.get("analysis_status", "UNKNOWN"),
        })
    return output


@router.post("/scanner/run")
def run_scanner(db: Session = Depends(get_db), limit: int = 50) -> dict:
    settings = get_settings()
    now = datetime.now(timezone.utc)
    interval = max(60, settings.worker_scanner_interval_seconds)
    slot_start = datetime.fromtimestamp(int(now.timestamp() // interval) * interval, tz=timezone.utc)
    from app.jobs.ledger import SQLLedger
    from app.jobs.tasks import run_task

    bounded_limit = max(1, min(limit, settings.scanner_max_matches_per_run, 100))
    outcome = run_task("scheduled_scan", f"scheduled_scan:{slot_start.isoformat()}", SQLLedger(db),
                       limit=bounded_limit)
    if outcome.get("reason") == "IDEMPOTENT_DUPLICATE":
        return {"status": "SKIPPED", **outcome}
    if outcome.get("status") == "SUCCESS":
        outcome["status"] = "COMPLETED"
    return outcome


@router.get("/provider/leagues")
def provider_leagues() -> list[dict]:
    settings = get_settings()
    provider = None
    try:
        provider = create_provider(settings)
        return ProviderCaller(provider, requests_per_minute=settings.provider_requests_per_minute,
                              max_retries=settings.provider_retry_count).call("get_leagues")
    except ProviderFailure as exc:
        raise HTTPException(status_code=503, detail=exc.code) from None
    finally:
        close = getattr(provider, "close", None)
        if callable(close):
            close()


@router.get("/provider/leagues/{league_id}/seasons")
def provider_seasons(league_id: str) -> list[dict]:
    settings = get_settings()
    provider = None
    try:
        provider = create_provider(settings)
        return ProviderCaller(provider, requests_per_minute=settings.provider_requests_per_minute,
                              max_retries=settings.provider_retry_count).call("get_seasons", league_id)
    except ProviderFailure as exc:
        raise HTTPException(status_code=503, detail=exc.code) from None
    finally:
        close = getattr(provider, "close", None)
        if callable(close):
            close()


@router.get("/bookmakers")
def bookmakers(db: Session = Depends(get_db)) -> list[dict]:
    rows = db.scalars(select(Bookmaker).order_by(Bookmaker.name)).all()
    return [{"bookmaker_id": str(row.id), "provider": row.provider,
             "provider_bookmaker_id": row.provider_bookmaker_id, "name": row.name,
             "source": row.source, "country": row.country, "active": row.active}
            for row in rows]


@router.get("/predictions")
def list_predictions(db: Session = Depends(get_db), limit: int = 100) -> list[dict]:
    rows = db.scalars(select(Prediction).where(Prediction.expires_at > datetime.now(timezone.utc)).order_by(Prediction.created_at.desc()).limit(max(1, min(limit, 500)))).all()
    return [_serialize(row) for row in rows]


@router.get("/coupons")
def list_coupons(db: Session = Depends(get_db), limit: int = 100) -> list[dict]:
    rows = db.scalars(select(Coupon).where(Coupon.expires_at > datetime.now(timezone.utc)).order_by(Coupon.created_at.desc()).limit(max(1, min(limit, 500)))).all()
    result = []
    for coupon in rows:
        item = _serialize(coupon)
        item["items"] = [_serialize(row) for row in db.scalars(select(CouponItem).where(CouponItem.coupon_id == coupon.id)).all()]
        result.append(item)
    return result


def _daily_metrics(db: Session, limit: int) -> list[dict]:
    rows = db.scalars(select(DailyMetric).order_by(DailyMetric.date.desc()).limit(max(1, min(limit, 365)))).all()
    return [_serialize(row) for row in reversed(rows)]


@router.get("/performance")
def performance(db: Session = Depends(get_db), limit: int = 90) -> list[dict]:
    return _daily_metrics(db, limit)


@router.get("/metrics/daily")
def daily_metrics(db: Session = Depends(get_db), limit: int = 90) -> list[dict]:
    return _daily_metrics(db, limit)


@router.get("/lessons")
def lessons(db: Session = Depends(get_db), limit: int = 100) -> list[dict]:
    rows = db.scalars(select(Lesson).order_by(Lesson.created_at.desc()).limit(max(1, min(limit, 500)))).all()
    return [_serialize(row) for row in rows]
