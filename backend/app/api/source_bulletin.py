"""Public, read-only OpenLigaDB fixture bulletin for the homepage.

One season request per competition per 15-minute cache interval, never a
fake prediction, score or odds. Source failures remain visible to clients.
No SQL, R2, scheduler or user secrets are accessed.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta
from threading import Lock
import time
from zoneinfo import ZoneInfo

from fastapi import APIRouter, HTTPException

from app.providers.openligadb import OpenLigaDBProvider, ATTRIBUTION

router = APIRouter(tags=["Public fixtures"])
ISTANBUL = ZoneInfo("Europe/Istanbul")
SOURCES = (
    ("bl1", "de.bl1", "Bundesliga"),
    ("ucl", "uefa.cl", "UEFA Champions League"),
    ("uel2026", "uefa.el", "UEFA Europa League"),
    ("uecl2026", "uefa.ecl", "UEFA Conference League"),
)
CACHE_SECONDS = 900
# A cache miss within the cooldown does not issue another external request.
COOLDOWN_SECONDS = 60
_LOCK = Lock()
_CACHE: dict[tuple[str, str, str], tuple[float, list[dict]]] = {}
_LAST_ATTEMPT: dict[str, float] = {}


def build_bulletin(day: date, *, provider_factory=OpenLigaDBProvider) -> dict:
    """Collect a bounded window; partial failure must never masquerade as empty data."""
    start = max(date(2026, 7, 1), day - timedelta(days=3))
    end = min(date(2027, 6, 30), day + timedelta(days=7))
    if end < start:
        raise ValueError("Day out of supported season")
    matches: list[dict] = []
    failures: list[dict] = []
    available: list[str] = []
    # Serialize cache misses within a single Render worker.
    with _LOCK:
        for shortcut, league_id, league_name in SOURCES:
            key = shortcut, start.isoformat(), end.isoformat()
            now = time.monotonic()
            cached = _CACHE.get(key)
            if cached and now - cached[0] < CACHE_SECONDS:
                rows = cached[1]
            elif shortcut in _LAST_ATTEMPT and now - _LAST_ATTEMPT[shortcut] < COOLDOWN_SECONDS:
                failures.append({"league_id": league_id, "reason": "SOURCE_COOLDOWN"})
                continue
            else:
                _LAST_ATTEMPT[shortcut] = now
                try:
                    with provider_factory(max_retries=1) as provider:
                        rows = provider.get_competition_window(shortcut, start, end)
                    if len(rows) > 200 or any(row["league_id"] != league_id for row in rows):
                        raise ValueError("SOURCE_SCOPE_MISMATCH")
                    _CACHE[key] = (time.monotonic(), rows)
                except Exception:
                    failures.append({"league_id": league_id, "reason": "SOURCE_UNAVAILABLE"})
                    continue
            available.append(league_id)
            for row in rows:
                matches.append({
                    "id": f"openligadb:{row['provider_match_id']}",
                    "provider_match_id": row["provider_match_id"],
                    "league_id": row["league_id"],
                    "league_name": league_name,
                    "home_team": row["home_team"],
                    "away_team": row["away_team"],
                    "start_time": row["start_time"].isoformat(),
                    "status": row["status"],
                    "score": row["score"],
                    "source": "OpenLigaDB",
                })
    matches.sort(key=lambda row: (row["start_time"], row["league_id"], row["provider_match_id"]))
    return {
        "day": day.isoformat(),
        "window_start": start.isoformat(),
        "window_end": end.isoformat(),
        "source": "OpenLigaDB",
        "attribution": ATTRIBUTION,
        "last_observed_at": datetime.now(ISTANBUL).isoformat(),
        "refresh_interval_seconds": CACHE_SECONDS,
        "live_guaranteed": False,
        "odds_available": False,
        "statistics_available": False,
        "available_leagues": available,
        "failures": failures,
        "matches": matches[:200],
    }


@router.get("/source-bulletin")
def source_bulletin():
    day = datetime.now(ISTANBUL).date()
    if not date(2026, 7, 1) <= day <= date(2027, 6, 23):
        raise HTTPException(status_code=422, detail="SEASON_OUT_OF_SCOPE")
    bulletin = build_bulletin(day)
    if not bulletin["available_leagues"]:
        raise HTTPException(status_code=503, detail="ALL_SOURCES_UNAVAILABLE")
    return bulletin
