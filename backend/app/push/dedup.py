import hashlib
import json
import logging
import os
from pathlib import Path
import threading
import time

logger = logging.getLogger("macanaliz.push.dedup")

DEFAULT_DEDUP_FILE = Path(__file__).resolve().parents[3] / "data" / "push" / "dedup.json"


class DedupStore:
    """Thread-safe persistent TTL deduplication store.
    Prevents duplicate notifications for the same event even across process restarts.
    """

    def __init__(self, ttl_seconds: int = 86400, storage_file: Path | str | None = None):
        self._ttl = ttl_seconds
        self._lock = threading.Lock()
        self._records: dict[str, float] = {}

        if storage_file is not None:
            self._storage_path = Path(storage_file)
        else:
            self._storage_path = DEFAULT_DEDUP_FILE

        self._load_from_disk()

    def _cleanup_expired(self, now: float) -> bool:
        expired = [k for k, exp in self._records.items() if exp <= now]
        for k in expired:
            del self._records[k]
        return len(expired) > 0

    def _load_from_disk(self) -> None:
        if not self._storage_path.exists():
            return
        try:
            with open(self._storage_path, "r", encoding="utf-8") as f:
                raw = json.load(f)
            now = time.time()
            records = raw.get("records", {})
            # Keep only unexpired records
            for key, exp in records.items():
                if exp > now:
                    self._records[key] = float(exp)
            logger.info("Loaded %d deduplication keys from %s", len(self._records), self._storage_path)
        except Exception as ex:
            logger.warning("Failed to load dedup file: %s", ex)

    def _save_to_disk(self) -> None:
        try:
            self._storage_path.parent.mkdir(parents=True, exist_ok=True)
            tmp_path = self._storage_path.with_suffix(".tmp")
            data = {
                "version": 1,
                "saved_at": time.time(),
                "records": self._records,
            }
            with open(tmp_path, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False)
            os.replace(tmp_path, self._storage_path)
        except Exception as ex:
            logger.error("Failed to save dedup file: %s", ex)

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
            cleaned = self._cleanup_expired(now)
            if key in self._records:
                if cleaned:
                    self._save_to_disk()
                return True  # Duplicate
            self._records[key] = now + self._ttl
            self._save_to_disk()
            return False  # New event

    def clear(self) -> None:
        """Clear all deduplication records (for isolated testing)."""
        with self._lock:
            self._records.clear()
            if self._storage_path.exists():
                try:
                    self._storage_path.unlink()
                except Exception:
                    pass
