"""Explicit Bundesliga daily runner; read-only unless --live AND config gate opt in.

Usage:
  python -m app.jobs.bundesliga_daily
  python -m app.jobs.bundesliga_daily --date 2026-11-24
  python -m app.jobs.bundesliga_daily --live   # requires BUNDESLIGA_INGEST_ENABLED=true

No automatic schedule is registered by this module.
"""
from __future__ import annotations

import argparse
from datetime import date, datetime
import json
from zoneinfo import ZoneInfo

from app.core.config import get_settings
from app.providers.openligadb import OpenLigaDBProvider


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--date", type=date.fromisoformat,
                        default=datetime.now(ZoneInfo("Europe/Istanbul")).date())
    parser.add_argument("--live", action="store_true",
                        help="Explicitly allow DB+R2 writes when the server config gate is also enabled")
    args = parser.parse_args(argv)
    cfg = get_settings()

    if args.live:
        if not cfg.bundesliga_ingest_enabled or cfg.bundesliga_ingest_dry_run:
            print(json.dumps({"status": "BLOCKED", "reason": "LIVE_OPERATOR_GATE_DISABLED"}))
            return 2
        if cfg.auto_retention_delete_enabled or cfg.operational_delete_enabled:
            print(json.dumps({"status": "BLOCKED", "reason": "DELETE_SAFETY_GATE"}))
            return 2

        from app.db.session import SessionLocal
        from app.jobs.bundesliga_pipeline import run_bundesliga_ingest_pipeline
        from app.storage.r2 import R2ObjectStore

        if SessionLocal is None:
            print(json.dumps({"status": "BLOCKED", "reason": "DATABASE_NOT_CONFIGURED"}))
            return 2
        if not cfg.r2_configured:
            print(json.dumps({"status": "BLOCKED", "reason": "R2_NOT_CONFIGURED"}))
            return 2
        with SessionLocal() as session:
            report = run_bundesliga_ingest_pipeline(
                args.date, matchday=None, dry_run=False, settings=cfg,
                session=session, store=R2ObjectStore(settings=cfg),
            )
    else:
        # Read-only daily probe: does not construct a SQL or R2 writer.
        with OpenLigaDBProvider(
            base_url=cfg.openligadb_base_url,
            timeout_seconds=cfg.openligadb_timeout_seconds,
            requests_per_minute=cfg.openligadb_requests_per_minute,
        ) as provider:
            matches = provider.get_matches(args.date)
        report = {
            "status": "DRY_RUN",
            "date": args.date.isoformat(),
            "league": "de.bl1",
            "season": "2026-2027",
            "matches_seen": len(matches),
            "finals_observed": sum(m["status"] == "FINISHED" and m.get("score") is not None for m in matches),
            "sql_writes": 0,
            "r2_writes": 0,
            "attribution": provider.attribution,
        }
    print(json.dumps(report, ensure_ascii=False, default=str))
    return 0 if report["status"] in {"DRY_RUN", "COMPLETED"} else 1


if __name__ == "__main__":
    raise SystemExit(main())
