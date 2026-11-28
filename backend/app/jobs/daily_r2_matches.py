"""Bounded daily OpenLigaDB -> immutable R2 snapshots, without PostgreSQL.

Dry-run is default. Live requires explicit operator approval and writes one
content-addressed snapshot per nonempty supported league/date, never overwrites.
Schedule this command only after a separately reviewed deployment and R2 access.
"""
from __future__ import annotations

import argparse
from datetime import date, datetime
import hashlib
import json
from zoneinfo import ZoneInfo

from app.jobs.first_ucl_r2_archive import archive_verified
from app.providers.openligadb import OpenLigaDBProvider
from app.providers.european_cups import EUROPEAN_CUPS
from app.storage.r2 import R2ObjectStore

TZ = ZoneInfo("Europe/Istanbul")
APPROVAL = "ARCHIVE_DAILY_2026_2027_R2_ONLY"
SOURCES = {"bundesliga": {"league": "de.bl1", "shortcut": "bl1"}, **{
    slug: {"league": info["league_id"], "shortcut": info["shortcut"]}
    for slug, info in EUROPEAN_CUPS.items()
}}


def canonical(payload: dict) -> bytes:
    return json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()


def validate_matches(matches: list[dict], day: date, league: str) -> list[dict]:
    if not isinstance(matches, list) or len(matches) > 150:
        raise ValueError("INVALID_DAILY_MATCH_COUNT")
    seen = set()
    safe = []
    for row in matches:
        if not isinstance(row, dict) or row.get("league_id") != league or row.get("match_date") != day:
            raise ValueError("DAILY_MATCH_SCOPE_MISMATCH")
        mid = str(row.get("provider_match_id", ""))
        if not mid or mid in seen:
            raise ValueError("DAILY_DUPLICATE_MATCH_ID")
        seen.add(mid)
        if not row.get("home_team") or not row.get("away_team") or not row.get("start_time"):
            raise ValueError("DAILY_INCOMPLETE_MATCH")
        serialized = {k: (v.isoformat() if isinstance(v, (date, datetime)) else v) for k, v in row.items()}
        safe.append(serialized)
    return sorted(safe, key=lambda row: row["provider_match_id"])


def run(*, day: date, live: bool = False, approval: str = "",
        leagues: tuple[str, ...] = ("bundesliga",),
        provider_factory=OpenLigaDBProvider, store_factory=R2ObjectStore) -> dict:
    if not date(2026, 7, 1) <= day <= date(2027, 6, 30):
        raise RuntimeError("DAY_OUT_OF_REVIEWED_SEASON")
    if live and approval != APPROVAL:
        raise RuntimeError("DAILY_R2_OPERATOR_APPROVAL_REQUIRED")
    if not live and approval:
        raise RuntimeError("DRY_RUN_APPROVAL_CONFLICT")
    if not leagues or len(set(leagues)) != len(leagues) or any(league not in SOURCES for league in leagues):
        raise RuntimeError("UNSUPPORTED_LEAGUE")
    results = []
    with provider_factory(max_retries=1) as provider:
        for slug in leagues:
            league = SOURCES[slug]["league"]
            matches = (provider.get_matches(day) if slug == "bundesliga" else
                       provider.get_competition_matches(SOURCES[slug]["shortcut"], day))
            rows = validate_matches(matches, day, league)
            results.append((slug, league, rows))
    # Never call R2 until every requested league was successfully fetched and validated.
    if not live:
        return {"status": "DRY_RUN", "day": day.isoformat(), "timezone": "Europe/Istanbul",
                "leagues": [{"slug": slug, "league": league, "matches": len(rows)}
                            for slug, league, rows in results],
                "postgresql": "NOT_USED", "r2": "NOT_ACCESSED"}
    store = store_factory()
    try:
        receipts = []
        for slug, league, rows in results:
            if not rows:
                receipts.append({"league": league, "status": "NO_MATCHES_REPORTED", "matches": 0})
                continue
            payload = canonical({
                "league": league, "date": day.isoformat(), "timezone": "Europe/Istanbul",
                "provider": "openligadb", "licence": "ODbL-1.0",
                "source": "https://www.openligadb.de",
                "matches": rows, "odds_available": False,
            })
            digest = hashlib.sha256(payload).hexdigest()
            key = f"daily/openligadb/season=2026-2027/league={league}/date={day.isoformat()}/{digest}.json"
            verified = archive_verified(store, key, payload, "application/json")
            receipts.append({"league": league, "status": "R2_VERIFIED", "matches": len(rows), **verified})
        return {"status": "R2_DAILY_ARCHIVE_VERIFIED", "date": day.isoformat(),
                "leagues": receipts, "postgresql": "NOT_USED"}
    finally:
        close = getattr(getattr(store, "client", None), "close", None)
        if callable(close):
            close()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--date", default=datetime.now(TZ).date().isoformat())
    parser.add_argument("--league", action="append", choices=tuple(SOURCES))
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--approval", default="")
    args = parser.parse_args(argv)
    try:
        day = date.fromisoformat(args.date)
        report = run(day=day, live=args.live, approval=args.approval,
                     leagues=tuple(args.league or ("bundesliga",)))
    except Exception:
        print(json.dumps({"status": "BLOCKED", "reason": "DAILY_ARCHIVE_PRECHECK_FAILED"}))
        return 1
    print(json.dumps(report, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
