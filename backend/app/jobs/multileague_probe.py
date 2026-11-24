"""Fixed Stage 7A pilot: one declared day per league, <=2 matches per resource, no persistence."""
import argparse
from datetime import datetime, timezone
import json
from urllib.parse import urlsplit

from app.providers.evidence_http import EvidenceFailure, EvidenceHTTP
from app.providers.multileague import PILOT, bounded, compare, openfootball, openligadb
from app.jobs.result_probe import POLICIES


def run(*, leagues=None, limit=2, http=None):
    selected = list(PILOT) if leagues is None else list(leagues)
    if not selected or len(set(selected)) != len(selected) or any(k not in PILOT for k in selected) or limit not in (1, 2):
        raise ValueError("INVALID_PILOT_SCOPE")
    own = http is None
    http = http or EvidenceHTTP()
    stopped_origins = set()
    report = {"stage": "7A", "strategy": "WEB_SCRAPING_FIRST", "evidence_kind": "LIVE_SOURCE_OBSERVATION" if own else "INJECTED_TEST_TRANSPORT",
              "observed_at": datetime.now(timezone.utc).isoformat(), "sql": "NOT_RUN", "r2": "NOT_RUN",
              "LIVE_DELETE": "NOT_RUN", "writes_allowed": False, "persistence_ready": False,
              "auto_retention_delete_enabled_by_this_command": False,
              "source_policies": POLICIES, "leagues": {}, "requests": http.requests,
              "resource_granularity": "One OpenFootball season file and explicit OpenLigaDB matchday per resource; analyse only the fixed day. No traversal or whole-season storage."}
    try:
        for league in selected:
            scope = PILOT[league]
            result = {"date": scope["date"], "name": scope["name"], "max_matches_per_resource": limit,
                      "sources": [], "observations": [], "identity_mapping": "REVIEW_REQUIRED_NO_MERGES"}
            report["leagues"][league] = result
            resources = [("openfootball", f"https://raw.githubusercontent.com/openfootball/football.json/master/2026-27/{scope['file']}.json", None)]
            resources += [("openligadb", f"https://api.openligadb.de/getmatchdata/{short}/2026/{round_}", (short, id_, round_))
                          for short, id_, round_ in scope["oldb"]]
            if not scope["oldb"]:
                result["sources"].append({"provider": "openligadb", "status": "NOT_AVAILABLE",
                                          "reason": "NO_REVIEWED_CURRENT_SEASON_LEAGUE_ID"})
            for provider, url, identity in resources:
                entry = {"provider": provider, "source_url": url}
                result["sources"].append(entry)
                origin = urlsplit(url).netloc
                if origin in stopped_origins:
                    entry.update(status="NOT_REQUESTED", error_code="ORIGIN_ACCESS_STOP")
                    continue
                try:
                    payload = http.json(url)
                    kwargs = {"observed_at": datetime.now(timezone.utc), "source_url": url}
                    rows = (openligadb(payload, league, *identity, **kwargs) if identity
                            else openfootball(payload, league, **kwargs))
                    rows, count = bounded(rows, limit)
                    result["observations"].extend(rows)
                    entry.update(status="OBSERVED" if count else "NO_MATCH_ON_SELECTED_DAY",
                                 selected_day_count=count, emitted_count=len(rows))
                except EvidenceFailure as exc:
                    # EvidenceHTTP emits fixed codes, never a transport exception string.
                    entry.update(status="UNAVAILABLE", error_code=str(exc))
                    if str(exc).startswith("ACCESS_STOP_") or str(exc) in {"ROBOTS_UNVERIFIED", "NETWORK_OR_TLS_FAILURE"}:
                        stopped_origins.add(origin)
                except Exception:
                    entry.update(status="REJECTED", error_code="SOURCE_SCHEMA_OR_IDENTITY_INVALID")
            result["comparisons"] = compare(result["observations"])
            result["production_final_results"] = []
        report["status"] = "READ_ONLY_PILOT_COMPLETE"
    finally:
        if own:
            http.close()
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--league", choices=list(PILOT), help="Omit to inspect all three fixed scopes")
    parser.add_argument("--max-matches", type=int, choices=(1, 2), default=2)
    args = parser.parse_args(argv)
    report = run(leagues=[args.league] if args.league else None, limit=args.max_matches)
    print(json.dumps(report, ensure_ascii=True, indent=2))
    # Partial coverage is an evidence outcome; exit 1 if every resource failed.
    return 0 if any(s.get("status") == "OBSERVED" for r in report["leagues"].values() for s in r["sources"]) else 1


if __name__ == "__main__":
    raise SystemExit(main())
