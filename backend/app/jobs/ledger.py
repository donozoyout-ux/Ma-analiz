"""Idempotent job execution and bounded PostgreSQL run history."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Protocol

from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.models import JobRun


@dataclass
class Run:
    idempotency_key: str
    job_type: str
    started_at: datetime
    finished_at: datetime | None = None
    status: str = "RUNNING"
    duration_ms: int | None = None
    records_seen: int = 0
    records_written: int = 0
    records_skipped: int = 0
    records_failed: int = 0
    error_code: str | None = None
    error_message: str | None = None
    records_processed: int = 0
    error: str | None = None


class Ledger(Protocol):
    def claim(self, key: str, job_type: str) -> tuple[Run, bool]: ...
    def finish(self, run: Run, status: str, **values: Any) -> None: ...
    def prune(self, days: int) -> int: ...


class MemoryLedger:
    def __init__(self):
        self.runs: dict[str, Run] = {}

    def claim(self, key: str, job_type: str) -> tuple[Run, bool]:
        existing = self.runs.get(key)
        if existing:
            stale_seconds = max(60, get_settings().job_run_stale_after_seconds)
            stale = existing.status == "RUNNING" and existing.started_at < datetime.now(timezone.utc) - timedelta(seconds=stale_seconds)
            retryable = existing.status in {"FAILED", "PARTIAL"}
            if stale or retryable:
                existing.started_at = datetime.now(timezone.utc)
                existing.finished_at = None
                existing.status = "RUNNING"
                existing.error_code = existing.error_message = existing.error = None
                return existing, True
            return existing, False
        run = Run(key, job_type, datetime.now(timezone.utc))
        self.runs[key] = run
        return run, True

    def finish(self, run: Run, status: str, **values: Any) -> None:
        run.finished_at = datetime.now(timezone.utc)
        run.duration_ms = max(0, int((run.finished_at - run.started_at).total_seconds() * 1000))
        run.status = status
        for key in ("records_seen", "records_written", "records_skipped", "records_failed",
                    "error_code", "error_message", "records_processed"):
            if key in values:
                setattr(run, key, values[key])
        run.error = run.error_message or run.error_code

    def prune(self, days: int) -> int:
        cutoff = datetime.now(timezone.utc) - timedelta(days=days)
        keys = [key for key, run in self.runs.items() if run.started_at < cutoff and run.status != "RUNNING"]
        for key in keys:
            del self.runs[key]
        return len(keys)


class SQLLedger:
    def __init__(self, session: Session):
        self.session = session

    def claim(self, key: str, job_type: str) -> tuple[Run, bool]:
        row = self.session.scalar(select(JobRun).where(JobRun.idempotency_key == key).with_for_update())
        now = datetime.now(timezone.utc)
        if row:
            stale_seconds = max(60, get_settings().job_run_stale_after_seconds)
            started = row.started_at.replace(tzinfo=timezone.utc) if row.started_at.tzinfo is None else row.started_at
            stale = row.status == "RUNNING" and started < now - timedelta(seconds=stale_seconds)
            if stale or row.status in {"FAILED", "PARTIAL"}:
                row.started_at, row.finished_at, row.status = now, None, "RUNNING"
                row.error = row.error_code = row.error_message = None
                row.records_seen = row.records_written = row.records_skipped = row.records_failed = 0
                row.records_processed = 0
                self.session.commit()
                return self._to_run(row), True
            self.session.rollback()
            return self._to_run(row), False
        row = JobRun(idempotency_key=key, job_type=job_type, started_at=now, status="RUNNING")
        self.session.add(row)
        try:
            self.session.commit()
        except IntegrityError:
            self.session.rollback()
            row = self.session.scalar(select(JobRun).where(JobRun.idempotency_key == key))
            if row is None:
                raise
            return self._to_run(row), False
        return self._to_run(row), True

    @staticmethod
    def _to_run(row: JobRun) -> Run:
        return Run(row.idempotency_key, row.job_type, row.started_at, row.finished_at, row.status,
                   row.duration_ms, row.records_seen, row.records_written, row.records_skipped,
                   row.records_failed, row.error_code, row.error_message, row.records_processed)

    def finish(self, run: Run, status: str, **values: Any) -> None:
        row = self.session.scalar(select(JobRun).where(JobRun.idempotency_key == run.idempotency_key).with_for_update())
        if row is None:
            raise RuntimeError("JOB_RUN_DISAPPEARED")
        finished = datetime.now(timezone.utc)
        started = row.started_at.replace(tzinfo=timezone.utc) if row.started_at.tzinfo is None else row.started_at
        row.finished_at = finished
        row.duration_ms = max(0, int((finished - started).total_seconds() * 1000))
        row.status = status
        row.records_seen = int(values.get("records_seen", 0))
        row.records_written = int(values.get("records_written", values.get("records_processed", 0)))
        row.records_skipped = int(values.get("records_skipped", 0))
        row.records_failed = int(values.get("records_failed", 0))
        row.records_processed = row.records_written
        row.error_code = values.get("error_code")
        row.error_message = (values.get("error_message") or "")[:240] or None
        row.error = row.error_code
        self.session.commit()

    def prune(self, days: int) -> int:
        # Critical job metadata is protected. Archive/review growth separately.
        return 0


def run_idempotent_job(ledger: Ledger, key: str, job_type: str, handler: Callable[[], dict[str, Any]]) -> dict[str, Any]:
    run, claimed = ledger.claim(key, job_type)
    if not claimed:
        return {"job": job_type, "status": run.status, "reason": "IDEMPOTENT_DUPLICATE",
                "records_written": run.records_written, "records_processed": run.records_processed}
    try:
        result = handler()
        status = result.get("status", "FAILED")
        if status not in {"SUCCESS", "PARTIAL", "SKIPPED", "FAILED"}:
            raise ValueError("INVALID_JOB_RESULT_STATUS")
        counts = {key: max(0, int(result.get(key, 0))) for key in
                  ("records_seen", "records_written", "records_skipped", "records_failed")}
        if status == "SUCCESS" and counts["records_written"] == 0:
            status = "SKIPPED"
            result = {**result, "reason": result.get("reason", "NO_RECORDS_INGESTED")}
        ledger.finish(run, status, **counts, records_processed=counts["records_written"],
                      error_code=result.get("error_code"), error_message=result.get("reason"))
        return {"job": job_type, **result, **counts, "status": status}
    except KeyboardInterrupt:
        # A user stop is durable and immediately claimable on the next run; do
        # not leave a live-looking RUNNING ledger row for the one-hour stale check.
        session = getattr(ledger, "session", None)
        if session is not None:
            session.rollback()
        ledger.finish(run, "PARTIAL", error_code="JOB_INTERRUPTED", error_message="Interrupted by operator")
        raise
    except Exception as exc:
        from app.providers.base import ProviderFailure
        code = exc.code if isinstance(exc, ProviderFailure) else type(exc).__name__.upper()
        session = getattr(ledger, "session", None)
        if session is not None:
            session.rollback()
        ledger.finish(run, "FAILED", error_code=code, error_message=type(exc).__name__)
        return {"job": job_type, "status": "FAILED", "error_code": code,
                "reason": type(exc).__name__, "records_seen": 0, "records_written": 0,
                "records_skipped": 0, "records_failed": 1}


def prune_job_history(session: Session | None = None, *, days: int | None = None) -> int:
    retention = days if days is not None else get_settings().job_history_retention_days
    if retention < 1:
        raise ValueError("JOB_HISTORY_RETENTION_DAYS must be positive")
    if session is None:
        return 0
    return SQLLedger(session).prune(retention)
