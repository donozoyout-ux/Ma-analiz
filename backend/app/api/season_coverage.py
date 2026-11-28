"""Coverage and favorite reliability reads from the season import ledger."""
from collections import defaultdict
from datetime import datetime, timezone
from typing import Literal

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.analysis.season_favorites import analyze_favorites
from app.api.match_center import read_db
from app.storage.lifecycle import aware
from app.models.season_bulk import season_fixtures, season_odds
from app.providers.season_files import LEAGUES

router = APIRouter(prefix="/league-selection", tags=["season-coverage"])


@router.get("")
def coverage(season: Literal["2024-2025", "2025-2026", "2026-2027"], db: Session = Depends(read_db)):
    fixtures = defaultdict(list)
    observations = defaultdict(list)
    updated = {}
    now = datetime.now(timezone.utc)
    for data, verified, changed in db.execute(select(
        season_fixtures.c.data, season_fixtures.c.verified_at, season_fixtures.c.updated_at,
    ).where(season_fixtures.c.season <= season, season_fixtures.c.league.in_(LEAGUES))):
        league = data["league"]
        verified = aware(verified) if verified is not None else None
        kickoff = datetime.fromisoformat(data["kickoff_utc"]) if data["kickoff_utc"] else None
        valid = (verified is not None and kickoff is not None and kickoff <= verified <= now
                 and data["status"] == "FINISHED" and data["regulation"] is not None)
        fixtures[league].append({**data, "verified_at": verified.isoformat() if valid else None})
        if data["season"] == season:
            changed = aware(changed)
            updated[league] = max(updated.get(league, changed), changed)
    for league, data in db.execute(select(season_fixtures.c.league, season_odds.c.data)
        .join(season_fixtures, season_fixtures.c.id == season_odds.c.fixture_id)
        .where(season_fixtures.c.season <= season, season_fixtures.c.league.in_(LEAGUES))):
        observations[league].append(data)
    rows = []
    for league, (name, group) in LEAGUES.items():
        all_rows = fixtures[league]
        selected = [row for row in all_rows if row["season"] == season]
        stats = analyze_favorites(selected, observations[league])
        history = analyze_favorites(all_rows, observations[league])
        played_or_past = [row for row in selected if row["status"] == "FINISHED" or row["date"] < now.date().isoformat()]
        verified = sum(bool(row["verified_at"]) for row in played_or_past)
        rows.append({
            "league": league, "name": name, "group": group, "total": len(selected), "verified": verified,
            "odds": stats["odds_matches"],
            "missing_result_fraction": (len(played_or_past) - verified) / len(played_or_past) if played_or_past else None,
            "missing_odds_fraction": 1 - stats["odds_matches"] / len(selected) if selected else None,
            "updated_at": updated.get(league),
            "sources": sorted({(row["provider"], row["licence"]) for row in selected}),
            "favorites": stats, "history": history,
        })
    return {"season": season, "leagues": rows}
