"""One-day, bounded Goaloo/OpenLigaDB source runner for the safe odds importer.

Runs in read-only mode by default. The optional write path requires explicit
--live, BUNDESLIGA_INGEST_ENABLED=true, BUNDESLIGA_INGEST_DRY_RUN=false,
an existing SQL match and an R2 object store. Does not create matches or a
scheduler. No access-control bypass or unreviewed cross-source joins.
"""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import date, datetime, timezone
import json
from zoneinfo import ZoneInfo

from app.core.config import get_settings
from app.jobs.goaloo_verified_import import import_verified_odds, ImportSafetyError
from app.providers.goaloo_match_identity import MappingDecision, _team
from app.providers.goaloo import GoalooProvider
from app.providers.openligadb import OpenLigaDBProvider


def map_observed_rows(goaloo_rows: list[dict], openliga_rows: list[dict]) -> list[MappingDecision]:
    """Conservative 1:1 mapping using normalized provider observations."""
    potential = {}
    for o in openliga_rows:
        if o.get("provider") != "openligadb" or o.get("league_id") != "de.bl1":
            continue
        if o.get("season") not in ("2026-2027", None):
            continue
        home, away = _team(o.get("home_team", "")), _team(o.get("away_team", ""))
        start = o.get("start_time")
        if not home or not away or not isinstance(start, datetime) or start.tzinfo is None:
            continue
        potential.setdefault((home, away), []).append((str(o["provider_match_id"]), start.astimezone(timezone.utc)))
    proposed = []
    for g in goaloo_rows:
        gid = str(g.get("provider_match_id", ""))
        if g.get("provider") != "goaloo" or str(g.get("league_id")) not in ("8", "de.bl1"):
            continue
        if g.get("season") not in ("2026-2027", None):
            continue
        home, away = _team(g.get("home_team", "")), _team(g.get("away_team", ""))
        at = g.get("start_time")
        if not gid or not home or not away or not isinstance(at, datetime) or at.tzinfo is None:
            proposed.append((gid, [], "UNREVIEWED_TEAM_OR_KICKOFF"))
            continue
        matches = [(mid, abs(int((at.astimezone(timezone.utc) - start).total_seconds())))
                   for mid, start in potential.get((home, away), ())
                   if abs((at.astimezone(timezone.utc) - start).total_seconds()) <= 1800]
        proposed.append((gid, matches, "EXACT_TEAMS_AND_TIME" if len(matches)==1 else "NO_UNIQUE_MATCH"))
    target_counts = Counter(m[0][0] for _, m, _ in proposed if len(m) == 1)
    source_counts = Counter(gid for gid, _, _ in proposed)
    result = []
    for gid, matches, reason in proposed:
        if len(matches) == 1 and target_counts[matches[0][0]] == 1 and source_counts[gid] == 1:
            result.append(MappingDecision(gid, matches[0][0], "MATCHED",
                "EXACT_TEAMS_AND_TIME", {"league_id": "de.bl1", "season": "2026-2027",
                                          "kickoff_delta_seconds": matches[0][1]}))
        else:
            result.append(MappingDecision(gid, None, "NEEDS_REVIEW" if matches or source_counts[gid] > 1 else "NO_MATCH",
                                          "AMBIGUOUS_OR_UNKNOWN_IDENTITY",
                                          {"candidate_count": len(matches), "league_id": "de.bl1"}))
    return result


def collect_one_day(target_day: date, *, openliga, goaloo, session, store=None,
                    dry_run: bool = True, live_approved: bool = False,
                    max_odds_requests: int = 2) -> dict:
    """Fetch source day, collect up to two verified odds pages, optionally persist."""
    if not 1 <= max_odds_requests <= 2:
        raise ValueError("Pilot allows 1-2 odds requests per invocation")
    open_rows = openliga.get_matches(target_day)
    goal_rows = goaloo.get_matches_on_day(target_day, "8", "2026-2027")
    decisions = map_observed_rows(goal_rows, open_rows)
    verified = sorted((d for d in decisions if d.status == "MATCHED"), key=lambda d:d.goaloo_id)
    quotes: dict[str, list[dict]] = {}
    for item in verified[:max_odds_requests]:
        quotes[item.goaloo_id] = goaloo.get_odds(item.goaloo_id)
    from app.providers.goaloo_match_identity import summary
    result = import_verified_odds(session, decisions, quotes, store=store,
                                  dry_run=dry_run, live_approved=live_approved)
    return {"day": target_day.isoformat(), "openligadb_matches": len(open_rows),
            "goaloo_matches": len(goal_rows), "mapping": summary(decisions),
            "odds_pages_checked": len(quotes), "import": result}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--date", type=date.fromisoformat,
        default=datetime.now(ZoneInfo("Europe/Istanbul")).date())
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--max-odds-requests", type=int, default=2)
    args = parser.parse_args(argv)
    cfg = get_settings()
    if args.live:
        if (not cfg.bundesliga_ingest_enabled or cfg.bundesliga_ingest_dry_run
                or cfg.auto_retention_delete_enabled or cfg.operational_delete_enabled):
            print(json.dumps({"status": "BLOCKED", "reason": "LIVE_GATE_DISABLED"}))
            return 2
        if not cfg.r2_configured or not cfg.database_url_valid:
            print(json.dumps({"status": "BLOCKED", "reason": "SQL_OR_R2_NOT_CONFIGURED"}))
            return 2
    from app.db.session import SessionLocal
    if SessionLocal is None:
        print(json.dumps({"status": "BLOCKED", "reason": "DATABASE_NOT_CONFIGURED"}))
        return 2
    from app.storage.r2 import R2ObjectStore
    from app.providers.base import ProviderFailure
    try:
        with SessionLocal() as session:
            with OpenLigaDBProvider(base_url=cfg.openligadb_base_url,
                    timeout_seconds=cfg.openligadb_timeout_seconds,
                    requests_per_minute=cfg.openligadb_requests_per_minute) as openliga:
                goaloo = GoalooProvider(base_url=cfg.goaloo_base_url,
                    odds_base_url=cfg.goaloo_odds_base_url,
                    league_ids=("8",), season_ids=("2026-2027",), requests_per_minute=6)
                try:
                    result = collect_one_day(args.date, openliga=openliga, goaloo=goaloo,
                        session=session, store=R2ObjectStore(settings=cfg) if args.live else None,
                        dry_run=not args.live, live_approved=args.live,
                        max_odds_requests=args.max_odds_requests)
                finally:
                    goaloo.close()
        print(json.dumps(result, ensure_ascii=False, default=str))
        return 0
    except (ProviderFailure, ImportSafetyError, ValueError) as exc:
        print(json.dumps({"status": "BLOCKED", "reason": str(exc)}))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
