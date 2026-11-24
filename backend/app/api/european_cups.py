"""Read-only European cups discovery and single-day fixtures API.

Never writes to PostgreSQL/R2 and never infers betting odds. 
Source may be incomplete because OpenLigaDB is community maintained.
"""
from __future__ import annotations

from datetime import date
import threading
import time

from fastapi import APIRouter, HTTPException, Query

from app.providers.european_cups import EUROPEAN_CUPS
from app.providers.openligadb import OpenLigaDBProvider, ATTRIBUTION

router = APIRouter(tags=["European competitions"])
_LOCK = threading.Lock()
_CACHE: dict[tuple[str, str], tuple[float, list[dict]]] = {}
_CACHE_SECONDS = 900
_LAST_SOURCE_CALL = 0.0
_MIN_SOURCE_INTERVAL = 60.0


@router.get("/competitions/europe")
def european_competitions():
    """Catalog only: zero external calls."""
    return {
        "season": "2026-2027",
        "provider": "openligadb",
        "odds_available": False,
        "attribution": ATTRIBUTION,
        "competitions": [{"slug": slug, **item} for slug, item in EUROPEAN_CUPS.items()],
    }


@router.get("/competitions/europe/{slug}/matches")
def european_matches(
    slug: str,
    day: date = Query(..., description="Europe/Istanbul local date YYYY-MM-DD"),
):
    global _LAST_SOURCE_CALL
    entry = EUROPEAN_CUPS.get(slug)
    if entry is None:
        raise HTTPException(status_code=404, detail="COMPETITION_NOT_FOUND")
    if not date(2026, 7, 1) <= day <= date(2027, 6, 30):
        raise HTTPException(status_code=422, detail="SEASON_OUT_OF_SCOPE")
    key = (slug, day.isoformat())

    # A single lock prevents concurrent cache-miss storms on one Render worker.
    # Requests beyond quota return 429, not fabricated or stale source data.
    with _LOCK:
        cached = _CACHE.get(key)
        if cached and time.monotonic() - cached[0] < _CACHE_SECONDS:
            matches = cached[1]
        else:
            if time.monotonic() - _LAST_SOURCE_CALL < _MIN_SOURCE_INTERVAL:
                raise HTTPException(status_code=429, detail="SOURCE_REQUEST_THROTTLED")
            _LAST_SOURCE_CALL = time.monotonic()
            try:
                with OpenLigaDBProvider(max_retries=1) as provider:
                    matches = provider.get_competition_matches(entry["shortcut"], day)
            except Exception as exc:
                # No provider URLs, tokens or server tracebacks in public error detail.
                raise HTTPException(status_code=503, detail="OPENLIGADB_UNAVAILABLE") from exc
            if len(matches) > 150:
                raise HTTPException(status_code=503, detail="SOURCE_SCOPE_LIMIT")
            _CACHE[key] = (time.monotonic(), matches)

    return {
        "league_id": entry["league_id"],
        "league_name": entry["name"],
        "season": entry["season"],
        "date": day.isoformat(),
        "timezone": "Europe/Istanbul",
        "provider": "openligadb",
        "attribution": ATTRIBUTION,
        "odds_available": False,
        "matches": matches,
    }
