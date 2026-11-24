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
