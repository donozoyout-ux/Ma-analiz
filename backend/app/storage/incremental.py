"""Immutable daily partitions with append-only, identity-scoped supplements.

Old daily objects remain readable. Late arrivals never overwrite an archive.
Every reused object is downloaded and verified before it counts as persisted.
"""
from __future__ import annotations

import hashlib
import json

from app.storage.archive import _verify_object, archive_partition


def row_identity(dataset: str, row: dict) -> str:
    if dataset == "odds":
        fields = ("provider", "provider_match_id", "provider_odds_id", "bookmaker", "market",
                  "selection", "line", "snapshot_type", "source_captured_at")
        if row.get("odds_id"):
            return str(row["odds_id"])
    elif dataset == "statistics":
        fields = ("provider", "provider_match_id", "statistic_id")
    elif dataset == "results" and row.get("result_id"):
        return str(row["result_id"])
    elif dataset == "predictions":
        return str(row["prediction_id"])
    else:
        fields = ("provider", "provider_match_id", "provider_result_id", "match_id")
    return json.dumps([str(row.get(field, "")) for field in fields], separators=(",", ":"))


def archive_incremental(store, tracker, *, dataset, season, league, day, rows):
    if not rows:
        return {"records_written": 0, "size_bytes": 0}
    # Validate segments before any object-store request.
    from app.storage.archive import _safe_partition
    for segment in (dataset, season, league):
        _safe_partition(segment)
    prefix = f"{dataset}/season={season}/league={league}/date={day.isoformat()}"
    existing_ids = set()
    existing_rows = {}
    found = [obj for obj in store.list_objects(prefix)
             if obj.key == prefix + ".parquet" or
             (obj.key.startswith(prefix + "/part=") and obj.key.endswith(".parquet"))]
    for obj in found:
        info = store.head_object(obj.key)
        if info is None:
            raise IOError("Archive object is missing")
        metadata = info.metadata or {}
        verified_rows = _verify_object(store, obj.key, expected_size=info.size,
                                       expected_rows=int(metadata.get("row-count", "-1")),
                                       expected_checksum=metadata.get("sha256", ""))
        for row in verified_rows:
            identity = row_identity(dataset, row)
            existing_ids.add(identity)
            existing_rows[identity] = row
    unique = {}
    for row in rows:
        identity = row_identity(dataset, row)
        if identity in existing_ids:
            # Retrieval/verification time changes on retry, source data must not.
            previous = existing_rows[identity]
            ignored = {"retrieved_at", "verified_at", "available_at"}
            if any(previous[field] != row[field] for field in previous.keys() & row.keys() - ignored):
                raise ValueError("Conflicting previously archived source record; source retained")
        if identity not in existing_ids:
            if identity in unique and unique[identity] != row:
                raise ValueError("Conflicting duplicate archive identity")
            unique[identity] = row
    if not unique:
        return {"records_written": 0, "size_bytes": 0}
    ordered = [unique[key] for key in sorted(unique)]
    part = hashlib.sha256("\n".join(sorted(unique)).encode()).hexdigest()
    receipt = archive_partition(store, tracker=tracker, dataset=dataset, season=season,
                                league=league, day=day, rows=ordered,
                                part=part if found else None)
    return {"records_written": receipt.row_count, "size_bytes": receipt.size,
            "key": receipt.key, "checksum": receipt.checksum}
