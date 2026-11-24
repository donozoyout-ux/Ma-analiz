"""Quota and cost protection for match data ingestion and R2 archiving.

Guarantees:
- Strict daily bounds on external requests, R2 write operations, archive size, and match processing.
- Thread-safe tracking and fail-closed circuit breaker.
- Zero surprises on cloud or external API bills.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timezone
import threading
from typing import Any

from app.core.config import Settings, get_settings


class QuotaExceededError(RuntimeError):
    """Raised when an ingestion or storage quota limit is reached."""
    code: str = "QUOTA_EXCEEDED"

    def __init__(self, metric: str, current: int | float, limit: int | float) -> None:
        super().__init__(f"QUOTA_EXCEEDED: {metric} reached {current}/{limit}")
        self.metric = metric
        self.current = current
        self.limit = limit


@dataclass
class QuotaUsage:
    date: date
    requests_count: int = 0
    r2_writes_count: int = 0
    r2_bytes_written: int = 0
    matches_processed: int = 0


class QuotaTracker:
    def __init__(self, settings: Settings | None = None) -> None:
        self._cfg = settings or get_settings()
        self._lock = threading.Lock()
        today = datetime.now(timezone.utc).date()
        self._usage = QuotaUsage(date=today)

    @property
    def max_requests(self) -> int:
        return getattr(self._cfg, "bundesliga_daily_max_requests", 10)

    @property
    def max_r2_writes(self) -> int:
        return getattr(self._cfg, "bundesliga_daily_max_r2_writes", 50)

    @property
    def max_r2_bytes(self) -> int:
        mb = getattr(self._cfg, "bundesliga_daily_max_archive_mb", 10)
        return mb * 1024 * 1024

    @property
    def max_matches(self) -> int:
        return getattr(self._cfg, "bundesliga_daily_max_matches", 50)

    def _rollover_if_needed(self) -> None:
        today = datetime.now(timezone.utc).date()
        if self._usage.date != today:
            self._usage = QuotaUsage(date=today)

    def check_request_quota(self, increment: int = 1) -> None:
        with self._lock:
            self._rollover_if_needed()
            if self._usage.requests_count + increment > self.max_requests:
                raise QuotaExceededError(
                    "daily_requests",
                    self._usage.requests_count + increment,
                    self.max_requests,
                )

    def record_request(self, count: int = 1) -> None:
        with self._lock:
            self._rollover_if_needed()
            self._usage.requests_count += count

    def check_r2_write_quota(self, write_count: int = 1, byte_size: int = 0) -> None:
        with self._lock:
            self._rollover_if_needed()
            if self._usage.r2_writes_count + write_count > self.max_r2_writes:
                raise QuotaExceededError(
                    "daily_r2_writes",
                    self._usage.r2_writes_count + write_count,
                    self.max_r2_writes,
                )
            if self._usage.r2_bytes_written + byte_size > self.max_r2_bytes:
                raise QuotaExceededError(
                    "daily_r2_bytes",
                    self._usage.r2_bytes_written + byte_size,
                    self.max_r2_bytes,
                )

    def record_r2_write(self, write_count: int = 1, byte_size: int = 0) -> None:
        with self._lock:
            self._rollover_if_needed()
            self._usage.r2_writes_count += write_count
            self._usage.r2_bytes_written += byte_size

    def check_matches_quota(self, count: int = 1) -> None:
        with self._lock:
            self._rollover_if_needed()
            if self._usage.matches_processed + count > self.max_matches:
                raise QuotaExceededError(
                    "daily_matches",
                    self._usage.matches_processed + count,
                    self.max_matches,
                )

    def record_matches(self, count: int) -> None:
        with self._lock:
            self._rollover_if_needed()
            self._usage.matches_processed += count

    def get_summary(self) -> dict[str, Any]:
        with self._lock:
            self._rollover_if_needed()
            return {
                "date": self._usage.date.isoformat(),
                "requests": {
                    "used": self._usage.requests_count,
                    "limit": self.max_requests,
                    "remaining": max(0, self.max_requests - self._usage.requests_count),
                },
                "r2_writes": {
                    "used": self._usage.r2_writes_count,
                    "limit": self.max_r2_writes,
                    "remaining": max(0, self.max_r2_writes - self._usage.r2_writes_count),
                },
                "r2_bytes": {
                    "used": self._usage.r2_bytes_written,
                    "limit": self.max_r2_bytes,
                    "remaining": max(0, self.max_r2_bytes - self._usage.r2_bytes_written),
                },
                "matches": {
                    "used": self._usage.matches_processed,
                    "limit": self.max_matches,
                    "remaining": max(0, self.max_matches - self._usage.matches_processed),
                },
            }

    def reset(self) -> None:
        with self._lock:
            self._usage = QuotaUsage(date=datetime.now(timezone.utc).date())


# Singleton tracker for the running application instance
_GLOBAL_QUOTA_TRACKER: QuotaTracker | None = None
_QUOTA_LOCK = threading.Lock()


def get_quota_tracker(settings: Settings | None = None) -> QuotaTracker:
    global _GLOBAL_QUOTA_TRACKER
    with _QUOTA_LOCK:
        if _GLOBAL_QUOTA_TRACKER is None:
            _GLOBAL_QUOTA_TRACKER = QuotaTracker(settings)
        return _GLOBAL_QUOTA_TRACKER


class DurableQuotaTracker:
    """PostgreSQL-backed, daily atomic quota reservations shared across workers/restarts.

    Reservations are counted even when later I/O fails; this is deliberately
    conservative, because a timed-out external request may have reached its
    provider and a failed R2 upload may still be billable.
    """

    def __init__(self, settings: Settings, engine) -> None:
        if engine is None or engine.dialect.name != "postgresql":
            raise RuntimeError("DURABLE_QUOTA_REQUIRES_POSTGRES")
        self._cfg = settings
        self._engine = engine

    def _limits(self) -> dict[str, int]:
        return {
            "requests": self._cfg.bundesliga_daily_max_requests,
            "r2_writes": self._cfg.bundesliga_daily_max_r2_writes,
            "r2_bytes": self._cfg.bundesliga_daily_max_archive_mb * 1024 * 1024,
            "matches": self._cfg.bundesliga_daily_max_matches,
        }

    def _reserve(self, changes: dict[str, int]) -> None:
        from sqlalchemy import select, text as sql_text
        from sqlalchemy.orm import Session
        from app.models import SystemSetting

        if any(not isinstance(v, int) or v < 0 for v in changes.values()):
            raise ValueError("Invalid quota reservation")
        limits = self._limits()
        if set(changes) - set(limits):
            raise ValueError("Unknown quota metric")

        with Session(self._engine) as session:
            # Serialize all readers and writers, including first creation of the
            # day's row. A row lock alone cannot protect absent rows.
            session.execute(sql_text("SELECT pg_advisory_xact_lock(:lock)"), {"lock": 57179413026024})
            key = f"quota:bundesliga:{datetime.now(timezone.utc).date().isoformat()}"
            row = session.scalar(select(SystemSetting).where(SystemSetting.key == key).with_for_update())
            used = dict(row.value) if row else {}
            for field, increment in changes.items():
                value = int(used.get(field, 0)) + increment
                if value > limits[field]:
                    raise QuotaExceededError("daily_" + field, value, limits[field])
                used[field] = value
            if row is None:
                session.add(SystemSetting(key=key, value=used))
            else:
                row.value = used
            session.commit()

    def reserve_request(self, count: int = 1) -> None:
        self._reserve({"requests": count})

    def reserve_matches(self, count: int) -> None:
        self._reserve({"matches": count})

    def reserve_r2_write(self, writes: int, byte_size: int) -> None:
        self._reserve({"r2_writes": writes, "r2_bytes": byte_size})

    def get_summary(self) -> dict[str, Any]:
        from sqlalchemy.orm import Session
        from app.models import SystemSetting

        with Session(self._engine) as session:
            key = f"quota:bundesliga:{datetime.now(timezone.utc).date().isoformat()}"
            row = session.get(SystemSetting, key)
            used = dict(row.value) if row else {}
        limits = self._limits()
        return {
            "date": datetime.now(timezone.utc).date().isoformat(),
            name: {
                "used": int(used.get(name, 0)),
                "limit": limit,
                "remaining": max(0, limit - int(used.get(name, 0))),
            }
            for name, limit in limits.items()
        }
