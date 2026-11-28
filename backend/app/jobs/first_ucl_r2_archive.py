"""Archive pinned 2024-25 UCL fixtures to R2 without PostgreSQL access.

Default: source validation only, with zero R2 requests.
Live mode: opt-in put-if-absent, HEAD/GET/SHA256 verification, no deletes.
This archives files; it does not populate the application's SQL match tables.
"""
from __future__ import annotations

import argparse
import hashlib
import json

import httpx

from app.jobs.first_ucl_import import SHA256, validate_source
from app.providers.season_files import DownloadBudget, download, source_for
from app.storage.r2 import R2ObjectStore, parquet_bytes, parquet_row_count

APPROVAL = "ARCHIVE_UCL_2024_2025_R2_ONLY"
PREFIX = "season-bulk/openfootball/season=2024-2025/league=uefa.cl"


def _hash(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _canonical_json(data: dict) -> bytes:
    return json.dumps(data, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()


def archive_verified(store, key: str, payload: bytes, content_type: str, *, rows: int | None = None) -> dict:
    """Immutable insert or verified reuse; mismatch is an error, not an overwrite."""
    receipt = {"key": key, "size": len(payload), "sha256": _hash(payload)}
    if rows is not None:
        receipt["row_count"] = rows
    head = store.head_object(key)
    if head is None:
        try:
            store.put_object_if_absent(key, payload, content_type=content_type,
                                       metadata={"sha256": receipt["sha256"]})
        except FileExistsError:
            # Racing writer: permit reuse only after full readback verification.
            pass
    info = store.head_object(key)
    if (info is None or info.size != len(payload)
            or (info.metadata or {}).get("sha256") != receipt["sha256"]):
        raise RuntimeError("R2_ARCHIVE_RECEIPT_MISMATCH")
    body = store.get_object(key)
    if len(body) != len(payload) or _hash(body) != receipt["sha256"]:
        raise RuntimeError("R2_ARCHIVE_SHA256_MISMATCH")
    if rows is not None and parquet_row_count(body) != rows:
        raise RuntimeError("R2_ARCHIVE_ROW_COUNT_MISMATCH")
    return receipt


def archive_source_only(store, raw: bytes) -> dict:
    source = source_for("uefa.cl", "2024-2025")
    rows = validate_source(source, raw)
    # All checks run before the first R2 write.
    parquet = parquet_bytes(rows)
    if parquet_row_count(parquet) != 189:
        raise RuntimeError("PARQUET_ROW_COUNT_MISMATCH")
    raw_receipt = archive_verified(
        store, f"{PREFIX}/raw/{_hash(raw)}.txt", raw, "text/plain; charset=utf-8")
    matches_receipt = archive_verified(
        store, f"{PREFIX}/matches/{_hash(parquet)}.parquet",
        parquet, "application/vnd.apache.parquet", rows=189)
    manifest = _canonical_json({
        "league": "uefa.cl", "season": "2024-2025", "provider": "openfootball",
        "revision": source.revision, "source_url": source.url, "licence": source.licence,
        "fixtures": 189, "regulation_scores": 189,
        "raw": raw_receipt, "matches": matches_receipt,
        "sql_imported": False,
    })
    manifest_receipt = archive_verified(
        store, f"{PREFIX}/manifests/{_hash(manifest)}.json", manifest, "application/json")
    return {
        "status": "R2_ARCHIVE_VERIFIED", "league": "uefa.cl", "season": "2024-2025",
        "fixtures": 189, "regulation_scores": 189, "source_sha256": SHA256,
        "objects_verified": 3, "receipts": {
            "raw": raw_receipt, "matches": matches_receipt, "manifest": manifest_receipt,
        }, "postgresql": "NOT_USED",
    }


def run(*, live: bool = False, approval: str = "", store_factory=R2ObjectStore) -> dict:
    if live and approval != APPROVAL:
        raise RuntimeError("R2_OPERATOR_APPROVAL_REQUIRED")
    if not live and approval:
        raise RuntimeError("DRY_RUN_DOES_NOT_ACCEPT_APPROVAL")
    source = source_for("uefa.cl", "2024-2025")
    with httpx.Client(trust_env=False) as client:
        raw = download(source, client, DownloadBudget(remaining_attempts=3))
    rows = validate_source(source, raw)
    if not live:
        return {
            "status": "DRY_RUN", "fixtures": len(rows), "source_sha256": SHA256,
            "r2": "NOT_ACCESSED", "postgresql": "NOT_USED",
        }
    store = store_factory()
    try:
        return archive_source_only(store, raw)
    finally:
        close = getattr(getattr(store, "client", None), "close", None)
        if callable(close):
            close()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--approval", default="")
    args = parser.parse_args(argv)
    try:
        result = run(live=args.live, approval=args.approval)
    except Exception as exc:
        recognized = {
            "R2_OPERATOR_APPROVAL_REQUIRED", "DRY_RUN_DOES_NOT_ACCEPT_APPROVAL",
            "SOURCE_SCOPE_MISMATCH", "SOURCE_SHA256_MISMATCH",
            "SOURCE_MATCH_COUNT_MISMATCH", "PARQUET_ROW_COUNT_MISMATCH",
            "R2_ARCHIVE_RECEIPT_MISMATCH", "R2_ARCHIVE_SHA256_MISMATCH",
            "R2_ARCHIVE_ROW_COUNT_MISMATCH",
        }
        print(json.dumps({"status": "BLOCKED", "reason": str(exc) if str(exc) in recognized else "R2_ARCHIVE_FAILED"}))
        return 1
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
