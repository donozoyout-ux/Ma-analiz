import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.push.crypto import VapidKeyManager, sanitize_endpoint
from app.push.dedup import DedupStore
from app.push.models import (
    NotificationEvent,
    NotificationType,
    PushPreferences,
    PushSubscriptionCreate,
    PushSubscriptionKeys,
    PushSubscriptionUpdate,
)
from app.push.service import PushNotificationService
from app.push.store import SubscriptionStore


@pytest.fixture
def isolated_service():
    store = SubscriptionStore()
    dedup = DedupStore()
    vapid = VapidKeyManager()
    return PushNotificationService(store=store, dedup=dedup, vapid=vapid)


@pytest.fixture
def client(monkeypatch):
    test_store = SubscriptionStore()
    test_dedup = DedupStore()
    test_vapid = VapidKeyManager()
    service = PushNotificationService(store=test_store, dedup=test_dedup, vapid=test_vapid)

    from app.api.push import get_push_service
    app.dependency_overrides[get_push_service] = lambda: service
    yield TestClient(app)
    app.dependency_overrides.clear()


def test_vapid_key_generation_and_headers():
    vapid = VapidKeyManager()
    assert len(vapid.public_key_b64) >= 64
    headers = vapid.create_vapid_headers("https://fcm.googleapis.com/fcm/send/test-sub")
    assert "Authorization" in headers
    assert headers["Authorization"].startswith("vapid t=")
    assert "Crypto-Key" in headers


def test_endpoint_sanitization():
    endpoint = "https://fcm.googleapis.com/fcm/send/secret-device-token-12345"
    sanitized = sanitize_endpoint(endpoint)
    assert "secret-device-token-12345" not in sanitized
    assert sanitized.startswith("https://fcm.googleapis.com/")


def test_dedup_store_prevents_duplicate_notifications():
    dedup = DedupStore(ttl_seconds=3600)
    # First time -> not duplicate (returns False)
    assert dedup.record_and_check("verified_goal", "match-123", "goal-1") is False

    # Second time -> DUPLICATE (returns True)
    assert dedup.record_and_check("verified_goal", "match-123", "goal-1") is True

    # Different goal -> not duplicate
    assert dedup.record_and_check("verified_goal", "match-123", "goal-2") is False


@pytest.mark.asyncio
async def test_rejection_of_unverified_events(isolated_service):
    # Unverified event (e.g. rumors, speculative scores) must fail closed
    unverified_event = NotificationEvent(
        event_type=NotificationType.VERIFIED_GOAL,
        match_id="m-99",
        home_team="Team A",
        away_team="Team B",
        title="Unverified Goal",
        body="Possible goal",
        verified=False,  # strictly False
    )
    result = await isolated_service.broadcast_event(unverified_event)
    assert result["status"] == "rejected"
    assert "Unverified" in result["reason"]


@pytest.mark.asyncio
async def test_duplicate_event_is_suppressed(isolated_service):
    event = NotificationEvent(
        event_type=NotificationType.VERIFIED_MATCH_RESULT,
        match_id="m-100",
        home_team="Galatasaray",
        away_team="Fenerbahçe",
        title="Maç Bitti",
        body="Galatasaray 2 - 1 Fenerbahçe (MS)",
        verified=True,
        detail_id="final-whistle",
    )
    first_res = await isolated_service.broadcast_event(event)
    assert first_res["status"] == "dry_run"

    # Second broadcast of same event
    second_res = await isolated_service.broadcast_event(event)
    assert second_res["status"] == "duplicate_suppressed"


@pytest.mark.asyncio
async def test_favorite_team_and_category_filtering(isolated_service):
    # Subscriber A: only follows Galatasaray, only goals
    sub_a = PushSubscriptionCreate(
        endpoint="https://push.example.com/sub_a",
        keys=PushSubscriptionKeys(p256dh="key-test-p256dh-abc-12345", auth="auth-secret-12345"),
        client_token="client-token-user-a-12345",
        preferences=PushPreferences(
            enabled=True,
            favorite_teams=["Galatasaray"],
            notification_types={
                NotificationType.VERIFIED_GOAL.value: True,
                NotificationType.MATCH_START_REMINDER.value: False,
            },
        ),
    )
    isolated_service.store.add_or_update(sub_a)

    # Event 1: Galatasaray Goal -> Eligible
    event_gs = NotificationEvent(
        event_type=NotificationType.VERIFIED_GOAL,
        match_id="m-gs",
        home_team="Galatasaray",
        away_team="Kasımpaşa",
        title="GOL",
        body="Galatasaray gol attı",
        verified=True,
    )
    res_gs = await isolated_service.broadcast_event(event_gs)
    assert res_gs["recipients_count"] == 1

    # Event 2: Arsenal vs Chelsea Goal -> Not eligible (user only follows GS)
    event_pl = NotificationEvent(
        event_type=NotificationType.VERIFIED_GOAL,
        match_id="m-pl",
        home_team="Arsenal",
        away_team="Chelsea",
        title="GOL",
        body="Arsenal gol attı",
        verified=True,
    )
    res_pl = await isolated_service.broadcast_event(event_pl)
    assert res_pl["recipients_count"] == 0

    # Event 3: Galatasaray Match Start -> Not eligible (toggle is False)
    event_start = NotificationEvent(
        event_type=NotificationType.MATCH_START_REMINDER,
        match_id="m-gs-start",
        home_team="Galatasaray",
        away_team="Kasımpaşa",
        title="Maç Başlıyor",
        body="Maç 15 dk sonra",
        verified=True,
    )
    res_start = await isolated_service.broadcast_event(event_start)
    assert res_start["recipients_count"] == 0


def test_api_subscription_lifecycle_and_auth(client):
    # 1. Public key endpoint
    res_pk = client.get("/api/v1/push/public-key")
    assert res_pk.status_code == 200
    data_pk = res_pk.json()
    assert "public_key" in data_pk
    assert data_pk["enabled"] is False  # Safe default

    # 2. Subscribe endpoint
    sub_payload = {
        "endpoint": "https://push.example.com/client-device-1",
        "keys": {
            "p256dh": "mock-p256dh-key-long-enough-for-validation",
            "auth": "mock-auth-secret-12345",
        },
        "client_token": "device-token-secret-xyz-987654",
        "preferences": {
            "enabled": True,
            "favorite_teams": ["Beşiktaş"],
            "notification_types": {
                "verified_goal": True,
            },
        },
    }
    res_sub = client.post("/api/v1/push/subscribe", json=sub_payload)
    assert res_sub.status_code == 200
    assert res_sub.json()["status"] == "subscribed"

    # 3. Get preferences with correct token
    res_pref = client.get("/api/v1/push/preferences?client_token=device-token-secret-xyz-987654")
    assert res_pref.status_code == 200
    assert res_pref.json()["preferences"]["favorite_teams"] == ["Beşiktaş"]

    # 4. Update preferences
    res_up = client.put(
        "/api/v1/push/preferences",
        json={
            "client_token": "device-token-secret-xyz-987654",
            "favorite_teams": ["Beşiktaş", "Liverpool"],
        },
    )
    assert res_up.status_code == 200
    assert res_up.json()["status"] == "updated"

    # Verify updated
    res_pref2 = client.get("/api/v1/push/preferences?client_token=device-token-secret-xyz-987654")
    assert "Liverpool" in res_pref2.json()["preferences"]["favorite_teams"]

    # 5. Unauthorized unsubscribe attempt (wrong token)
    res_unsub_bad = client.post(
        "/api/v1/push/unsubscribe",
        json={
            "endpoint": "https://push.example.com/client-device-1",
            "client_token": "wrong-token-attacker",
        },
    )
    assert res_unsub_bad.status_code == 404

    # 6. Authorized unsubscribe
    res_unsub_good = client.post(
        "/api/v1/push/unsubscribe",
        json={
            "endpoint": "https://push.example.com/client-device-1",
            "client_token": "device-token-secret-xyz-987654",
        },
    )
    assert res_unsub_good.status_code == 200
    assert res_unsub_good.json()["status"] == "unsubscribed"


def test_api_test_endpoint(client):
    res = client.post("/api/v1/push/test")
    assert res.status_code == 200
    assert res.json()["status"] == "test_broadcast_complete"
