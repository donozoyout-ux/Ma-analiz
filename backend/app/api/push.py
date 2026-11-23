import logging
from typing import Any
from fastapi import APIRouter, Depends, Header, HTTPException, Request, status

from app.push.models import (
    NotificationEvent,
    NotificationType,
    PushPreferences,
    PushSubscriptionCreate,
    PushSubscriptionUpdate,
    PushUnsubscribeRequest,
)
from app.push.service import PushNotificationService, get_push_service

logger = logging.getLogger("macanaliz.api.push")
router = APIRouter(prefix="/push", tags=["Push Notifications"])


def check_rate_limit(request: Request, service: PushNotificationService = Depends(get_push_service)) -> None:
    client_ip = request.client.host if request.client else "unknown"
    if not service.rate_limiter.is_allowed(client_ip):
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Çok fazla bildirim isteği gönderildi. Lütfen bir süre sonra tekrar deneyin.",
        )


@router.get("/public-key")
async def get_public_key(service: PushNotificationService = Depends(get_push_service)) -> dict[str, Any]:
    """Return the VAPID public key required by browser PushManager."""
    return {
        "status": "ok",
        "public_key": service.public_key,
        "enabled": service.is_enabled,
    }


@router.post("/subscribe", dependencies=[Depends(check_rate_limit)])
async def subscribe(
    payload: PushSubscriptionCreate,
    request: Request,
    service: PushNotificationService = Depends(get_push_service),
) -> dict[str, Any]:
    """Register or update a client push subscription.
    Client token is securely saved and used to verify future preference updates.
    """
    sub_id, is_new = service.store.add_or_update(payload)
    logger.info("Push subscription %s (new=%s)", sub_id[:8], is_new)
    return {
        "status": "subscribed",
        "subscription_id": sub_id,
        "is_new": is_new,
    }


@router.post("/unsubscribe", dependencies=[Depends(check_rate_limit)])
async def unsubscribe(
    payload: PushUnsubscribeRequest,
    service: PushNotificationService = Depends(get_push_service),
) -> dict[str, Any]:
    """Revoke and remove a client push subscription."""
    removed = service.store.remove(payload.endpoint, payload.client_token)
    if not removed:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Abonelik bulunamadı veya yetkisiz erişim anahtarı.",
        )
    return {"status": "unsubscribed"}


@router.get("/preferences")
async def get_preferences(
    client_token: str,
    service: PushNotificationService = Depends(get_push_service),
) -> dict[str, Any]:
    """Retrieve saved notification preferences for a client token."""
    records = service.store.get_by_token(client_token)
    if not records:
        return {
            "status": "not_found",
            "preferences": PushPreferences().model_dump(),
        }
    return {
        "status": "ok",
        "preferences": records[0].preferences.model_dump(),
    }


@router.put("/preferences", dependencies=[Depends(check_rate_limit)])
async def update_preferences(
    payload: PushSubscriptionUpdate,
    service: PushNotificationService = Depends(get_push_service),
) -> dict[str, Any]:
    """Update notification preferences for a verified client subscription."""
    success = service.store.update_preferences(payload.client_token, payload)
    if not success:
        return {
            "status": "no_active_subscription",
            "message": "Belirtilen istemci anahtarına ait aktif abonelik bulunamadı.",
        }
    return {"status": "updated"}


@router.post("/test", dependencies=[Depends(check_rate_limit)])
async def test_notification(
    service: PushNotificationService = Depends(get_push_service),
) -> dict[str, Any]:
    """Safe test dispatch trigger: broadcast a mock event to subscribers in dry-run mode."""
    test_event = NotificationEvent(
        event_type=NotificationType.VERIFIED_GOAL,
        match_id="test-demo-match",
        home_team="Galatasaray",
        away_team="Fenerbahçe",
        title="⚽ Doğrulanmış Gol! (Test)",
        body="Galatasaray 1 - 0 Fenerbahçe (28'). Maç Analiz bildirim sistemi test mesajı.",
        url="/matches",
        verified=True,
        detail_id="test-goal-1",
    )
    result = await service.broadcast_event(test_event)
    return {
        "status": "test_broadcast_complete",
        "result": result,
    }
