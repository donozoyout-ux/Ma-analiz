"""Bounded ingestion task entrypoints; no live provider is assumed."""
from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Any

from app.core.config import get_settings
from app.db.session import SessionLocal
from app.jobs.ledger import MemoryLedger, SQLLedger, prune_job_history, run_idempotent_job
from app.jobs.results import aggregate_daily_metrics, settle_verified_results
from app.providers.base import ProviderFailure, ProviderNotConfigured
from app.providers.registry import ProviderCaller, create_provider
from app.storage.r2 import R2ObjectStore


def _caller() -> ProviderCaller:
    settings = get_settings()
    provider = create_provider(settings)
    return ProviderCaller(provider, requests_per_minute=settings.provider_requests_per_minute,
                          max_retries=settings.provider_max_retries)


def _missing_provider_result(exc: ProviderFailure) -> dict:
    return {"status": "SKIPPED" if isinstance(exc, ProviderNotConfigured) else "FAILED",
            "error_code": exc.code, "reason": exc.code, "records_seen": 0, "records_written": 0,
            "records_skipped": 0, "records_failed": 0 if isinstance(exc, ProviderNotConfigured) else 1}


def _send_operational_message(message: str, dedupe_key: str) -> str:
    settings = get_settings()
    if not (settings.telegram_bot_token and settings.telegram_chat_id):
        return "TELEGRAM_NOT_CONFIGURED"
    if SessionLocal is None:
        return "DATABASE_NOT_CONFIGURED"
    import asyncio
    import httpx
    from app.notifications.telegram import SQLNotificationLedger, TelegramService
    try:
        with SessionLocal() as session:
            async def send():
                async with httpx.AsyncClient(timeout=10) as client:
                    service = TelegramService(SQLNotificationLedger(session), client)
                    return await service.send_message(message, dedupe_key=dedupe_key)
            sent = asyncio.run(send())
        return "SENT" if sent else "DEDUPED"
    except Exception as exc:
        return type(exc).__name__.upper()


def _notify_ingestion(day: date, result: dict) -> None:
    settings = get_settings()
    if result.get("records_written", 0) < 1:
        return
    leagues = ",".join(settings.enabled_league_ids) or "configured leagues"
    text = (f"DATA UPDATE\\nLeague: {leagues}\\nDate: {day.isoformat()}\\n"
            f"Matches/records: {result.get('records_written', 0)}\\n"
            f"Skipped: {result.get('records_skipped', 0)}\\nStatus: {result.get('status')}")
    _send_operational_message(text, f"ingestion:{leagues}:{day.isoformat()}")

def _daily_matches(day: date) -> dict:
    from app.jobs.ingestion import daily_ingest
    from app.core.config import get_settings
    try:
        caller = _caller()
    except ProviderFailure as exc:
        return _missing_provider_result(exc)
    if SessionLocal is None:
        return {"status": "SKIPPED", "error_code": "DATABASE_NOT_CONFIGURED", "reason": "DATABASE_NOT_CONFIGURED",
                "records_seen": 0, "records_written": 0, "records_skipped": 0, "records_failed": 0}
    with SessionLocal() as session:
        try:
            result = daily_ingest(session, caller, day, enabled_leagues=get_settings().enabled_league_ids)
            _notify_ingestion(day, result)
            return result
        except ProviderFailure as exc:
            session.rollback()
            return _missing_provider_result(exc)


def collect_matches(day: date | None = None) -> dict[str, Any]:
    today = day or datetime.now(timezone.utc).date()
    return _daily_matches(today)


def collect_odds(day: date | None = None) -> dict[str, Any]:
    today = day or datetime.now(timezone.utc).date()
    try:
        caller = _caller()
    except ProviderFailure as exc:
        return _missing_provider_result(exc)
    if SessionLocal is None:
        return {"status": "SKIPPED", "error_code": "DATABASE_NOT_CONFIGURED", "reason": "DATABASE_NOT_CONFIGURED",
                "records_seen": 0, "records_written": 0, "records_skipped": 0, "records_failed": 0}
    from app.jobs.ingestion import daily_odds
    with SessionLocal() as session:
        try:
            return daily_odds(session, caller, today, enabled_leagues=get_settings().enabled_league_ids)
        except ProviderFailure as exc:
            session.rollback()
            return _missing_provider_result(exc)


def scheduled_scan(slot_start: datetime | None = None, session: Any | None = None,
                  limit: int = 50) -> dict[str, Any]:
    if session is None:
        return {"status": "SKIPPED", "error_code": "DATABASE_NOT_CONFIGURED", "reason": "DATABASE_NOT_CONFIGURED",
                "records_seen": 0, "records_written": 0, "records_skipped": 0, "records_failed": 0}
    from app.analysis.scanning import _run_analysis, scan_upcoming_matches

    interval = max(60, get_settings().worker_scanner_interval_seconds)
    now = datetime.now(timezone.utc)
    run_at = slot_start or datetime.fromtimestamp(int(now.timestamp() // interval) * interval, tz=timezone.utc)
    result = scan_upcoming_matches(session, analyze_one=_run_analysis, limit=limit, as_of=now,
                                   scheduled_slot_start=run_at)
    return {**result, "status": "SUCCESS" if result["matches_scanned"] else "SKIPPED",
            "reason": None if result["matches_scanned"] else "NO_UPCOMING_MATCHES"}


def process_results(day: date | None = None) -> dict[str, Any]:
    today = day or datetime.now(timezone.utc).date()
    try:
        caller = _caller()
    except ProviderFailure as exc:
        return _missing_provider_result(exc)
    if SessionLocal is None:
        return {"status": "SKIPPED", "error_code": "DATABASE_NOT_CONFIGURED", "reason": "DATABASE_NOT_CONFIGURED",
                "records_seen": 0, "records_written": 0, "records_skipped": 0, "records_failed": 0}
    from app.jobs.ingestion import ingest_results
    with SessionLocal() as session:
        try:
            result = ingest_results(session, caller, today)
            from app.jobs.results import settle_verified_results
            settlement = settle_verified_results(session, day=today)
            return {**result, **settlement}
        except ProviderFailure as exc:
            session.rollback()
            return _missing_provider_result(exc)


def archive_data(day: date | None = None) -> dict[str, Any]:
    settings = get_settings()
    try:
        store = R2ObjectStore()
    except Exception as exc:
        code = "R2_NOT_CONFIGURED" if "NOT_CONFIGURED" in str(exc) else "R2_UNAVAILABLE"
        return {"status": "SKIPPED", "error_code": code, "reason": code, "records_seen": 0,
                "records_written": 0, "records_skipped": 0, "records_failed": 0}
    if SessionLocal is None:
        return {"status": "SKIPPED", "error_code": "DATABASE_NOT_CONFIGURED", "reason": "DATABASE_NOT_CONFIGURED",
                "records_seen": 0, "records_written": 0, "records_skipped": 0, "records_failed": 0}
    from app.jobs.ingestion import archive_expired_active_state, historical_ingest
    today = day or datetime.now(timezone.utc).date()
    target_day = today - __import__("datetime").timedelta(days=1)
    archived_counts = {"odds": 0, "results": 0, "predictions": 0}
    historical_results = []
    with SessionLocal() as session:
        archived_counts = archive_expired_active_state(session, store)
        try:
            caller = _caller()
            seasons, leagues = settings.enabled_season_ids, settings.enabled_league_ids
            if seasons and leagues:
                historical_results = [historical_ingest(
                    session, caller, store, season=seasons[0], league=league,
                    start_date=target_day, end_date=target_day,
                ) for league in leagues]
            else:
                historical_results = [{"status": "SKIPPED", "error_code": "HISTORICAL_SCOPE_NOT_CONFIGURED",
                                       "records_seen": 0, "records_written": 0, "records_skipped": 0,
                                       "records_failed": 0}]
        except ProviderFailure as exc:
            historical_results = [_missing_provider_result(exc)]
    archived = sum(archived_counts.values()) + sum(item.get("records_written", 0) for item in historical_results)
    failures = sum(item.get("records_failed", 0) for item in historical_results)
    skipped = sum(item.get("records_skipped", 0) for item in historical_results)
    if failures and archived:
        status = "PARTIAL"
    elif failures:
        status = "FAILED"
    elif archived:
        status = "SUCCESS"
    else:
        status = "SKIPPED"
    reason = None if archived else next((item.get("error_code") or item.get("reason") for item in historical_results
                                         if item.get("error_code") or item.get("reason")), "NO_EXPIRED_ROWS")
    return {"status": status, "reason": reason, "records_seen": archived + skipped,
            "records_written": archived, "records_skipped": skipped, "records_failed": failures,
            "archived_by_dataset": archived_counts, "historical_jobs": historical_results}

def cleanup_database() -> dict[str, Any]:
    if SessionLocal is None:
        return {"status": "SKIPPED", "error_code": "DATABASE_NOT_CONFIGURED", "reason": "DATABASE_NOT_CONFIGURED",
                "records_seen": 0, "records_written": 0, "records_skipped": 0, "records_failed": 0}
    try:
        store = R2ObjectStore()
    except Exception as exc:
        code = "R2_NOT_CONFIGURED" if "NOT_CONFIGURED" in str(exc) else "R2_UNAVAILABLE"
        return {"status": "SKIPPED", "error_code": code, "reason": code,
                "records_seen": 0, "records_written": 0, "records_skipped": 0, "records_failed": 0}
    from app.jobs.ingestion import cleanup_verified_archives
    with SessionLocal() as session:
        count = cleanup_verified_archives(session, store)
    return {"status": "SUCCESS" if count else "SKIPPED",
            "reason": None if count else "NO_VERIFIED_ARCHIVE_CANDIDATES",
            "records_seen": count, "records_written": count, "records_skipped": 0, "records_failed": 0}


def calculate_daily_metrics(day: date | None = None) -> dict[str, Any]:
    if SessionLocal is None:
        return {"status": "SKIPPED", "error_code": "DATABASE_NOT_CONFIGURED", "reason": "DATABASE_NOT_CONFIGURED",
                "records_seen": 0, "records_written": 0, "records_skipped": 0, "records_failed": 0}
    today = day or datetime.now(timezone.utc).date()
    with SessionLocal() as session:
        from app.db.session import engine
        from app.storage.monitor import StorageMonitor
        snapshot = StorageMonitor(engine).snapshot()
        pg = snapshot["postgres"]["bytes"]
        r2 = snapshot["r2"]["bytes"]
        metric = aggregate_daily_metrics(session, day=today, postgres_bytes=pg, r2_bytes=r2)
    growth = snapshot["r2"].get("daily_growth_bytes")
    from app.providers.registry import provider_status
    summary = (
        f"DAILY REPORT\nMatches: {metric.total_matches}\nAnalyzed: {metric.analyzed_matches}\n"
        f"Predictions: {metric.predictions}\nHit Rate: {metric.hit_rate if metric.hit_rate is not None else 'pending'}\n"
        f"Paper P/L: {metric.paper_pl if metric.paper_pl is not None else 'pending'}\n"
        f"R2: {r2 if r2 is not None else 'unavailable'} bytes; daily growth {growth if growth is not None else 'unavailable'}\n"
        f"PostgreSQL: {pg if pg is not None else 'unavailable'} bytes\nProvider: {provider_status()}\n"
        f"Archive: {snapshot['r2']['status']}\nWorker: job cycle completed"
    )
    notification = _send_operational_message(summary, f"daily-summary:{today.isoformat()}")
    if snapshot["postgres"]["status"] in {"warning", "high", "critical"}:
        level = snapshot["postgres"]["status"]
        _send_operational_message(
            f"SYSTEM ALERT\nComponent: PostgreSQL\nStatus: {level}\nReason: storage threshold crossed\nAction: review largest tables",
            f"storage:{today.isoformat()}:{level}",
        )
    return {"status": "SUCCESS", "records_seen": metric.total_matches, "records_written": 1,
            "records_skipped": 0, "records_failed": 0, "telegram": notification}


HANDLERS = {
    "collect_matches": collect_matches,
    "collect_odds": collect_odds,
    "archive_data": archive_data,
    "cleanup_database": cleanup_database,
    "process_results": process_results,
    "calculate_daily_metrics": calculate_daily_metrics,
    "scheduled_scan": scheduled_scan,
}


def run_task(job_type: str, key: str, ledger: Any, *, day: date | None = None,
             limit: int | None = None) -> dict[str, Any]:
    if job_type not in HANDLERS:
        raise ValueError(f"UNKNOWN_JOB_TYPE: {job_type}")
    if job_type == "scheduled_scan":
        slot_start = datetime.fromisoformat(key.removeprefix("scheduled_scan:"))
        return run_idempotent_job(ledger, key, job_type,
                                  lambda: HANDLERS[job_type](slot_start, getattr(ledger, "session", None),
                                                             limit=limit or 50))
    return run_idempotent_job(ledger, key, job_type, lambda: HANDLERS[job_type](day) if job_type in
                              {"collect_matches", "collect_odds", "process_results", "calculate_daily_metrics", "archive_data"}
                              else HANDLERS[job_type]())


def run_daily(job_type: str, day: date | None = None) -> dict[str, Any]:
    from app.db.session import SessionLocal
    today = day or datetime.now(timezone.utc).date()
    key = f"{job_type}:{today.isoformat()}"
    if SessionLocal is None:
        return run_task(job_type, key, MemoryLedger(), day=today)
    with SessionLocal() as session:
        return run_task(job_type, key, SQLLedger(session), day=today)
