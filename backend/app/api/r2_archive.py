"""Read verified, pinned UCL 2024/25 R2 archive without PostgreSQL.

The archive is immutable: inspect the content-addressed manifest and verify the
Parquet object before returning any match to the web UI.
"""
from __future__ import annotations

import hashlib
import json

from fastapi import APIRouter, HTTPException, Query

from app.storage.r2 import R2ObjectStore, parquet_rows

router = APIRouter(tags=["R2 archive"])
PREFIX = "season-bulk/openfootball/season=2024-2025/league=uefa.cl"
SOURCE_SHA256 = "c2692b22af02c5df4587b69c5f430aebf7d936845b5337aee462ce8e649eb1b5"


def _sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def read_verified_archive(store) -> list[dict]:
    """Reject missing, ambiguous or corrupted archive; never silently fall back."""
    manifests = store.list_objects(f"{PREFIX}/manifests/")
    if len(manifests) != 1:
        raise ValueError("ARCHIVE_MANIFEST_NOT_UNIQUE")
    item = manifests[0]
    manifest_raw = store.get_object(item.key)
    if item.key != f"{PREFIX}/manifests/{_sha(manifest_raw)}.json":
        raise ValueError("ARCHIVE_MANIFEST_HASH_MISMATCH")
    manifest = json.loads(manifest_raw)
    if (manifest.get("league") != "uefa.cl" or manifest.get("season") != "2024-2025"
            or manifest.get("provider") != "openfootball" or manifest.get("fixtures") != 189
            or manifest.get("regulation_scores") != 189):
        raise ValueError("ARCHIVE_MANIFEST_SCOPE_MISMATCH")
    source_receipt, receipt = manifest["raw"], manifest["matches"]
    if (source_receipt.get("sha256") != SOURCE_SHA256
            or not source_receipt.get("key", "").startswith(f"{PREFIX}/raw/")
            or not receipt.get("key", "").startswith(f"{PREFIX}/matches/")
            or receipt.get("row_count") != 189):
        raise ValueError("ARCHIVE_RECEIPT_SCOPE_MISMATCH")
    for ref in (source_receipt, receipt):
        body = store.get_object(ref["key"])
        if len(body) != ref["size"] or _sha(body) != ref["sha256"]:
            raise ValueError("ARCHIVE_OBJECT_HASH_MISMATCH")
        if ref is receipt:
            rows = parquet_rows(body)
    if len(rows) != 189 or len({r["id"] for r in rows}) != 189:
        raise ValueError("ARCHIVE_ROW_COUNT_MISMATCH")
    if sum(r["regulation"] is not None for r in rows) != 189:
        raise ValueError("ARCHIVE_SCORE_COUNT_MISMATCH")
    if any(r["league"] != "uefa.cl" or r["season"] != "2024-2025" for r in rows):
        raise ValueError("ARCHIVE_ROW_SCOPE_MISMATCH")
    return rows


@router.get("/archive/r2/champions-league")
def archived_champions_league(
    page: int = Query(1, ge=1, le=20),
    page_size: int = Query(25, ge=1, le=100),
    team: str = Query("", max_length=80),
):
    try:
        store = R2ObjectStore()
        try:
            rows = read_verified_archive(store)
        finally:
            close = getattr(store.client, "close", None)
            if callable(close):
                close()
    except Exception as exc:
        # Never return connection information, credentials, or internal bucket names.
        raise HTTPException(status_code=503, detail="R2_ARCHIVE_UNAVAILABLE") from exc
    search = team.casefold().strip()
    if search:
        rows = [r for r in rows if search in r["home_team"].casefold()
                or search in r["away_team"].casefold()]
    rows.sort(key=lambda r: (r["date"], r["id"]), reverse=True)
    offset = (page - 1) * page_size
    return {"season": "2024-2025", "league": "uefa.cl", "source": "R2",
            "total": len(rows), "page": page, "page_size": page_size,
            "matches": [{
                "id": r["id"], "date": r["date"], "kickoff_utc": r["kickoff_utc"],
                "home_team": r["home_team"], "away_team": r["away_team"],
                "status": r["status"], "regulation": r["regulation"],
                "round": r["round"], "verification": r["verification"]
            } for r in rows[offset: offset + page_size]]}
