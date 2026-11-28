"""Read-only verified Bundesliga seasons from immutable R2 archives. No SQL."""
from __future__ import annotations

import hashlib
import json

from fastapi import APIRouter, HTTPException, Query

from app.storage.r2 import R2ObjectStore, parquet_rows

router = APIRouter(tags=["R2 Bundesliga archive"])
SEASONS = {
    "2024-2025": (306, 306, "f473d6595e6c29ebedc08b09177aed6a6b2ff0fa8378b82f51dea469139f44c3"),
    "2025-2026": (306, 294, "17d0999db6281e6365823acdbce41a4f01dd469eac63e1f897e4e47c02906672"),
    "2026-2027": (96, 33, "07fab65d4a1de8ce4c8472da9ccf4d1d2b145ecd5abdc5b6d4250a5c119d7681"),
}
REVISION = "e6744429ee395bc86f247348c6184bb08d4eb361"
CUTOFF = "2026-11-28"
BUCKET = "sdfgdsgdg"


def _digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def read_bundesliga_season(store, season: str) -> list[dict]:
    """Fail closed if any pinned manifest, source, or Parquet object disagrees."""
    if season not in SEASONS:
        raise ValueError("UNSUPPORTED_SEASON")
    expected_count, expected_scores, raw_sha = SEASONS[season]
    prefix = f"season-bulk/openfootball/season={season}/league=de.bl1"
    manifests = store.list_objects(f"{prefix}/manifests/")
    if len(manifests) != 1:
        raise ValueError("BUNDESLIGA_MANIFEST_NOT_UNIQUE")
    key = manifests[0].key
    manifest_raw = store.get_object(key)
    if key != f"{prefix}/manifests/{_digest(manifest_raw)}.json":
        raise ValueError("BUNDESLIGA_MANIFEST_HASH_MISMATCH")
    manifest = json.loads(manifest_raw)
    if (manifest.get("season") != season or manifest.get("league") != "de.bl1"
            or manifest.get("revision") != REVISION or manifest.get("licence") != "CC0-1.0"
            or manifest.get("cutoff") != CUTOFF or manifest.get("matches") != expected_count
            or manifest.get("scores") != expected_scores
            or manifest.get("score_verification") != "SOURCE_REPORTED"):
        raise ValueError("BUNDESLIGA_MANIFEST_SCOPE_MISMATCH")
    raw = manifest["raw"]
    matches = manifest["parquet"]
    if raw.get("sha256") != raw_sha or matches.get("row_count") != expected_count:
        raise ValueError("BUNDESLIGA_RECEIPT_SCOPE_MISMATCH")
    for item, expected_suffix in ((raw, "raw"), (matches, "matches")):
        object_key = item["key"]
        extension = "json" if expected_suffix == "raw" else "parquet"
        if object_key != f"{prefix}/{expected_suffix}/{item['sha256']}.{extension}":
            raise ValueError("BUNDESLIGA_RECEIPT_KEY_MISMATCH")
        body = store.get_object(object_key)
        if len(body) != item["size"] or _digest(body) != item["sha256"]:
            raise ValueError("BUNDESLIGA_OBJECT_HASH_MISMATCH")
        if expected_suffix == "matches":
            rows = parquet_rows(body)
    if (len(rows) != expected_count or
            len({row["id"] for row in rows}) != expected_count or
            sum(row["regulation"] is not None for row in rows) != expected_scores or
            any(row["league"] != "de.bl1" or row["season"] != season or
                row["date"] > CUTOFF for row in rows)):
        raise ValueError("BUNDESLIGA_ROWS_INTEGRITY_FAILED")
    return rows


@router.get("/archive/r2/bundesliga")
def archived_bundesliga(
    season: str = Query("2024-2025"),
    page: int = Query(1, ge=1, le=30),
    page_size: int = Query(25, ge=1, le=100),
    team: str = Query("", max_length=80),
):
    if season not in SEASONS:
        raise HTTPException(status_code=422, detail="UNSUPPORTED_SEASON")
    try:
        store = R2ObjectStore()
        try:
            if store.bucket != BUCKET:
                raise ValueError("UNEXPECTED_R2_BUCKET")
            rows = read_bundesliga_season(store, season)
        finally:
            close = getattr(store.client, "close", None)
            if callable(close):
                close()
    except Exception as exc:
        raise HTTPException(status_code=503, detail="R2_BUNDESLIGA_ARCHIVE_UNAVAILABLE") from exc
    lookup = team.strip().casefold()
    if lookup:
        rows = [row for row in rows if lookup in row["home_team"].casefold()
                or lookup in row["away_team"].casefold()]
    rows.sort(key=lambda row: (row["date"], row["id"]), reverse=True)
    offset = (page - 1) * page_size
    return {
        "season": season, "league": "de.bl1", "source": "R2",
        "total": len(rows), "page": page, "page_size": page_size,
        "matches": [
            {"id": row["id"], "date": row["date"],
             "home_team": row["home_team"], "away_team": row["away_team"],
             "round": row["round"], "status": row["status"],
             "regulation": row["regulation"], "verification": row["verification"]}
            for row in rows[offset:offset + page_size]
        ],
    }
