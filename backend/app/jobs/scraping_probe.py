"""One league + one Istanbul day, read-only web probe. No SQL/R2 or paid fallback."""
from __future__ import annotations

import argparse
import hashlib
import json
from datetime import date, datetime, timezone
from typing import Any

import httpx
from app.analysis.engine import form_analyzer, goals_analyzer
from app.providers.base import ProviderFailure, ProviderAccessRestricted
from app.providers.goaloo import GoalooProvider
from app.providers.identity import deduplicate_scope, fixture_key
from app.providers.normalize import normalize_matches, normalize_odds
from app.providers.source_policy import SOURCES


def season_for(day: date) -> str:
    year = day.year if day.month >= 7 else day.year - 1
    return f"{year}-{year+1}"


def usable_statistics(match: dict, stats: list[dict]) -> list[dict]:
    # Technical HTML may update while the season schedule still says scheduled.
    # Preserve observations in the report but do not use inconsistent/incomplete games as model history.
    if match.get("status") != "FINISHED":
        return []
    return [row for row in stats if row.get("home_value") is not None and row.get("away_value") is not None]


def probe(day: date, league: str, *, max_matches: int = 3, provider: Any = None) -> dict:
    if league not in {"8", "36"} or not 1 <= max_matches <= 3:
        raise ValueError("ONE_REVIEWED_LEAGUE_AND_AT_MOST_THREE_MATCH_DETAILS_REQUIRED")
    season = season_for(day)
    report = {"strategy": "WEB_SCRAPING_FIRST", "status": "FAILED", "writes": False,
              "sql": "NOT_USED", "r2": "NOT_USED", "paid_fallback": False,
              "scope": {"league": league, "season": season, "date": day.isoformat(), "timezone": "Europe/Istanbul"},
              "observed_at": datetime.now(timezone.utc).isoformat(), "source_policies": SOURCES,
              "requests": [], "matches": [], "match_count": None, "odds_count": 0, "result_count": 0,
              "statistics_count": 0, "usable_statistics_count": 0, "duplicates": [], "live_test": provider is None,
              "source_resource_scope": "One current/requested-season schedule file, filtered to one day; no season traversal/backfill"}
    client = None
    if provider is None:
        def audit_request(request):
            if len(report["requests"]) >= 12:
                raise ProviderAccessRestricted("PROVIDER_ACCESS_RESTRICTED: request budget exhausted")
        def audit_response(response):
            response.read()
            entry = {"url": str(response.request.url), "http_status": response.status_code,
                     "sha256": hashlib.sha256(response.content).hexdigest(),
                     "observed_at": datetime.now(timezone.utc).isoformat()}
            if response.request.url.path == "/robots.txt":
                entry["policy_text"] = response.text[:5000]
            report["requests"].append(entry)
        client = httpx.Client(timeout=15, follow_redirects=False,
                              event_hooks={"request": [audit_request], "response": [audit_response]})
        provider = GoalooProvider(league_ids=(league,), season_ids=(season,), requests_per_minute=6, client=client)
    try:
        raw = provider.get_matches_on_day(day, league, season)
        normalized, report["duplicates"] = deduplicate_scope(normalize_matches(raw, provider="goaloo"))
        report["match_count"] = len(normalized)
        outcomes = {r["provider_match_id"]: r for r in provider._results(raw)}
        report["result_count"] = len(outcomes)
        source_url = f"{provider.base_url}/jsData/matchResult/json/{season}/s{league}_en.json"
        for match in normalized[:max_matches]:
            identity = match["provider_match_id"]
            row = {**match, "source_url": source_url, "fixture_key": fixture_key(match),
                   "fields": {"fixture": "PASS", "score": "PASS" if identity in outcomes else "NOT_AVAILABLE"},
                   "result": outcomes.get(identity), "odds": [], "statistics": [],
                   "analysis_input": {"history_status": "NOT_FETCHED_IN_SINGLE_DAY_PROBE", "technical_statistics": []}}
            report["matches"].append(row)
            try:
                quotes = provider.get_odds(identity)
                row["odds"] = [normalize_odds(q, match_id=identity, provider="goaloo") for q in quotes]
                row["fields"]["odds"] = "PASS" if quotes else "SOURCE_EMPTY"
                report["odds_count"] += len(row["odds"])
            except ProviderFailure as exc:
                row["fields"]["odds"] = "BLOCKED" if isinstance(exc, ProviderAccessRestricted) else "FAILED"
                row["odds_error"] = exc.code
            try:
                stats = provider.get_statistics(identity)
                row["statistics"] = stats
                usable = usable_statistics(match, stats)
                row["analysis_input"]["technical_statistics"] = usable
                row["fields"]["statistics"] = "PASS" if usable else "OBSERVED_UNVERIFIED" if stats else "SOURCE_EMPTY"
                report["usable_statistics_count"] += len(usable)
                if stats and not usable:
                    row["statistics_warning"] = "MATCH_NOT_CONFIRMED_FINISHED_BY_SCHEDULE"
                report["statistics_count"] += len(stats)
            except ProviderFailure as exc:
                row["fields"]["statistics"] = "BLOCKED" if isinstance(exc, ProviderAccessRestricted) else "FAILED"
                row["statistics_error"] = exc.code
            # Reuse analysis contracts without inventing previous matches or triggering AI.
            for side in ("home", "away"):
                team = match[f"{side}_team"]
                row["analysis_input"][side] = {
                    "form": form_analyzer(team, [], as_of=match["start_time"]).to_dict(),
                    "venue_form": form_analyzer(team, [], as_of=match["start_time"], venue=side).to_dict(),
                    "goals": goals_analyzer(team, [], as_of=match["start_time"]),
                }
            row["opening_odds"] = "AVAILABLE" if any(q["snapshot_type"] == "OPENING" for q in row["odds"]) else "NOT_AVAILABLE"
            row["closing_odds"] = "AVAILABLE" if any(q["snapshot_type"] == "CLOSING" for q in row["odds"]) else "NOT_AVAILABLE"
        report["matches_tested"] = len(report["matches"])
        report["untested_match_details"] = max(0, len(normalized)-len(report["matches"]))
        report["status"] = "SOURCE_EMPTY" if not normalized else "PARTIAL" if any(
            row["fields"].get(field) != "PASS" for row in report["matches"] for field in ("odds", "statistics")) else "SOURCE_VERIFIED"
    except ProviderFailure as exc:
        report["error_code"] = exc.code
    except (ValueError, TypeError, KeyError):
        report["error_code"] = "SOURCE_SCHEMA_INVALID"
    except Exception:
        report["error_code"] = "SOURCE_PROBE_FAILED"
    finally:
        provider.close()
        if client is not None:
            client.close()
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--league", required=True, choices=("8", "36"), help="One Goaloo league ID: Bundesliga 8 or Premier League 36")
    parser.add_argument("--date", required=True, type=date.fromisoformat, help="One date in Europe/Istanbul")
    parser.add_argument("--max-matches", type=int, choices=(1,2,3), default=3)
    args = parser.parse_args(argv)
    report = probe(args.date, args.league, max_matches=args.max_matches)
    print(json.dumps(report, ensure_ascii=True, default=str, indent=2))
    return 0 if report["status"] == "SOURCE_VERIFIED" else 2 if report["status"] == "SOURCE_EMPTY" else 1


if __name__ == "__main__":
    raise SystemExit(main())
