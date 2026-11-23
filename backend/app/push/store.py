import hashlib
import hmac
import secrets
import threading
import time
from typing import Any
from app.push.models import PushPreferences, PushSubscriptionCreate, PushSubscriptionUpdate


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
    ):
        self.subscription_id = subscription_id
        self.endpoint = endpoint
        self.p256dh = p256dh
        self.auth = auth
        self.client_token_hash = client_token_hash
        self.preferences = preferences
        self.created_at = created_at
        self.updated_at = updated_at
        self.failure_count = 0


class SubscriptionStore:
    """Thread-safe store for push subscriptions with token authentication.
    Does NOT touch PostgreSQL or perform any database migrations, keeping existing production data safe.
    """

    def __init__(self):
        self._lock = threading.Lock()
        # Key: endpoint_hash -> SubscriptionRecord
        self._subscriptions: dict[str, SubscriptionRecord] = {}
        # Key: subscription_id -> endpoint_hash
        self._id_index: dict[str, str] = {}

    @staticmethod
    def hash_endpoint(endpoint: str) -> str:
        return hashlib.sha256(endpoint.encode("utf-8")).hexdigest()

    @staticmethod
    def hash_token(token: str) -> str:
        return hashlib.sha256(token.encode("utf-8")).hexdigest()

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
                # Verify token match if updating
                if not hmac.compare_digest(existing.client_token_hash, token_hash):
                    # Different token for same endpoint: re-key with new token
                    existing.client_token_hash = token_hash
                existing.p256dh = sub.keys.p256dh
                existing.auth = sub.keys.auth
                existing.preferences = sub.preferences
                existing.updated_at = now
                existing.failure_count = 0
                return existing.subscription_id, False

            # Create new
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

    def count(self) -> int:
        with self._lock:
            return len(self._subscriptions)

    def clear(self) -> None:
        with self._lock:
            self._subscriptions.clear()
            self._id_index.clear()


_global_store = SubscriptionStore()


def get_subscription_store() -> SubscriptionStore:
    return _global_store
