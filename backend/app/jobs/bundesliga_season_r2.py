"""Prepare pinned Bundesliga season archives; production writes require separate approval."""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import date
import hashlib
import json
import unicodedata

import httpx

from app.core.config import get_settings
from app.jobs.first_ucl_r2_archive import archive_verified
from app.providers.season_files import DOMESTIC_REV, DownloadBudget, download, parse_season, source_for
from app.storage.r2 import R2ObjectStore, parquet_bytes, parquet_rows

BUCKET = "sdfgdsgdg"
CUTOFF = date(2026, 11, 28)
APPROVAL = "ARCHIVE_BUNDESLIGA_2024_2027_R2_ONLY"
REVISION = "e6744429ee395bc86f247348c6184bb08d4eb361"
HASHES = {
    "2024-2025": "f473d6595e6c29ebedc08b09177aed6a6b2ff0fa8378b82f51dea469139f44c3",
    "2025-2026": "17d0999db6281e6365823acdbce41a4f01dd469eac63e1f897e4e47c02906672",
    "2026-2027": "07fab65d4a1de8ce4c8472da9ccf4d1d2b145ecd5abdc5b6d4250a5c119d7681",
}
DAILY_PREFIX = "daily/openligadb/season=2026-2027/league=de.bl1/"


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def canonical(obj: dict) -> bytes:
    return json.dumps(obj, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()


def team_name(name: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", name).casefold().split())


def match_key(day: str, home: str, away: str) -> tuple[str, str, str]:
    return day, team_name(home), team_name(away)


def prepare(season: str, raw: bytes) -> tuple[dict, list[tuple[str, bytes, str]]]:
    source = source_for("de.bl1", season)
    if source is None or source.revision != REVISION or DOMESTIC_REV != REVISION or source.licence != "CC0-1.0":
        raise RuntimeError("SOURCE_SCOPE_MISMATCH")
    if digest(raw) != HASHES[season]:
        raise RuntimeError("SOURCE_SHA256_MISMATCH")
    source_matches = json.loads(raw)["matches"]
    rows = parse_season(source, raw)
    if len(source_matches) != 306 or len(rows) != 306:
        raise RuntimeError("SOURCE_SEASON_COUNT_MISMATCH")
    rows = [row for row in rows if row["date"] <= CUTOFF.isoformat()]
    for row in rows:
        for score in (row["half_time"], row["regulation"]):
            if score is not None and (not isinstance(score, (list, tuple)) or len(score) != 2
                                      or any(type(value) is not int or value < 0 for value in score)):
                raise RuntimeError("INVALID_SOURCE_SCORE")
        if row["regulation"] is None:
            row["status"] = "UNKNOWN"
    keys = [match_key(row["date"], row["home_team"], row["away_team"]) for row in rows]
    if len(set(keys)) != len(rows) or len({row["id"] for row in rows}) != len(rows):
        raise RuntimeError("DUPLICATE_FIXTURE_IDENTITY")
    parquet = parquet_bytes(rows)
    if parquet_rows(parquet) != rows:
        raise RuntimeError("PARQUET_READBACK_MISMATCH")
    prefix = f"season-bulk/openfootball/season={season}/league=de.bl1"
    raw_info = {"key": f"{prefix}/raw/{digest(raw)}.json", "size": len(raw), "sha256": digest(raw)}
    match_info = {"key": f"{prefix}/matches/{digest(parquet)}.parquet", "size": len(parquet),
                  "sha256": digest(parquet), "row_count": len(rows)}
    manifest = canonical({"source": source.url, "revision": REVISION, "licence": source.licence,
                          "licence_url": source.licence_url, "league": "de.bl1", "season": season,
                          "cutoff": CUTOFF.isoformat(), "matches": len(rows),
                          "scores": sum(row["regulation"] is not None for row in rows),
                          "raw": raw_info, "parquet": match_info, "score_verification": "SOURCE_REPORTED"})
    manifest_key = f"{prefix}/manifests/{digest(manifest)}.json"
    report = {"season": season, "source_rows": len(source_matches), "matches": len(rows),
              "scores": sum(row["regulation"] is not None for row in rows),
              "half_time": sum(row["half_time"] is not None for row in rows),
              "unknown_status": sum(row["status"] == "UNKNOWN" for row in rows),
              "odds": 0, "sha256": digest(raw), "bytes": len(raw) + len(parquet) + len(manifest),
              "source_reported_only": True, "rows": rows}
    return report, [(raw_info["key"], raw, "application/json"),
                    (match_info["key"], parquet, "application/vnd.apache.parquet"),
                    (manifest_key, manifest, "application/json")]


def compare_daily(store, rows: list[dict]) -> dict:
    index = {match_key(row["date"], row["home_team"], row["away_team"]): row for row in rows}
    source_ids = {}
    fixture_ids = {}
    matched = set()
    unmatched = set()
    for item in store.list_objects(DAILY_PREFIX):
        data = store.get_object(item.key)
        head = store.head_object(item.key)
        if head is None or head.size != len(data) or (head.metadata or {}).get("sha256") != digest(data) or not item.key.endswith("/" + digest(data) + ".json"):
            raise RuntimeError("DAILY_ARCHIVE_INTEGRITY_FAILED")
        content = json.loads(data)
        if content.get("league") != "de.bl1" or content.get("provider") != "openligadb":
            raise RuntimeError("DAILY_ARCHIVE_SCOPE_MISMATCH")
        for row in content["matches"]:
            day = row["match_date"]
            if day > CUTOFF.isoformat():
                continue
            if row.get("league_id") != "de.bl1" or day != content.get("date"):
                raise RuntimeError("DAILY_ARCHIVE_SCOPE_MISMATCH")
            key = match_key(day, row["home_team"], row["away_team"])
            provider_id = str(row["provider_match_id"])
            if provider_id in source_ids and source_ids[provider_id] != key:
                raise RuntimeError("AMBIGUOUS_DAILY_IDENTITY")
            source_ids[provider_id] = key
            if key in index:
                fixture_id = index[key]["id"]
                fixture_ids.setdefault(fixture_id, set()).add(provider_id)
                matched.add(provider_id)
            else:
                unmatched.add(provider_id)
    ambiguous = sum(len(ids) > 1 for ids in fixture_ids.values())
    links = [{"openfootball_id": fixture_id, "openligadb_id": next(iter(ids))}
             for fixture_id, ids in sorted(fixture_ids.items()) if len(ids) == 1]
    return {"daily_unique_matches": len(source_ids), "matched": len(matched), "unmatched": len(unmatched),
            "ambiguous": ambiguous, "links": links, "merge_performed": False}


def run(*, live: bool = False, approval: str = "", store_factory=R2ObjectStore) -> dict:
    if live and approval != APPROVAL:
        raise RuntimeError("LIVE_APPROVAL_REQUIRED")
    if not live and approval:
        raise RuntimeError("DRY_RUN_APPROVAL_CONFLICT")
    if get_settings().r2_bucket != BUCKET:
        raise RuntimeError("BUCKET_MISMATCH")
    reports, objects = [], []
    with httpx.Client(trust_env=False) as client:
        budget = DownloadBudget()
        for season in HASHES:
            source = source_for("de.bl1", season)
            report, artifacts = prepare(season, download(source, client, budget))
            reports.append(report)
            objects.extend(artifacts)
    store = store_factory()
    try:
        if store.bucket != BUCKET:
            raise RuntimeError("BUCKET_MISMATCH")
        comparison = compare_daily(store, reports[-1]["rows"])
        # Record reviewed, exact cross-source identities without merging source records.
        old_key, manifest_data, kind = objects[-1]
        manifest = json.loads(manifest_data)
        manifest["daily_identity_comparison"] = comparison
        manifest_data = canonical(manifest)
        manifest_key = old_key.rsplit("/", 1)[0] + "/" + digest(manifest_data) + ".json"
        objects[-1] = (manifest_key, manifest_data, kind)
        reports[-1]["bytes"] = sum(len(data) for _, data, _ in objects[-3:])
        # Verify every existing candidate before any possible write.
        existing = 0
        for key, data, _ in objects:
            info = store.head_object(key)
            if info is None:
                continue
            body = store.get_object(key)
            if info.size != len(data) or (info.metadata or {}).get("sha256") != digest(data) or digest(body) != digest(data):
                raise RuntimeError("ARCHIVE_OBJECT_CONFLICT")
            existing += 1
        if live:
            if comparison["unmatched"] or comparison["ambiguous"]:
                raise RuntimeError("DAILY_IDENTITIES_REQUIRE_REVIEW")
            for key, data, kind in objects:
                archive_verified(store, key, data, kind)
        return {"status": "R2_ARCHIVE_VERIFIED" if live else "BLOCKED_AWAITING_LIVE_APPROVAL",
                "bucket": BUCKET, "revision": REVISION, "cutoff": CUTOFF.isoformat(),
                "seasons": [{k: v for k, v in report.items() if k != "rows"} for report in reports],
                "comparison": comparison, "object_count": len(objects), "existing_objects": existing,
                "total_bytes": sum(len(data) for _, data, _ in objects), "postgresql": "NOT_USED"}
    finally:
        store.client.close()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--approval", default="")
    args = parser.parse_args(argv)
    try:
        report = run(live=args.live, approval=args.approval)
    except Exception:
        print(json.dumps({"status": "BLOCKED", "reason": "PRECHECK_OR_ARCHIVE_FAILED"}))
        return 1
    print(json.dumps(report, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
