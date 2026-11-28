"""Read-only, SHA-256 verified La Liga/Premier League R2 season snapshots."""
from __future__ import annotations

import hashlib
import json
from fastapi import APIRouter, HTTPException, Query

from app.storage.r2 import R2ObjectStore, parquet_rows
from app.jobs.es_en_season_r2 import REVISION, CUTOFF, BUCKET, source_for

router = APIRouter(tags=["R2 Spain and England archive"])
EXPECTED = {
    ("es.1", "2024-2025"): (380, 370, "da5bfa1b3634e1039d9e71aec50bdc847f8761189e3de235a4e730e0dc64ca38"),
    ("es.1", "2025-2026"): (380, 365, "b40397d8e62c640138548d57a1a9f76150549213d2a07fcbbb90ed0eabc6ccea"),
    ("es.1", "2026-2027"): (130, 64, "4b1a9906e3f2b279a1f25497a38f2f58e21ec19ea726b882fa646da7c07bc0e0"),
    ("en.pl", "2024-2025"): (380, 380, "c3d18f552d83382cd4b6b21127adbbfe38e7f8618ddc979fb5404f2df07d0a75"),
    ("en.pl", "2025-2026"): (380, 353, "d6070bdf731546ccf97767f062e8af4bd26dd309dee69f6092025bf9c78f43c1"),
    ("en.pl", "2026-2027"): (120, 45, "bb91f0ab3e8359df163bb9ec98549bfed85e200dd4c32f3bd606b5890d932dd2"),
}


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def read_season(store, league: str, season: str) -> list[dict]:
    key = (league, season)
    if key not in EXPECTED:
        raise ValueError("UNSUPPORTED_SCOPE")
    expected_rows, expected_scores, expected_raw_sha = EXPECTED[key]
    source = source_for(league, season)
    prefix = f"season-bulk/openfootball/season={season}/league={league}"
    manifests = store.list_objects(prefix + "/manifests/")
    if len(manifests) != 1:
        raise ValueError("MANIFEST_NOT_UNIQUE")
    manifest_key = manifests[0].key
    manifest_raw = store.get_object(manifest_key)
    if manifest_key != f"{prefix}/manifests/{digest(manifest_raw)}.json":
        raise ValueError("MANIFEST_SHA_MISMATCH")
    manifest = json.loads(manifest_raw)
    if (manifest.get("league") != league or manifest.get("season") != season or
            manifest.get("revision") != REVISION or manifest.get("source") != source.url or
            manifest.get("licence") != "CC0-1.0" or manifest.get("cutoff") != CUTOFF.isoformat() or
            manifest.get("matches") != expected_rows or manifest.get("scores") != expected_scores or
            manifest.get("score_verification") != "SOURCE_REPORTED"):
        raise ValueError("MANIFEST_SCOPE_MISMATCH")
    receipts = (("raw", manifest.get("raw"), "json"), ("matches", manifest.get("parquet"), "parquet"))
    rows = None
    for folder, receipt, extension in receipts:
        if not isinstance(receipt, dict):
            raise ValueError("MISSING_RECEIPT")
        object_key = receipt.get("key")
        sha = receipt.get("sha256")
        if object_key != f"{prefix}/{folder}/{sha}.{extension}":
            raise ValueError("RECEIPT_KEY_MISMATCH")
        data = store.get_object(object_key)
        if len(data) != receipt.get("size") or digest(data) != sha:
            raise ValueError("OBJECT_SHA_MISMATCH")
        if folder == "raw" and sha != expected_raw_sha:
            raise ValueError("PINNED_SOURCE_MISMATCH")
        if folder == "matches":
            if receipt.get("row_count") != expected_rows:
                raise ValueError("ROW_COUNT_MISMATCH")
            rows = parquet_rows(data)
    if (rows is None or len(rows) != expected_rows or len({r["id"] for r in rows}) != expected_rows or
            sum(r["regulation"] is not None for r in rows) != expected_scores or
            any(r["league"] != league or r["season"] != season or r["date"] > CUTOFF.isoformat()
                for r in rows)):
        raise ValueError("ARCHIVE_INTEGRITY_FAILED")
    return rows


@router.get("/archive/r2/es-en")
def archived_es_en(league: str = Query("es.1"), season: str = Query("2024-2025"),
                   team: str = Query("", max_length=80),
                   page: int = Query(1, ge=1, le=30),
                   page_size: int = Query(25, ge=1, le=100)):
    if (league, season) not in EXPECTED:
        raise HTTPException(status_code=422, detail="UNSUPPORTED_SCOPE")
    try:
        store = R2ObjectStore()
        try:
            if store.bucket != BUCKET:
                raise ValueError("BUCKET_MISMATCH")
            rows = read_season(store, league, season)
        finally:
            close = getattr(getattr(store, "client", None), "close", None)
            if callable(close):
                close()
    except Exception as exc:
        raise HTTPException(status_code=503, detail="R2_ES_EN_ARCHIVE_UNAVAILABLE") from exc
    needle = team.strip().casefold()
    if needle:
        rows = [r for r in rows if needle in r["home_team"].casefold() or
                needle in r["away_team"].casefold()]
    rows.sort(key=lambda r: (r["date"], r["id"]), reverse=True)
    offset = (page - 1) * page_size
    return {"season": season, "league": league, "source": "R2",
            "total": len(rows), "page": page, "page_size": page_size,
            "matches": [{"id": r["id"], "date": r["date"], "home_team": r["home_team"],
                         "away_team": r["away_team"], "round": r["round"], "status": r["status"],
                         "regulation": r["regulation"], "verification": r["verification"]}
                        for r in rows[offset:offset + page_size]]}
