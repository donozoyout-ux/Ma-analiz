"""Phase A: OpenLigaDB read-only probe for Bundesliga (de.bl1).

Validates:
- HTTPS endpoint connectivity
- OpenLigaDB API schema and data availability for 2026-2027
- Legal attribution and ODbL-1.0 license compliance
- Latency and rate limit compliance
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import time
from typing import Any

from app.core.config import Settings, get_settings
from app.providers.openligadb import ATTRIBUTION, LICENCE, LICENCE_URL, OpenLigaDBProvider


def probe_openligadb(
    settings: Settings | None = None,
    provider: OpenLigaDBProvider | None = None,
    season_year: int = 2026,
    matchday: int = 10,
) -> dict[str, Any]:
    cfg = settings or get_settings()
    own_provider = provider is None
    p = provider or OpenLigaDBProvider(
        base_url=getattr(cfg, "openligadb_base_url", "https://api.openligadb.de"),
        timeout_seconds=getattr(cfg, "openligadb_timeout_seconds", 10.0),
        requests_per_minute=getattr(cfg, "openligadb_requests_per_minute", 1),
    )

    t0 = time.monotonic()
    report: dict[str, Any] = {
        "probe": "OPENLIGADB_PHASE_A_SOURCE_VALIDATION",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "league": "de.bl1",
        "season_year": season_year,
        "season": f"{season_year}-{season_year + 1}",
        "target_matchday": matchday,
        "status": "UNKNOWN",
        "attribution": ATTRIBUTION,
        "licence": LICENCE,
        "licence_url": LICENCE_URL,
        "endpoints_probed": [],
        "sample_fixtures": [],
        "errors": [],
    }

    try:
        # Step 1: Health / current group
        health = p.health_check()
        report["endpoints_probed"].append({
            "endpoint": "getcurrentgroup/bl1",
            "status": health.get("status"),
            "latency_ms": health.get("latency_ms"),
        })

        if health.get("status") not in ("PROVIDER_OK", "DEGRADED"):
            report["status"] = "OPENLIGADB_PROBE_FAILED"
            report["errors"].append(f"Health check failed: {health.get('error_code')}")
            return report

        # Step 2: Fetch matchday data
        t_match = time.monotonic()
        matches = p.get_matchdata_by_group("bl1", season_year, matchday)
        match_latency = (time.monotonic() - t_match) * 1000
        report["endpoints_probed"].append({
            "endpoint": f"getmatchdata/bl1/{season_year}/{matchday}",
            "status": "OK",
            "matches_count": len(matches),
            "latency_ms": round(match_latency, 2),
        })

        if not matches:
            report["status"] = "OPENLIGADB_PROBE_WARNING"
            report["errors"].append("No matches returned for specified season and matchday")
            return report

        # Validate schema of sample fixtures
        for m in matches[:3]:
            report["sample_fixtures"].append({
                "match_id": m.get("provider_match_id"),
                "home_team": m.get("home_team"),
                "away_team": m.get("away_team"),
                "start_time": m.get("start_time").isoformat() if isinstance(m.get("start_time"), datetime) else str(m.get("start_time")),
                "status": m.get("status"),
                "score": m.get("score"),
                "round": m.get("round"),
            })

        # Check required fields
        first = matches[0]
        required_fields = ["provider_match_id", "league_id", "home_team", "away_team", "start_time", "status", "attribution"]
        missing = [f for f in required_fields if f not in first or first[f] is None]
        if missing:
            report["status"] = "OPENLIGADB_PROBE_WARNING"
            report["errors"].append(f"Missing required fields: {missing}")
        else:
            report["status"] = "OPENLIGADB_PROBE_OK"

    except Exception as exc:
        report["status"] = "OPENLIGADB_PROBE_FAILED"
        report["errors"].append(str(exc))
    finally:
        report["total_probe_duration_ms"] = round((time.monotonic() - t0) * 1000, 2)
        if own_provider:
            p.close()

    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--season-year", type=int, default=2026, help="Bundesliga starting year (e.g. 2026 for 2026-2027)")
    parser.add_argument("--matchday", type=int, default=10, help="Matchday group (1-34)")
    args = parser.parse_args(argv)

    report = probe_openligadb(season_year=args.season_year, matchday=args.matchday)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["status"] == "OPENLIGADB_PROBE_OK" else 1


if __name__ == "__main__":
    raise SystemExit(main())
