"""One league, season and UTC day; read-only source coverage probe, no SQL/R2 writes."""
import argparse
import json
from datetime import date

from app.core.config import get_settings
from app.providers.base import ProviderFailure
from app.providers.goaloo import GoalooProvider
from app.providers.registry import ProviderCaller


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--league", required=True)
    parser.add_argument("--season", required=True)
    parser.add_argument("--date", required=True, type=date.fromisoformat)
    parser.add_argument("--skip-odds", action="store_true")
    args = parser.parse_args(argv)
    cfg = get_settings()
    provider = GoalooProvider(base_url=cfg.goaloo_base_url, odds_base_url=cfg.goaloo_odds_base_url,
                              league_ids=(args.league,), season_ids=(args.season,),
                              timeout_seconds=cfg.provider_timeout_seconds)
    caller = ProviderCaller(provider, requests_per_minute=min(30, cfg.provider_requests_per_minute), max_retries=1)
    report = {"league": args.league, "season": args.season, "date": str(args.date),
              "provider": "goaloo", "writes": False, "matches": [],
              "statistics": "NOT_REQUESTED", "closing": "UNVERIFIED",
              "source_timestamp": "NOT_PROVIDED"}
    try:
        seasons = caller.call("get_seasons", args.league)
        report["available_target_seasons"] = [row["id"] for row in seasons
                                               if row["id"] in {"2023-2024", "2024-2025", "2025-2026"}]
        if args.season not in {row["id"] for row in seasons}:
            raise ValueError("SEASON_UNAVAILABLE")
        matches = caller.call("get_matches", args.date)
        results = caller.call("get_results", args.date)
        report["results"] = len(results)
        for match in matches:
            row = {"match_id": match["provider_match_id"], "status": match["status"], "odds": "NOT_REQUESTED"}
            report["matches"].append(row)
            if match["status"] == "FINISHED":
                try:
                    stats = caller.call("get_statistics", match["provider_match_id"])
                    row.update(statistics="AVAILABLE" if stats else "SOURCE_EMPTY", statistics_rows=len(stats),
                               statistics_fields=[s["name"] for s in stats])
                    report["statistics"] = row["statistics"]
                except ProviderFailure as exc:
                    row.update(statistics="FAILED", statistics_error=exc.code)
            if not args.skip_odds and match["status"] == "FINISHED" and args.date >= date(2025, 7, 1):
                try:
                    quotes = caller.call("get_odds", match["provider_match_id"])
                    row.update(odds="AVAILABLE" if quotes else "SOURCE_EMPTY", rows=len(quotes),
                               snapshots=sorted({q["snapshot_type"] for q in quotes}),
                               opening_rows=sum(q["snapshot_type"] == "OPENING" for q in quotes),
                               source_url=f"{provider.odds_base_url}/football/match/oddscomp-{match['provider_match_id']}")
                except ProviderFailure as exc:
                    row.update(odds="FAILED", error_code=exc.code)
        report["status"] = "FAILED" if any(m["odds"] == "FAILED" or m.get("statistics") == "FAILED" for m in report["matches"]) else "SOURCE_VERIFIED" if matches else "SOURCE_EMPTY"
    except Exception as exc:
        report.update(status="FAILED", error_code=getattr(exc, "code", type(exc).__name__.upper()))
    finally:
        provider.close()
    print(json.dumps(report, ensure_ascii=False, default=str))
    return 1 if report["status"] == "FAILED" else 0


if __name__ == "__main__":
    raise SystemExit(main())
