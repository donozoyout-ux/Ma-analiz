"""Idempotent job execution and persistent run history."""
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
    error: str | None = None
    records_processed: int = 0


class Ledger(Protocol):
    def claim(self, key: str, job_type: str) -> tuple[Run, bool]: ...
    def finish(self, run: Run, status: str, *, records: int = 0, error: str | None = None) -> None: ...
    def prune(self, days: int) -> int: ...


class MemoryLedger:
    def __init__(self):
        self.runs: dict[str, Run] = {}

    def claim(self, key: str, job_type: str) -> tuple[Run, bool]:
        existing = self.runs.get(key)
        if existing:
            if existing.status == "RUNNING" and existing.started_at < datetime.now(timezone.utc) - timedelta(hours=1):
                existing.started_at = datetime.now(timezone.utc)
                return existing, True
            return existing, False
        run = Run(key, job_type, datetime.now(timezone.utc))
        self.runs[key] = run
        return run, True

    def finish(self, run: Run, status: str, *, records: int = 0, error: str | None = None) -> None:
        run.finished_at = datetime.now(timezone.utc)
        run.duration_ms = max(0, int((run.finished_at - run.started_at).total_seconds() * 1000))
        run.status, run.records_processed, run.error = status, records, error

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
            if row.status == "RUNNING" and row.started_at < now - timedelta(hours=1):
                row.started_at = now
                self.session.commit()
                return self._to_run(row), True
            return self._to_run(row), False
        row = JobRun(idempotency_key=key, job_type=job_type, started_at=now, status="RUNNING")
        self.session.add(row)
        try:
            self.session.commit()
        except IntegrityError:
            self.session.rollback()
            row = self.session.scalar(select(JobRun).where(JobRun.idempotency_key == key))
            return self._to_run(row), False
        return self._to_run(row), True

    @staticmethod
    def _to_run(row: JobRun) -> Run:
        return Run(row.idempotency_key, row.job_type, row.started_at, row.finished_at, row.status,
                   row.duration_ms, row.error, row.records_processed)

    def finish(self, run: Run, status: str, *, records: int = 0, error: str | None = None) -> None:
        row = self.session.scalar(select(JobRun).where(JobRun.idempotency_key == run.idempotency_key).with_for_update())
        if row is None:
            raise RuntimeError("JOB_RUN_DISAPPEARED")
        finished = datetime.now(timezone.utc)
        row.finished_at = finished
        row.duration_ms = max(0, int((finished - row.started_at).total_seconds() * 1000))
        row.status, row.records_processed, row.error = status, records, error
        self.session.commit()

    def prune(self, days: int) -> int:
        cutoff = datetime.now(timezone.utc) - timedelta(days=days)
        result = self.session.execute(delete(JobRun).where(JobRun.created_at < cutoff, JobRun.status != "RUNNING"))
        self.session.commit()
        return result.rowcount or 0


def run_idempotent_job(ledger: Ledger, key: str, job_type: str, handler: Callable[[], dict[str, Any]]) -> dict[str, Any]:
    run, claimed = ledger.claim(key, job_type)
    if not claimed:
        return {"job": job_type, "status": run.status, "reason": "IDEMPOTENT_DUPLICATE", "records_processed": run.records_processed}
    try:
        result = handler()
        status = result.get("status", "SUCCESS")
        if status not in {"SUCCESS", "SKIPPED"}:
            raise ValueError("INVALID_JOB_RESULT_STATUS")
        records = int(result.get("records_processed", 0))
        ledger.finish(run, status, records=records, error=result.get("reason"))
        return {"job": job_type, **result, "status": status}
    except Exception as exc:
        ledger.finish(run, "FAILED", error=type(exc).__name__)
        return {"job": job_type, "status": "FAILED", "reason": type(exc).__name__, "records_processed": 0}


def prune_job_history(session: Session | None = None, *, days: int | None = None) -> int:
    retention = days if days is not None else get_settings().job_history_retention_days
    if retention < 1:
        raise ValueError("JOB_HISTORY_RETENTION_DAYS must be positive")
    if session is None:
        return 0
    return SQLLedger(session).prune(retention)
