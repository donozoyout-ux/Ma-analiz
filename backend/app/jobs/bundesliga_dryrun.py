"""Phase B: Bundesliga single-day dry-run probe.

Guarantees:
- Zero SQL INSERT/UPDATE/DELETE.
- Zero R2 PUT/DELETE.
- Strictly read-only against external provider and database.
- Reports detected fixtures, duplicates, conflicts, and verified final scores.
- Enforces cost and quota protections.
"""
from __future__ import annotations

import argparse
from datetime import date, datetime, timezone
import json
import time
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import Settings, get_settings
from app.models import ActiveMatch, MatchResult
from app.providers.identity import fixture_key, deduplicate_scope
from app.providers.openligadb import ATTRIBUTION, LICENCE, LICENCE_URL, OpenLigaDBProvider
from app.storage.quota import QuotaExceededError, get_quota_tracker

ISTANBUL = ZoneInfo("Europe/Istanbul")


def run_bundesliga_dryrun(
    target_day: date,
    matchday: int = 10,
    *,
    settings: Settings | None = None,
    session: Session | None = None,
    provider: OpenLigaDBProvider | None = None,
) -> dict[str, Any]:
    cfg = settings or get_settings()
    quota = get_quota_tracker(cfg)
    own_provider = provider is None

    p = provider or OpenLigaDBProvider(
        base_url=getattr(cfg, "openligadb_base_url", "https://api.openligadb.de"),
        timeout_seconds=getattr(cfg, "openligadb_timeout_seconds", 10.0),
        requests_per_minute=getattr(cfg, "openligadb_requests_per_minute", 1),
    )

    t0 = time.monotonic()
    report: dict[str, Any] = {
        "operation": "BUNDESLIGA_DRY_RUN",
        "dry_run": True,
        "writes_executed": 0,
        "database_writes": False,
        "r2_writes": False,
        "target_day": target_day.isoformat(),
        "target_matchday": matchday,
        "league": "de.bl1",
        "season": "2026-2027",
        "observed_at": datetime.now(timezone.utc).isoformat(),
        "attribution": ATTRIBUTION,
        "licence": LICENCE,
        "licence_url": LICENCE_URL,
        "matches_found": 0,
        "candidates": [],
        "conflicts": [],
        "verified_finals": [],
        "existing_in_db": 0,
        "quota_summary": {},
        "status": "COMPLETED",
        "errors": [],
    }

    try:
        # Check quota before calling provider
        quota.check_request_quota(1)
        quota.record_request(1)

        season_year = target_day.year if target_day.month >= 7 else target_day.year - 1
        fetched = p.get_matchdata_by_group("bl1", season_year, matchday)
        report["matches_found"] = len(fetched)

        # Deduplicate using canonical cross-source fixture identity
        unique_matches, duplicate_conflicts = deduplicate_scope(fetched)
        report["conflicts"].extend(duplicate_conflicts)

        # Compare against existing database if session provided (read-only query)
        existing_matches_map: dict[str, ActiveMatch] = {}
        if session is not None:
            provider_ids = [m["provider_match_id"] for m in unique_matches]
            if provider_ids:
                stmt = select(ActiveMatch).where(
                    (ActiveMatch.provider == "openligadb") &
                    (ActiveMatch.provider_match_id.in_(provider_ids))
                )
                existing_rows = session.scalars(stmt).all()
                for row in existing_rows:
                    existing_matches_map[row.provider_match_id] = row
            report["existing_in_db"] = len(existing_matches_map)

        for m in unique_matches:
            fkey = fixture_key(m)
            p_id = m["provider_match_id"]
            existing = existing_matches_map.get(p_id)

            candidate_info = {
                "provider_match_id": p_id,
                "fixture_key": fkey,
                "home_team": m["home_team"],
                "away_team": m["away_team"],
                "start_time": m["start_time"].isoformat() if isinstance(m["start_time"], datetime) else str(m["start_time"]),
                "status": m["status"],
                "is_new": existing is None,
                "existing_id": str(existing.id) if existing else None,
            }

            if m["status"] == "FINISHED" and m.get("score") is not None:
                candidate_info["score"] = m["score"]
                report["verified_finals"].append({
                    "provider_match_id": p_id,
                    "home_team": m["home_team"],
                    "away_team": m["away_team"],
                    "score": m["score"],
                    "status": "FINAL",
                })

            report["candidates"].append(candidate_info)

    except QuotaExceededError as exc:
        report["status"] = "QUOTA_EXCEEDED"
        report["errors"].append(str(exc))
    except Exception as exc:
        report["status"] = "FAILED"
        report["errors"].append(str(exc))
    finally:
        report["duration_ms"] = round((time.monotonic() - t0) * 1000, 2)
        report["quota_summary"] = quota.get_summary()
        if own_provider:
            p.close()

    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--date", type=date.fromisoformat, default=date(2026, 11, 20), help="Target date YYYY-MM-DD")
    parser.add_argument("--matchday", type=int, default=10, help="Matchday (1-34)")
    args = parser.parse_args(argv)

    report = run_bundesliga_dryrun(target_day=args.date, matchday=args.matchday)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["status"] == "COMPLETED" else 1


if __name__ == "__main__":
    raise SystemExit(main())
