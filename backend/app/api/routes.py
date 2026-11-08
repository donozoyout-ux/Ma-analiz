from datetime import datetime, timezone
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.db.session import engine, get_db
from app.models import ActiveMatch, ActiveOdds, Coupon, CouponItem, DailyMetric, Lesson, Prediction
from app.storage.monitor import StorageMonitor

router = APIRouter()


def _serialize(model) -> dict:
    return {column.name: getattr(model, column.name) for column in model.__table__.columns}


@router.get("/health")
def health() -> dict[str, str]:
    settings = get_settings()
    database = "DATABASE_NOT_CONFIGURED"
    if engine is not None:
        try:
            with engine.connect() as connection:
                connection.execute(text("SELECT 1"))
            database = "ONLINE"
        except Exception:
            database = "OFFLINE"
    return {
        "status": "ok",
        "provider": "MOCK" if settings.match_provider == "mock" else "PROVIDER_NOT_CONFIGURED" if not settings.match_provider else "PROVIDER_UNSUPPORTED",
        "r2": "CONFIGURED" if settings.r2_endpoint and settings.r2_access_key_id and settings.r2_secret_access_key else "R2_NOT_CONFIGURED",
        "telegram": "CONFIGURED" if settings.telegram_bot_token and settings.telegram_chat_id else "TELEGRAM_NOT_CONFIGURED",
        "ai": "CONFIGURED" if settings.ai_provider and settings.ai_api_key else "AI_NOT_CONFIGURED",
        "database": database,
        "worker": "WORKER_HEARTBEAT_NOT_CONNECTED",
    }


@router.get("/system/status")
def system_status() -> dict:
    readiness = health()
    return {
        "status": readiness["status"],
        "provider_mode": "MOCK" if readiness["provider"] == "MOCK" else "LIVE" if readiness["provider"] == "ONLINE" else "NOT_CONFIGURED",
        "services": {key: readiness[key] for key in ("provider", "database", "r2", "worker", "telegram", "ai")},
    }


@router.get("/storage")
@router.get("/system/storage")
def storage() -> dict:
    return StorageMonitor(engine).snapshot()


@router.get("/matches")
def list_matches(db: Session = Depends(get_db), limit: int = 100) -> list[dict]:
    now = datetime.now(timezone.utc)
    rows = db.scalars(select(ActiveMatch).where(ActiveMatch.expires_at > now).order_by(ActiveMatch.start_time).limit(max(1, min(limit, 500)))).all()
    return [_serialize(row) for row in rows]


@router.get("/matches/{match_id}")
def match_detail(match_id: UUID, db: Session = Depends(get_db)) -> dict:
    match = db.get(ActiveMatch, match_id)
    if match is None:
        raise HTTPException(status_code=404, detail="MATCH_NOT_FOUND")
    data = _serialize(match)
    data["odds"] = [_serialize(row) for row in db.scalars(select(ActiveOdds).where(ActiveOdds.match_id == match_id).order_by(ActiveOdds.captured_at.desc()).limit(200)).all()]
    return data


@router.get("/predictions")
def list_predictions(db: Session = Depends(get_db), limit: int = 100) -> list[dict]:
    rows = db.scalars(select(Prediction).where(Prediction.expires_at > datetime.now(timezone.utc)).order_by(Prediction.created_at.desc()).limit(max(1, min(limit, 500)))).all()
    return [_serialize(row) for row in rows]


@router.get("/coupons")
def list_coupons(db: Session = Depends(get_db), limit: int = 100) -> list[dict]:
    rows = db.scalars(select(Coupon).where(Coupon.expires_at > datetime.now(timezone.utc)).order_by(Coupon.created_at.desc()).limit(max(1, min(limit, 500)))).all()
    result = []
    for coupon in rows:
        item = _serialize(coupon)
        item["items"] = [_serialize(row) for row in db.scalars(select(CouponItem).where(CouponItem.coupon_id == coupon.id)).all()]
        result.append(item)
    return result


def _daily_metrics(db: Session, limit: int) -> list[dict]:
    rows = db.scalars(select(DailyMetric).order_by(DailyMetric.date.desc()).limit(max(1, min(limit, 365)))).all()
    return [_serialize(row) for row in reversed(rows)]


@router.get("/performance")
def performance(db: Session = Depends(get_db), limit: int = 90) -> list[dict]:
    return _daily_metrics(db, limit)


@router.get("/metrics/daily")
def daily_metrics(db: Session = Depends(get_db), limit: int = 90) -> list[dict]:
    return _daily_metrics(db, limit)


@router.get("/lessons")
def lessons(db: Session = Depends(get_db), limit: int = 100) -> list[dict]:
    rows = db.scalars(select(Lesson).order_by(Lesson.created_at.desc()).limit(max(1, min(limit, 500)))).all()
    return [_serialize(row) for row in rows]
