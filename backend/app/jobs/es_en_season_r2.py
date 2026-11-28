"""Immutable, score-only La Liga/EPL season archives. No SQL, odds, statistics or workers.

Default command is read-only dry-run. Live mode requires an explicit scoped
approval and a correctly configured R2 bucket. Never edits Bundesliga objects.
"""
from __future__ import annotations

import argparse
from datetime import date
import hashlib
import json

import httpx

from app.core.config import get_settings
from app.jobs.first_ucl_r2_archive import archive_verified
from app.providers.season_files import Source, DownloadBudget, download, parse_season
from app.storage.r2 import R2ObjectStore, parquet_bytes, parquet_rows

BUCKET = "sdfgdsgdg"
REVISION = "e6744429ee395bc86f247348c6184bb08d4eb361"
CUTOFF = date(2026, 11, 28)
APPROVAL = "ARCHIVE_ES_EN_2024_2027_R2_ONLY"
LEAGUES = {"es.1": "La Liga", "en.pl": "Premier League"}
FILES = {"es.1": "es.1.json", "en.pl": "en.1.json"}
SEASONS = ("2024-2025", "2025-2026", "2026-2027")


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def canonical(data: dict) -> bytes:
    return json.dumps(data, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()


def source_for(league: str, season: str) -> Source:
    if league not in LEAGUES or season not in SEASONS:
        raise ValueError("UNSUPPORTED_SCOPE")
    year = f"{season[:4]}-{season[-2:]}"
    root = f"https://raw.githubusercontent.com/openfootball/football.json/{REVISION}"
    return Source(league, season, "openfootball", "football.json",
                  f"{root}/{year}/{FILES[league]}", "CC0-1.0",
                  f"{root}/LICENSE.md", REVISION)


def prepare(league: str, season: str, raw: bytes):
    source = source_for(league, season)
    source_rows = json.loads(raw).get("matches")
    if not isinstance(source_rows, list) or len(source_rows) != 380:
        raise ValueError("SOURCE_EXPECTED_380_FIXTURES")
    rows = parse_season(source, raw)
    if len(rows) != 380:
        raise ValueError("PARSE_COUNT_MISMATCH")
    rows = [r for r in rows if r["date"] <= CUTOFF.isoformat()]
    for row in rows:
        if row["league"] != league or row["season"] != season or row["provider"] != "openfootball":
            raise ValueError("ROW_SCOPE_MISMATCH")
        for field in ("half_time", "regulation"):
            value = row[field]
            if value is not None and (not isinstance(value, list) or len(value) != 2
                                      or any(type(n) is not int or n < 0 for n in value)):
                raise ValueError("INVALID_SOURCE_SCORE")
        if row["regulation"] is None:
            row["status"] = "UNKNOWN"
    identities = [(r["date"], r["home_team"].casefold(), r["away_team"].casefold()) for r in rows]
    if len(identities) != len(set(identities)) or len({r["id"] for r in rows}) != len(rows):
        raise ValueError("DUPLICATE_MATCH_IDENTITY")
    if not rows:
        raise ValueError("NO_MATCHES_IN_SCOPE")
    parquet = parquet_bytes(rows)
    if parquet_rows(parquet) != rows:
        raise ValueError("PARQUET_READBACK_MISMATCH")
    prefix = f"season-bulk/openfootball/season={season}/league={league}"
    raw_receipt = {"key": f"{prefix}/raw/{digest(raw)}.json", "sha256": digest(raw), "size": len(raw)}
    parquet_receipt = {"key": f"{prefix}/matches/{digest(parquet)}.parquet",
                       "sha256": digest(parquet), "size": len(parquet), "row_count": len(rows)}
    manifest = canonical({
        "league": league, "season": season, "source": source.url,
        "revision": REVISION, "licence": source.licence, "licence_url": source.licence_url,
        "cutoff": CUTOFF.isoformat(), "matches": len(rows),
        "scores": sum(r["regulation"] is not None for r in rows),
        "score_verification": "SOURCE_REPORTED", "raw": raw_receipt, "parquet": parquet_receipt,
        "odds": 0, "statistics": "NOT_INCLUDED", "postgresql": "NOT_USED",
    })
    objects = [(raw_receipt["key"], raw, "application/json"),
               (parquet_receipt["key"], parquet, "application/vnd.apache.parquet"),
               (f"{prefix}/manifests/{digest(manifest)}.json", manifest, "application/json")]
    return {"league": league, "season": season, "matches": len(rows),
            "scores": sum(r["regulation"] is not None for r in rows),
            "unknown_scores": sum(r["regulation"] is None for r in rows),
            "raw_sha256": digest(raw), "objects": len(objects)}, objects


def execute(*, live=False, approval="", fetcher=None, store_factory=R2ObjectStore):
    if live and approval != APPROVAL:
        raise ValueError("LIVE_APPROVAL_REQUIRED")
    if not live and approval:
        raise ValueError("DRY_RUN_APPROVAL_CONFLICT")
    reports, artifacts = [], []
    if fetcher is None:
        with httpx.Client(trust_env=False) as client:
            budget = DownloadBudget(remaining_attempts=18, remaining_bytes=12 * 1024 * 1024)
            for league in LEAGUES:
                for season in SEASONS:
                    source = source_for(league, season)
                    report, objects = prepare(league, season, download(source, client, budget))
                    reports.append(report)
                    artifacts.extend(objects)
    else:
        for league in LEAGUES:
            for season in SEASONS:
                report, objects = prepare(league, season, fetcher(source_for(league, season)))
                reports.append(report)
                artifacts.extend(objects)
    # All six data files must pass before a store is opened or any object is written.
    if not live:
        return {"status": "DRY_RUN_VALIDATED", "seasons": reports,
                "objects_planned": len(artifacts), "r2": "NOT_ACCESSED",
                "postgresql": "NOT_USED", "odds_api_credits": 0}
    if get_settings().r2_bucket != BUCKET:
        raise ValueError("R2_BUCKET_MISMATCH")
    store = store_factory()
    try:
        if store.bucket != BUCKET:
            raise ValueError("R2_BUCKET_MISMATCH")
        # Reject every conflicting object before the first potential write.
        existing = 0
        for key, body, _ in artifacts:
            info = store.head_object(key)
            if info is not None:
                if info.size != len(body) or (info.metadata or {}).get("sha256") != digest(body) or store.get_object(key) != body:
                    raise ValueError("R2_OBJECT_CONFLICT")
                existing += 1
        for key, body, content_type in artifacts:
            archive_verified(store, key, body, content_type,
                             rows=next((report["matches"] for report in reports
                                       if key.startswith(f"season-bulk/openfootball/season={report['season']}/league={report['league']}/matches/")), None))
        return {"status": "R2_ARCHIVE_VERIFIED", "seasons": reports, "objects_verified": len(artifacts),
                "previously_existing": existing, "postgresql": "NOT_USED", "odds_api_credits": 0}
    finally:
        close = getattr(getattr(store, "client", None), "close", None)
        if callable(close):
            close()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--approval", default="")
    args = parser.parse_args(argv)
    try:
        result = execute(live=args.live, approval=args.approval)
    except Exception:
        print(json.dumps({"status": "BLOCKED", "reason": "SOURCE_OR_ARCHIVE_PREFLIGHT_FAILED"}))
        return 1
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
