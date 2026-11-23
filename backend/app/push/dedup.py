import hashlib
import threading
import time


class DedupStore:
    """Thread-safe TTL deduplication store to prevent sending duplicate notifications for the same event."""

    def __init__(self, ttl_seconds: int = 86400):
        self._ttl = ttl_seconds
        self._lock = threading.Lock()
        self._records: dict[str, float] = {}

    def _cleanup_expired(self, now: float) -> None:
        expired = [k for k, exp in self._records.items() if exp <= now]
        for k in expired:
            del self._records[k]

    def make_event_key(self, event_type: str, match_id: str, detail_id: str | None = None) -> str:
        """Create a deterministic hash key for an event."""
        raw = f"{event_type}:{match_id}:{detail_id or ''}"
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()

    def is_duplicate(self, event_type: str, match_id: str, detail_id: str | None = None) -> bool:
        """Check if event has already been recorded without recording it."""
        key = self.make_event_key(event_type, match_id, detail_id)
        now = time.time()
        with self._lock:
            self._cleanup_expired(now)
            return key in self._records

    def record_and_check(self, event_type: str, match_id: str, detail_id: str | None = None) -> bool:
        """Atomically check and record an event.
        Returns:
            True if event was already recorded (DUPLICATE, DO NOT SEND).
            False if event is new (RECORDED, PROCEED WITH SEND).
        """
        key = self.make_event_key(event_type, match_id, detail_id)
        now = time.time()
        with self._lock:
            self._cleanup_expired(now)
            if key in self._records:
                return True  # Duplicate
            self._records[key] = now + self._ttl
            return False  # New event

    def clear(self) -> None:
        """Clear all deduplication records (for testing)."""
        with self._lock:
            self._records.clear()
