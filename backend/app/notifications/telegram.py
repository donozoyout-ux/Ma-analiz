import hashlib
from datetime import datetime, timezone
from typing import Protocol

import httpx
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.models import NotificationDelivery


class NotificationLedger(Protocol):
    def claim(self, fingerprint: str) -> bool: ...
    def mark_sent(self, fingerprint: str) -> None: ...
    def mark_failed(self, fingerprint: str) -> None: ...


class SQLNotificationLedger:
    def __init__(self, session: Session):
        self.session = session

    def claim(self, fingerprint: str) -> bool:
        row = self.session.execute(
            select(NotificationDelivery).where(NotificationDelivery.fingerprint == fingerprint).with_for_update()
        ).scalar_one_or_none()
        if row:
            if row.status == "FAILED":
                row.status = "PENDING"; self.session.commit(); return True
            self.session.rollback()
            return False
        self.session.add(NotificationDelivery(fingerprint=fingerprint, status="PENDING"))
        try:
            self.session.commit(); return True
        except IntegrityError:
            self.session.rollback(); return False

    def mark_sent(self, fingerprint: str) -> None:
        row = self.session.get(NotificationDelivery, fingerprint)
        if row:
            row.status = "SENT"; row.sent_at = datetime.now(timezone.utc); self.session.commit()

    def mark_failed(self, fingerprint: str) -> None:
        row = self.session.get(NotificationDelivery, fingerprint)
        if row:
            row.status = "FAILED"; self.session.commit()


class TelegramNotConfigured(RuntimeError):
    pass


class TelegramService:
    def __init__(self, ledger: NotificationLedger, client: httpx.AsyncClient | None = None):
        cfg = get_settings()
        if not (cfg.telegram_bot_token and cfg.telegram_chat_id):
            raise TelegramNotConfigured("TELEGRAM_NOT_CONFIGURED")
        self.token, self.chat_id = cfg.telegram_bot_token, cfg.telegram_chat_id
        self.ledger = ledger
        self.client = client or httpx.AsyncClient(timeout=10)

    async def send_message(self, text: str, *, dedupe_key: str | None = None) -> bool:
        fingerprint = hashlib.sha256((dedupe_key or text).encode("utf-8")).hexdigest()
        if not self.ledger.claim(fingerprint):
            return False
        try:
            response = await self.client.post(
                f"https://api.telegram.org/bot{self.token}/sendMessage",
                json={"chat_id": self.chat_id, "text": text},
            )
            response.raise_for_status()
            self.ledger.mark_sent(fingerprint)
            return True
        except Exception:
            self.ledger.mark_failed(fingerprint)
            raise

    async def send_daily_summary(self, text: str, day: str) -> bool:
        return await self.send_message(text, dedupe_key=f"daily-summary:{day}")

    async def send_prediction(self, text: str, prediction_id: str) -> bool:
        return await self.send_message(text, dedupe_key=f"prediction:{prediction_id}")

    async def send_coupon(self, text: str, coupon_id: str) -> bool:
        return await self.send_message(text, dedupe_key=f"coupon:{coupon_id}")

    async def send_result(self, text: str, prediction_id: str) -> bool:
        return await self.send_message(text, dedupe_key=f"result:{prediction_id}")

    async def send_lesson(self, text: str, lesson_id: str) -> bool:
        return await self.send_message(text, dedupe_key=f"lesson:{lesson_id}")

    async def send_system_alert(self, text: str, alert_key: str) -> bool:
        return await self.send_message(text, dedupe_key=f"alert:{alert_key}")
