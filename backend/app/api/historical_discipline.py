"""Read-only 2024-27 corner, yellow and red card archives.

All data are source-reported, permission-gated imported observations.
Not official match verification, betting odds, or a prediction result ledger.
"""
from __future__ import annotations

from datetime import datetime, timezone
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, or_, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.api.match_center import read_db
from app.historical.discipline_stats import summarize
from app.models.historical_discipline import HistoricalDisciplineMatch as H
from app.models.metadata import ArchiveJob as A

router = APIRouter(prefix="/historical-discipline", tags=["Historical corners and cards"])
LEAGUES = {"en.pl", "de.bl1", "es.ll"}
SEASONS = {"2024-2025", "2025-2026", "2026-2027"}
FIELDS = ("home_team", "away_team", "home_corners", "away_corners",
          "home_yellows", "away_yellows", "home_reds", "away_reds")


def filtered_query(*, league: str | None, season: str | None,
                   team: str | None, as_of: datetime | None = None):
    if league is not None and league not in LEAGUES:
        raise HTTPException(422, detail="INVALID_LEAGUE")
    if season is not None and season not in SEASONS:
        raise HTTPException(422, detail="INVALID_SEASON")
    if team and (len(team) > 150 or not team.strip()):
        raise HTTPException(422, detail="INVALID_TEAM")
    # Only SQL rows whose read-back-verified R2 ledger is still VERIFIED.
    stmt = select(H).join(A, (A.r2_key == H.r2_archive_key) &
        (A.checksum == H.r2_archive_checksum) &
        (A.status == "VERIFIED") & (A.dataset_type == "match-statistics"))
    if league:
        stmt = stmt.where(H.league_id == league)
    if season:
        stmt = stmt.where(H.season == season)
    if team:
        normalized = team.strip().lower()
        stmt = stmt.where(or_(func.lower(H.home_team) == normalized, func.lower(H.away_team) == normalized))
    if as_of:
        if as_of.tzinfo is None or as_of.utcoffset() is None:
            raise HTTPException(422, detail="AWARE_AS_OF_REQUIRED")
        # Historical CSVs imported today must NEVER appear as if already known
        # to a model that ran before the import; match_date is not known_at.
        stmt = stmt.where(H.ingested_at <= as_of.astimezone(timezone.utc),
                          H.match_date < as_of.date())
    return stmt


def _limited(session, stmt, *, max_rows=5000):
    try:
        rows = session.scalars(stmt.limit(max_rows + 1)).all()
    except SQLAlchemyError:
        raise HTTPException(503, detail="HISTORICAL_DISCIPLINE_UNAVAILABLE") from None
    if len(rows) > max_rows:
        raise HTTPException(503, detail="HISTORICAL_DISCIPLINE_SCOPE_TOO_LARGE")
    return rows


@router.get("/summary")
def summary(
    league: str | None = Query(None),
    season: str | None = Query(None),
    team: str | None = Query(None, max_length=150),
    db: Session = Depends(read_db),
):
    rows = _limited(db, filtered_query(league=league, season=season, team=team))
    payload = summarize(({key: getattr(row, key) for key in FIELDS} for row in rows),
                        team=(team or "").strip())
    return {
        "source": "football-data.co.uk",
        "source_coverage": "2024/25–2026/27; only manually imported rows",
        "league": league,
        "season": season,
        "team": team,
        **payload,
        "verified_match_outcomes": False,
        "archive_integrity": "VERIFIED_R2_LEDGER" if rows else "NO_VERIFIED_ARCHIVE_ROWS",
        "verified_r2_partitions": len({row.r2_archive_key for row in rows}),
        "odds_included": False,
        "last_observed_at": max((row.ingested_at for row in rows), default=None),
        "note": "Kaynakça bildirilen korner/kart istatistikleri; eksik veriler sıfır sayılmaz. Lisanslı içe aktarım yoksa kayıt bulunmaz.",
    }


@router.get("/matches")
def matches(
    league: str | None = Query(None),
    season: str | None = Query(None),
    team: str | None = Query(None, max_length=150),
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0, le=5000),
    db: Session = Depends(read_db),
):
    stmt = filtered_query(league=league, season=season, team=team)
    try:
        rows = db.scalars(stmt.order_by(H.match_date.desc(), H.source_key).limit(limit + 1).offset(offset)).all()
    except SQLAlchemyError:
        raise HTTPException(503, detail="HISTORICAL_DISCIPLINE_UNAVAILABLE") from None
    return {
        "items": [{
            "id": row.source_key, "league_id": row.league_id,
            "season": row.season, "date": row.match_date.isoformat(),
            "home_team": row.home_team, "away_team": row.away_team,
            "home_corners": row.home_corners, "away_corners": row.away_corners,
            "home_yellows": row.home_yellows, "away_yellows": row.away_yellows,
            "home_reds": row.home_reds, "away_reds": row.away_reds,
            "source_url": row.source_url, "ingested_at": row.ingested_at,
            "archive_status": "VERIFIED_R2_LEDGER",
        } for row in rows[:limit]],
        "next_offset": offset + limit if len(rows) > limit else None,
        "source": "football-data.co.uk",
        "official_result_verified": False,
        "archive_integrity": "VERIFIED_R2_LEDGER" if rows else "NO_VERIFIED_ARCHIVE_ROWS",
    }
