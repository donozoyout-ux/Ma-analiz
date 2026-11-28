"""Read-only ledger API. Legacy predictions are intentionally not queried."""
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import case, exists, func, select
from sqlalchemy.orm import Session, aliased
from app.db.session import get_db
from app.models.prediction_ledger import PredictionPublication as P, PredictionSettlement as S

router = APIRouter(tags=["Prediction ledger"])


def ledger_query(*, start: date | None, end: date | None, tz: str, league: str | None,
                 market: str | None, model: str | None, status: str | None):
    if tz not in {"UTC", "Europe/Istanbul"} or (start and end and start > end):
        raise HTTPException(status_code=422, detail="INVALID_DATE_SCOPE")
    if status and status not in {"WON", "LOST", "PENDING", "VOID", "UNSCORED"}:
        raise HTTPException(status_code=422, detail="INVALID_STATUS")
    zone = ZoneInfo(tz)
    ranked = select(S.id.label("sid"), S.publication_id, S.outcome, S.version,
                    func.row_number().over(partition_by=S.publication_id, order_by=S.version.desc()).label("rn")).subquery()
    latest = select(ranked.c.sid, ranked.c.publication_id, ranked.c.outcome).where(ranked.c.rn == 1).subquery()
    outcome = func.coalesce(latest.c.outcome, "PENDING")
    successor = aliased(P)
    stmt = (select(P, outcome.label("outcome"), latest.c.sid)
            .outerjoin(latest, latest.c.publication_id == P.id)
            .where(~exists(select(1).where(successor.supersedes_id == P.id))))
    if start:
        try:
            lower = datetime.combine(start, time.min, zone).astimezone(timezone.utc)
        except OverflowError:
            raise HTTPException(status_code=422, detail="INVALID_DATE_SCOPE") from None
        stmt = stmt.where(P.published_at >= lower)
    if end:
        try:
            bound = datetime.combine(end + timedelta(days=1), time.min, zone).astimezone(timezone.utc)
        except OverflowError:
            raise HTTPException(status_code=422, detail="INVALID_DATE_SCOPE") from None
        stmt = stmt.where(P.published_at < bound)
    for column, value in ((P.league_id, league), (P.market, market), (P.model_version, model), (outcome, status)):
        if value:
            stmt = stmt.where(column == value)
    return stmt


@router.get("/prediction-ledger")
def ledger(start: date | None = None, end: date | None = None, tz: str = "UTC",
           league: str | None = None, market: str | None = None, model: str | None = None,
           status: str | None = None, limit: int = Query(50, ge=1, le=100),
           offset: int = Query(0, ge=0, le=10000), db: Session = Depends(get_db)):
    stmt = ledger_query(start=start, end=end, tz=tz, league=league, market=market, model=model, status=status)
    rows = db.execute(stmt.order_by(P.published_at.desc(), P.id.desc()).offset(offset).limit(limit + 1)).all()
    items = []
    for publication, outcome, settlement_id in rows[:limit]:
        result = db.get(S, settlement_id) if settlement_id else None
        items.append({"id": publication.id, "match_id": publication.match_id,
            "provider": publication.provider, "provider_match_id": publication.provider_match_id,
            "league_id": publication.league_id, "season": publication.season,
            "home_team": publication.home_team, "away_team": publication.away_team,
            "kickoff_at": publication.kickoff_at, "published_at": publication.published_at,
            "market": publication.market, "selection": publication.selection,
            "probability": publication.probability, "model_version": publication.model_version,
            "feature_version": publication.feature_version, "feature_sha256": publication.feature_sha256,
            "publication_version": publication.publication_version, "supersedes_publication_id": publication.supersedes_id,
            "outcome": outcome, "odds": None, "verified_at": result.verified_at if result else None,
            "settled_at": result.settled_at if result else None, "result_source": result.result_source if result else None,
            "home_score": result.home_score if result else None, "away_score": result.away_score if result else None})
    return {"items": items, "next_offset": offset + limit if len(rows) > limit else None,
            "timezone": tz, "legacy_included": False, "publication_delivery_verified": False}


@router.get("/prediction-ledger/metrics")
def metrics(start: date | None = None, end: date | None = None, tz: str = "UTC",
            league: str | None = None, market: str | None = None, model: str | None = None,
            status: str | None = None, db: Session = Depends(get_db)):
    selected = ledger_query(start=start, end=end, tz=tz, league=league, market=market, model=model, status=status).subquery()
    outcome = selected.c.outcome
    counts = db.execute(select(func.count(), *[func.coalesce(func.sum(case((outcome == code, 1), else_=0)), 0) for code in ("WON", "LOST", "PENDING", "VOID", "UNSCORED")])).one()
    total, won, lost, pending, void, unscored = map(int, counts)
    return {"total": total, "won": won, "lost": lost, "pending": pending, "void": void,
            "unscored": unscored, "provisional_sample_size": won + lost, "sample_size": 0, "hit_rate": None,
            "verification_status": "PUBLICATION_DELIVERY_UNVERIFIED",
            "timezone": tz, "legacy_included": False, "publication_delivery_verified": False}


@router.get("/prediction-ledger/{publication_id}/events")
def events(publication_id: str, db: Session = Depends(get_db)):
    from uuid import UUID
    try:
        key = UUID(publication_id)
    except ValueError:
        raise HTTPException(status_code=422, detail="INVALID_PUBLICATION_ID") from None
    publication = db.get(P, key)
    if publication is None:
        raise HTTPException(status_code=404, detail="PUBLICATION_NOT_FOUND")
    rows = db.scalars(select(S).where(S.publication_id == key).order_by(S.version).limit(1001)).all()
    if len(rows) > 1000:
        raise HTTPException(status_code=503, detail="EVENT_HISTORY_LIMIT_EXCEEDED")
    return {"publication_id": key, "supersedes_publication_id": publication.supersedes_id,
            "events": [{"id": row.id, "version": row.version, "outcome": row.outcome,
                        "supersedes_id": row.supersedes_id, "result_id": row.result_id,
                        "result_source": row.result_source, "verified_at": row.verified_at,
                        "settled_at": row.settled_at, "home_score": row.home_score, "away_score": row.away_score}
                       for row in rows], "publication_delivery_verified": False}
