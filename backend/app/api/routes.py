from datetime import datetime, timezone
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.db.session import engine, get_db
from app.models import ActiveMatch, ActiveOdds, ArchiveJob, Coupon, CouponItem, DailyMetric, JobRun, Lesson, Prediction
from app.providers.registry import provider_status
from app.storage.monitor import StorageMonitor

router = APIRouter()


def _serialize(model) -> dict:
    return {column.name: getattr(model, column.name) for column in model.__table__.columns}


def health() -> dict[str, str]:
    settings = get_settings()
    database = "DATABASE_NOT_CONFIGURED"
    worker, archive = "UNKNOWN", "NO_VERIFIED_ARCHIVES"
    if engine is not None:
        try:
            with engine.connect() as connection:
                connection.execute(text("SELECT 1"))
                latest_job = connection.execute(select(JobRun.status).order_by(JobRun.started_at.desc()).limit(1)).scalar_one_or_none()
                latest_archive = connection.execute(select(ArchiveJob.status).order_by(ArchiveJob.created_at.desc()).limit(1)).scalar_one_or_none()
            database = "ONLINE"
            worker = latest_job or "NO_JOB_RUNS"
            archive = latest_archive or "NO_ARCHIVES"
        except Exception:
            database = "OFFLINE"
            worker = "UNKNOWN"
            archive = "UNKNOWN"
    provider = provider_status()
    return {
        "status": "ok",
        "provider": provider,
        "database": database,
        "r2": "CONFIGURED" if settings.r2_endpoint and settings.r2_access_key_id and settings.r2_secret_access_key else "R2_NOT_CONFIGURED",
        "worker": worker,
        "archive": archive,
        "telegram": "CONFIGURED" if settings.telegram_bot_token and settings.telegram_chat_id else "TELEGRAM_NOT_CONFIGURED",
        "ai": "CONFIGURED" if settings.ai_provider and settings.ai_api_key else "AI_NOT_CONFIGURED",
    }


@router.get("/health")
def health_endpoint() -> dict[str, str]:
    return health()


@router.get("/system/status")
def system_status() -> dict:
    readiness = health()
    return {
        "status": readiness["status"],
        "provider_mode": readiness["provider"],
        "services": {key: readiness[key] for key in
                     ("provider", "database", "r2", "worker", "archive", "telegram", "ai")},
    }


@router.get("/storage")
@router.get("/system/storage")
def storage() -> dict:
    result = StorageMonitor(engine).snapshot()
    result["r2"]["daily_growth_bytes"] = None
    if engine is not None:
        try:
            with engine.connect() as connection:
                metrics = connection.execute(
                    select(DailyMetric.date, DailyMetric.r2_bytes)
                    .where(DailyMetric.r2_bytes.is_not(None))
                    .order_by(DailyMetric.date.desc()).limit(2)
                ).all()
            if len(metrics) == 2:
                result["r2"]["daily_growth_bytes"] = int(metrics[0].r2_bytes) - int(metrics[1].r2_bytes)
        except Exception:
            pass
    return result


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
    odds = [_serialize(row) for row in db.scalars(select(ActiveOdds).where(ActiveOdds.match_id == match_id).order_by(ActiveOdds.captured_at.desc()).limit(200)).all()]
    data["odds"] = odds
    data["current_odds"] = odds[0] if odds else None
    data["opening_odds"] = odds[-1] if odds else None
    data["odds_history"] = odds
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
