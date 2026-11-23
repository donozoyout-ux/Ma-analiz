import hashlib
import hmac
import json
import logging
import os
from pathlib import Path
import secrets
import threading
import time
from typing import Any

from app.push.models import PushPreferences, PushSubscriptionCreate, PushSubscriptionUpdate

logger = logging.getLogger("macanaliz.push.store")

# Default persistent directory inside project
DEFAULT_STORAGE_DIR = Path(__file__).resolve().parents[3] / "data" / "push"


class SubscriptionRecord:
    def __init__(
        self,
        subscription_id: str,
        endpoint: str,
        p256dh: str,
        auth: str,
        client_token_hash: str,
        preferences: PushPreferences,
        created_at: float,
        updated_at: float,
        failure_count: int = 0,
    ):
        self.subscription_id = subscription_id
        self.endpoint = endpoint
        self.p256dh = p256dh
        self.auth = auth
        self.client_token_hash = client_token_hash
        self.preferences = preferences
        self.created_at = created_at
        self.updated_at = updated_at
        self.failure_count = failure_count

    def to_dict(self) -> dict[str, Any]:
        return {
            "subscription_id": self.subscription_id,
            "endpoint": self.endpoint,
            "p256dh": self.p256dh,
            "auth": self.auth,
            "client_token_hash": self.client_token_hash,
            "preferences": self.preferences.model_dump(),
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "failure_count": self.failure_count,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "SubscriptionRecord":
        return cls(
            subscription_id=data["subscription_id"],
            endpoint=data["endpoint"],
            p256dh=data["p256dh"],
            auth=data["auth"],
            client_token_hash=data["client_token_hash"],
            preferences=PushPreferences(**data["preferences"]),
            created_at=data.get("created_at", time.time()),
            updated_at=data.get("updated_at", time.time()),
            failure_count=data.get("failure_count", 0),
        )


class SubscriptionStore:
    """Thread-safe persistent store for Web Push subscriptions.
    Survives application restarts by persisting state atomically to disk.
    Does NOT touch production PostgreSQL or run migrations, keeping database zero-risk.
    """

    def __init__(self, storage_file: Path | str | None = None):
        self._lock = threading.Lock()
        self._subscriptions: dict[str, SubscriptionRecord] = {}
        self._id_index: dict[str, str] = {}

        if storage_file is not None:
            self._storage_path = Path(storage_file)
        else:
            self._storage_path = DEFAULT_STORAGE_DIR / "subscriptions.json"

        self._load_from_disk()

    @staticmethod
    def hash_endpoint(endpoint: str) -> str:
        return hashlib.sha256(endpoint.encode("utf-8")).hexdigest()

    @staticmethod
    def hash_token(token: str) -> str:
        return hashlib.sha256(token.encode("utf-8")).hexdigest()

    def _load_from_disk(self) -> None:
        """Load subscriptions from persistent storage on startup."""
        if not self._storage_path.exists():
            return
        try:
            with open(self._storage_path, "r", encoding="utf-8") as f:
                raw = json.load(f)
            records = raw.get("subscriptions", [])
            for r in records:
                try:
                    record = SubscriptionRecord.from_dict(r)
                    endpoint_hash = self.hash_endpoint(record.endpoint)
                    self._subscriptions[endpoint_hash] = record
                    self._id_index[record.subscription_id] = endpoint_hash
                except Exception as ex:
                    logger.warning("Corrupt subscription record skipped: %s", ex)
            logger.info("Loaded %d persistent push subscriptions from %s", len(self._subscriptions), self._storage_path)
        except Exception as ex:
            logger.error("Failed to load push subscriptions from disk: %s", ex)

    def _save_to_disk(self) -> None:
        """Persist state atomically to disk."""
        try:
            self._storage_path.parent.mkdir(parents=True, exist_ok=True)
            tmp_path = self._storage_path.with_suffix(".tmp")
            data = {
                "version": 1,
                "saved_at": time.time(),
                "subscriptions": [rec.to_dict() for rec in self._subscriptions.values()],
            }
            with open(tmp_path, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
            os.replace(tmp_path, self._storage_path)
        except Exception as ex:
            logger.error("Failed to persist push subscriptions to disk: %s", ex)

    def add_or_update(self, sub: PushSubscriptionCreate) -> tuple[str, bool]:
        """Add a new subscription or update an existing one.
        Returns (subscription_id, is_new).
        """
        endpoint_hash = self.hash_endpoint(sub.endpoint)
        token_hash = self.hash_token(sub.client_token)
        now = time.time()

        with self._lock:
            existing = self._subscriptions.get(endpoint_hash)
            if existing:
                if not hmac.compare_digest(existing.client_token_hash, token_hash):
                    existing.client_token_hash = token_hash
                existing.p256dh = sub.keys.p256dh
                existing.auth = sub.keys.auth
                existing.preferences = sub.preferences
                existing.updated_at = now
                existing.failure_count = 0
                self._save_to_disk()
                return existing.subscription_id, False

            sub_id = secrets.token_hex(16)
            record = SubscriptionRecord(
                subscription_id=sub_id,
                endpoint=sub.endpoint,
                p256dh=sub.keys.p256dh,
                auth=sub.keys.auth,
                client_token_hash=token_hash,
                preferences=sub.preferences,
                created_at=now,
                updated_at=now,
            )
            self._subscriptions[endpoint_hash] = record
            self._id_index[sub_id] = endpoint_hash
            self._save_to_disk()
            return sub_id, True

    def remove(self, endpoint: str, client_token: str) -> bool:
        """Remove a subscription if client_token matches."""
        endpoint_hash = self.hash_endpoint(endpoint)
        token_hash = self.hash_token(client_token)

        with self._lock:
            record = self._subscriptions.get(endpoint_hash)
            if not record:
                return False
            if not hmac.compare_digest(record.client_token_hash, token_hash):
                return False  # Unauthorized

            del self._subscriptions[endpoint_hash]
            if record.subscription_id in self._id_index:
                del self._id_index[record.subscription_id]
            self._save_to_disk()
            return True

    def get_by_token(self, client_token: str) -> list[SubscriptionRecord]:
        """Find active subscriptions belonging to this client_token."""
        token_hash = self.hash_token(client_token)
        with self._lock:
            return [
                r for r in self._subscriptions.values()
                if hmac.compare_digest(r.client_token_hash, token_hash)
            ]

    def update_preferences(self, client_token: str, update: PushSubscriptionUpdate) -> bool:
        """Update preferences for all subscriptions belonging to this token."""
        token_hash = self.hash_token(client_token)
        now = time.time()
        updated = False

        with self._lock:
            for record in self._subscriptions.values():
                if hmac.compare_digest(record.client_token_hash, token_hash):
                    if update.enabled is not None:
                        record.preferences.enabled = update.enabled
                    if update.favorite_teams is not None:
                        record.preferences.favorite_teams = update.favorite_teams
                    if update.notification_types is not None:
                        record.preferences.notification_types.update(update.notification_types)
                    record.updated_at = now
                    updated = True
            if updated:
                self._save_to_disk()
        return updated

    def get_active_subscribers(self) -> list[SubscriptionRecord]:
        """Return list of all currently active subscribers."""
        with self._lock:
            return [r for r in self._subscriptions.values() if r.preferences.enabled]

    def record_failure(self, endpoint_hash: str) -> None:
        """Increment failure counter and prune if endpoint is permanently dead (410 Gone / 404)."""
        with self._lock:
            record = self._subscriptions.get(endpoint_hash)
            if record:
                record.failure_count += 1
                if record.failure_count >= 5:
                    del self._subscriptions[endpoint_hash]
                    if record.subscription_id in self._id_index:
                        del self._id_index[record.subscription_id]
                self._save_to_disk()

    def count(self) -> int:
        with self._lock:
            return len(self._subscriptions)

    def clear(self) -> None:
        """Clear all subscriptions (for isolated tests)."""
        with self._lock:
            self._subscriptions.clear()
            self._id_index.clear()
            if self._storage_path.exists():
                try:
                    self._storage_path.unlink()
                except Exception:
                    pass


_global_store: SubscriptionStore | None = None


def get_subscription_store() -> SubscriptionStore:
    global _global_store
    if _global_store is None:
        _global_store = SubscriptionStore()
    return _global_store
