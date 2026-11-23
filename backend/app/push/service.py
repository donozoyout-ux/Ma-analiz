import collections
import logging
import time
from typing import Any

from app.core.config import get_settings
from app.push.crypto import VapidKeyManager, sanitize_endpoint
from app.push.dedup import DedupStore
from app.push.models import NotificationEvent, NotificationType
from app.push.store import SubscriptionRecord, SubscriptionStore, get_subscription_store

logger = logging.getLogger("macanaliz.push")


class RateLimiter:
    """Sliding-window rate limiter per client IP or key."""

    def __init__(self, max_requests: int = 60, window_seconds: int = 60):
        self.max_requests = max_requests
        self.window = window_seconds
        self._history: dict[str, collections.deque[float]] = collections.defaultdict(collections.deque)

    def is_allowed(self, key: str) -> bool:
        now = time.time()
        q = self._history[key]
        cutoff = now - self.window
        while q and q[0] <= cutoff:
            q.popleft()
        if len(q) >= self.max_requests:
            return False
        q.append(now)
        return True


class PushNotificationService:
    def __init__(
        self,
        store: SubscriptionStore | None = None,
        dedup: DedupStore | None = None,
        vapid: VapidKeyManager | None = None,
    ):
        self.settings = get_settings()
        self.store = store or get_subscription_store()
        self.dedup = dedup or DedupStore()
        private_pem = (
            self.settings.vapid_private_key.get_secret_value()
            if self.settings.vapid_private_key
            else None
        )
        self.vapid = vapid or VapidKeyManager(
            private_key_pem=private_pem,
            public_key_b64=self.settings.vapid_public_key,
        )
        self.rate_limiter = RateLimiter(
            max_requests=self.settings.push_rate_limit_per_minute,
            window_seconds=60,
        )

    @property
    def public_key(self) -> str:
        return self.vapid.public_key_b64

    @property
    def is_enabled(self) -> bool:
        return bool(self.settings.push_notifications_enabled)

    @staticmethod
    def _team_matches(team: str, favorite_teams: list[str]) -> bool:
        """Case-insensitive Turkish-friendly team match check."""
        t_clean = team.strip().casefold()
        for fav in favorite_teams:
            if fav.strip().casefold() in t_clean or t_clean in fav.strip().casefold():
                return True
        return False

    def should_deliver(self, sub: SubscriptionRecord, event: NotificationEvent) -> bool:
        """Evaluate if subscriber should receive this event based on preferences."""
        if not sub.preferences.enabled:
            return False

        # 1. Check event type toggle
        type_key = event.event_type.value
        if not sub.preferences.notification_types.get(type_key, False):
            return False

        # 2. Check team filter (if user selected favorite teams)
        if sub.preferences.favorite_teams:
            home_matches = self._team_matches(event.home_team, sub.preferences.favorite_teams)
            away_matches = self._team_matches(event.away_team, sub.preferences.favorite_teams)
            if not (home_matches or away_matches):
                return False

        return True

    async def broadcast_event(self, event: NotificationEvent) -> dict[str, Any]:
        """Dispatch a verified notification event to eligible subscribers.
        Strict safeguards:
        - Rejects unverified data (fake scores, speculative odds, incomplete predictions).
        - Prevents duplicate alerts for the same event via DedupStore.
        - Respects default PUSH_NOTIFICATIONS_ENABLED=false (safe mock / dry run).
        """
        # Guard 1: Verify data integrity
        if not event.verified:
            logger.warning(
                "Push broadcast rejected: unverified event data for match=%s type=%s",
                event.match_id,
                event.event_type.value,
            )
            return {
                "status": "rejected",
                "reason": "Unverified or incomplete data cannot generate push notifications.",
            }

        # Guard 2: Deduplication
        is_dup = self.dedup.record_and_check(
            event_type=event.event_type.value,
            match_id=event.match_id,
            detail_id=event.detail_id,
        )
        if is_dup:
            logger.info(
                "Push broadcast suppressed as duplicate: event=%s match=%s detail=%s",
                event.event_type.value,
                event.match_id,
                event.detail_id,
            )
            return {
                "status": "duplicate_suppressed",
                "match_id": event.match_id,
                "event_type": event.event_type.value,
            }

        # Guard 3: Filter eligible subscribers
        active_subs = self.store.get_active_subscribers()
        eligible_subs = [s for s in active_subs if self.should_deliver(s, event)]

        recipients_count = len(eligible_subs)

        # Guard 4: Safe mode when push notifications disabled in environment
        if not self.is_enabled:
            logger.info(
                "Push dispatch recorded (safe dry-run, push_notifications_enabled=false): "
                "event=%s match=%s eligible_recipients=%d",
                event.event_type.value,
                event.match_id,
                recipients_count,
            )
            return {
                "status": "dry_run",
                "event_type": event.event_type.value,
                "match_id": event.match_id,
                "recipients_count": recipients_count,
                "enabled": False,
            }

        # Live dispatch (in production when explicitly enabled)
        delivered = 0
        failed = 0
        import httpx

        async with httpx.AsyncClient(timeout=10.0) as client:
            for sub in eligible_subs:
                try:
                    headers = self.vapid.create_vapid_headers(
                        sub.endpoint,
                        subscriber=self.settings.vapid_claims_sub,
                    )
                    headers["Content-Type"] = "application/json"
                    headers["Urgency"] = "high" if event.event_type == NotificationType.VERIFIED_GOAL else "normal"

                    payload = {
                        "title": event.title,
                        "body": event.body,
                        "icon": "/icon-192.png",
                        "badge": "/icon-192.png",
                        "tag": f"match-{event.match_id}-{event.event_type.value}",
                        "data": {
                            "url": event.url or f"/matches?matchId={event.match_id}",
                            "matchId": event.match_id,
                            "eventType": event.event_type.value,
                        },
                    }

                    # Mask endpoint in logs
                    masked = sanitize_endpoint(sub.endpoint)
                    resp = await client.post(sub.endpoint, json=payload, headers=headers)
                    if resp.is_success:
                        delivered += 1
                    elif resp.status_code in (404, 410):
                        # Subscription expired or revoked by device
                        endpoint_hash = SubscriptionStore.hash_endpoint(sub.endpoint)
                        self.store.record_failure(endpoint_hash)
                        failed += 1
                    else:
                        failed += 1
                        logger.warning("Push send returned status %d for %s", resp.status_code, masked)
                except Exception as ex:
                    failed += 1
                    logger.debug("Push send exception: %s", type(ex).__name__)

        return {
            "status": "delivered",
            "delivered": delivered,
            "failed": failed,
            "event_type": event.event_type.value,
        }


_service_instance: PushNotificationService | None = None


def get_push_service() -> PushNotificationService:
    global _service_instance
    if _service_instance is None:
        _service_instance = PushNotificationService()
    return _service_instance
