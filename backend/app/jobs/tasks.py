"""Bounded ingestion task entrypoints; unavailable inputs are recorded as skips."""
from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Any

from app.core.config import get_settings
from app.providers.mock import EmptyMockProvider


def collect_matches() -> dict[str, Any]:
    provider = get_settings().match_provider
    if provider == "mock":
        count = len(EmptyMockProvider().get_matches(datetime.now(timezone.utc).date()))
        return {"status": "SKIPPED", "reason": "MOCK_PROVIDER_EMPTY" if count == 0 else "NO_PERSISTENCE_ADAPTER", "records_processed": 0}
    if not provider:
        return {"status": "SKIPPED", "reason": "PROVIDER_NOT_CONFIGURED", "records_processed": 0}
    return {"status": "SKIPPED", "reason": "PROVIDER_UNSUPPORTED", "records_processed": 0}


def collect_odds() -> dict[str, Any]:
    provider = get_settings().match_provider
    reason = "MOCK_PROVIDER_EMPTY" if provider == "mock" else "PROVIDER_NOT_CONFIGURED" if not provider else "PROVIDER_UNSUPPORTED"
    return {"status": "SKIPPED", "reason": reason, "records_processed": 0}


def archive_data() -> dict[str, Any]:
    settings = get_settings()
    if not (settings.r2_endpoint and settings.r2_access_key_id and settings.r2_secret_access_key):
        return {"status": "SKIPPED", "reason": "R2_NOT_CONFIGURED", "records_processed": 0}
    return {"status": "SKIPPED", "reason": "NO_ARCHIVE_SOURCE", "records_processed": 0}


def cleanup_database() -> dict[str, Any]:
    return {"status": "SKIPPED", "reason": "NO_VERIFIED_ARCHIVE_CANDIDATES", "records_processed": 0}


def process_results() -> dict[str, Any]:
    return {"status": "SKIPPED", "reason": "RESULT_PROVIDER_NOT_CONFIGURED", "records_processed": 0}


def calculate_daily_metrics() -> dict[str, Any]:
    return {"status": "SKIPPED", "reason": "NO_RESULT_SOURCE", "records_processed": 0}


HANDLERS = {
    "collect_matches": collect_matches,
    "collect_odds": collect_odds,
    "archive_data": archive_data,
    "cleanup_database": cleanup_database,
    "process_results": process_results,
    "calculate_daily_metrics": calculate_daily_metrics,
}


def run_task(job_type: str, key: str, ledger: Any) -> dict[str, Any]:
    from app.jobs.ledger import run_idempotent_job
    if job_type not in HANDLERS:
        raise ValueError(f"UNKNOWN_JOB_TYPE: {job_type}")
    return run_idempotent_job(ledger, key, job_type, HANDLERS[job_type])


def run_daily(job_type: str, day: date | None = None) -> dict[str, Any]:
    from app.jobs.ledger import MemoryLedger
    from app.db.session import SessionLocal
    today = day or datetime.now(timezone.utc).date()
    key = f"{job_type}:{today.isoformat()}"
    if SessionLocal is None:
        return run_task(job_type, key, MemoryLedger())
    with SessionLocal() as session:
        from app.jobs.ledger import SQLLedger
        return run_task(job_type, key, SQLLedger(session))
