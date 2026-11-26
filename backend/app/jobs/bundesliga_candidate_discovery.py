"""Bounded read-only Bundesliga final-score discovery across up to three UTC days.

Never verifies SQL results, writes R2 evidence, archives matches or deletes data.
Only existing FINISHED SQL fixtures with FINAL scores and exact agreement between
two official APIs become CANDIDATE_FOR_EXPLICIT_VERIFICATION.

Discovery report counters and reason codes track why zero candidates may appear.
"""
from __future__ import annotations

import argparse
from datetime import date, datetime, timedelta, timezone
import json

from sqlalchemy import select

from app.analysis.football_data_corroboration import corroborate
from app.jobs.bundesliga_verified_runner import collect
from app.models import ActiveMatch, MatchResult
from app.storage.lifecycle import aware
from app.providers.result_sources import utc


class DiscoveryBlocked(ValueError):
    pass


def parse_days(value: str, *, today: date | None = None) -> list[date]:
    pieces = [p.strip() for p in value.split(",")]
    if not 1 <= len(pieces) <= 3 or any(not p for p in pieces):
        raise DiscoveryBlocked("ONE_TO_THREE_DATES_REQUIRED")
    try:
        days = [date.fromisoformat(p) for p in pieces]
    except ValueError:
        raise DiscoveryBlocked("INVALID_DATE_FORMAT") from None
    today = today or datetime.now(timezone.utc).date()
    if len(set(days)) != len(days) or any(
        d.year not in (2026, 2027) or
        (d.year if d.month >= 7 else d.year - 1) != 2026 or
        not today - timedelta(days=120) <= d <= today for d in days
    ):
        raise DiscoveryBlocked("INVALID_OR_DUPLICATE_SEASON_DATE")
    return days


def run(session, *, days: list[date], matchday: int, collector=collect, now=None):
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None or not 1 <= matchday <= 34:
        raise DiscoveryBlocked("INVALID_DISCOVERY_SCOPE")
    if days != parse_days(",".join(d.isoformat() for d in days), today=now.date()):
        raise DiscoveryBlocked("INVALID_DISCOVERY_DAYS")
    report = {"operation": "BUNDESLIGA_READ_ONLY_CANDIDATE_DISCOVERY",
              "days": [d.isoformat() for d in days], "matchday": matchday,
              "status": "SUCCESS", "candidates": [], "held": [],
              "api_requests_max": len(days) * 2, "sql_writes": 0,
              "r2_writes": 0, "sql_delete": False,
              "openligadb_fetched": 0, "footballdata_fetched": 0,
              "matched_both_sources": 0, "in_postgresql": 0,
              "not_in_postgresql": 0, "team_name_mismatches": 0,
              "kickoff_mismatches": 0, "score_mismatches": 0,
              "identity_conflicts": 0,
              "reason_code": None}
    for day in days:
        try:
            primary, secondary = collector(day, matchday, now=now)
            decision = corroborate(primary, secondary)
        except Exception:
            report["held"].append({"date": day.isoformat(),
                                   "reason": "SOURCE_FETCH_OR_PARSE_FAILED"})
            report["openligadb_fetched"] += len(primary) if primary else 0
            report["footballdata_fetched"] += len(secondary) if secondary else 0
            continue
        report["openligadb_fetched"] += len(primary) if primary else 0
        report["footballdata_fetched"] += len(secondary) if secondary else 0
        if decision["conflicts"] or decision["unmatched"]:
            report["held"].append({"date": day.isoformat(), "reason": "SOURCE_DISAGREEMENT",
                                   "conflicts": len(decision["conflicts"]),
                                   "unmatched": len(decision["unmatched"])})
            report["reason_code"] = "SOURCE_DISAGREEMENT"
            continue
        for candidate in decision["matches"]:
            # Both sources must agree exactly, and both must have been fetched
            # fresh for this observation window. corroborate enforces exact
            # source kickoff, team names, and score.
            first = [r for r in primary if str(r["source_match_id"]) ==
                     candidate["openligadb_match_id"]]
            peer = [r for r in secondary if str(r["source_match_id"]) ==
                    candidate["football_data_match_id"]]
            if len(first) != 1 or len(peer) != 1 or any(
                (utc(row.get("observed_at")) is None or
                 not timedelta(0) <= now - utc(row["observed_at"]) <= timedelta(minutes=15))
                for row in (first[0], peer[0])
            ):
                report["held"].append({"date": day.isoformat(), "reason": "STALE_OR_AMBIGUOUS_EVIDENCE"})
                report["identity_conflicts"] += 1
                continue
            fixtures = session.scalars(select(ActiveMatch).where(
                ActiveMatch.provider == "openligadb",
                ActiveMatch.provider_match_id == candidate["openligadb_match_id"],
                ActiveMatch.league_id == "de.bl1",
                ActiveMatch.season == "2026-2027",
            ).limit(2)).all()
            if len(fixtures) != 1:
                report["held"].append({"date": day.isoformat(),
                    "source_match_id": candidate["openligadb_match_id"],
                    "reason": "SQL_MATCH_NOT_UNIQUE_OR_MISSING"})
                report["not_in_postgresql"] += 1
                continue
            match = fixtures[0]
            results = session.scalars(select(MatchResult).where(
                MatchResult.match_id == match.id).limit(2)).all()
            if (match.status != "FINISHED" or
                    aware(match.start_time) != utc(candidate["start_time"]) or
                    match.home_team != first[0]["home_team"] or
                    match.away_team != first[0]["away_team"] or
                    len(results) != 1 or results[0].provider != "openligadb" or
                    results[0].status != "FINAL" or
                    [results[0].home_score, results[0].away_score] != candidate["score"]):
                report["held"].append({"date": day.isoformat(),
                    "source_match_id": candidate["openligadb_match_id"],
                    "reason": "SQL_FINAL_OR_IDENTITY_CONFLICT"})
                if match.home_team != first[0]["home_team"] or match.away_team != first[0]["away_team"]:
                    report["team_name_mismatches"] += 1
                if aware(match.start_time) != utc(candidate["start_time"]):
                    report["kickoff_mismatches"] += 1
                if [results[0].home_score, results[0].away_score] != candidate["score"]:
                    report["score_mismatches"] += 1
                report["identity_conflicts"] += 1
                report["reason_code"] = "SQL_FINAL_OR_IDENTITY_CONFLICT"
                continue
            report["candidates"].append({
                "date": day.isoformat(), "match_id": str(match.id),
                "openligadb_match_id": candidate["openligadb_match_id"],
                "football_data_match_id": candidate["football_data_match_id"],
                "home_team": match.home_team, "away_team": match.away_team,
                "score": candidate["score"],
                "status": ("ALREADY_VERIFIED" if results[0].verified_at is not None
                           else "CANDIDATE_FOR_EXPLICIT_VERIFICATION")})
            report["in_postgresql"] += 1
            report["matched_both_sources"] += 1
    if report["held"]:
        report["status"] = "PARTIAL"
    report["candidate_count"] = len(report["candidates"])
    report["held_count"] = len(report["held"])
    if report["candidate_count"] == 0:
        if report["held"]:
            report["reason_code"] = report["held"][0].get("reason", "NO_CANDIDATES")
        elif report["openligadb_fetched"] == 0 and report["footballdata_fetched"] == 0:
            report["reason_code"] = "NO_MATCHES_FETCHED"
        elif report["matched_both_sources"] == 0:
            report["reason_code"] = "SOURCE_DISAGREEMENT"
        elif report["in_postgresql"] == 0:
            report["reason_code"] = "NO_MATCHES_IN_SQL"
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--days", required=True, help="1–3 comma-separated UTC dates")
    parser.add_argument("--matchday", required=True, type=int)
    args = parser.parse_args(argv)
    from app.core.config import get_settings
    cfg = get_settings()
    if cfg.operational_delete_enabled or cfg.auto_retention_delete_enabled:
        print(json.dumps({"status": "BLOCKED", "reason": "DELETE_GATES_MUST_BE_DISABLED"}))
        return 2
    try:
        days = parse_days(args.days)
        from app.db.session import SessionLocal
        if SessionLocal is None or not cfg.database_url_valid:
            raise DiscoveryBlocked("DATABASE_NOT_CONFIGURED")
        with SessionLocal() as session:
            report = run(session, days=days, matchday=args.matchday)
            session.rollback()
    except DiscoveryBlocked as exc:
        print(json.dumps({"status": "BLOCKED", "reason": str(exc)}))
        return 2
    except Exception:
        print(json.dumps({"status": "FAILED", "reason": "DISCOVERY_EXECUTION_FAILED"}))
        return 1
    print(json.dumps(report, ensure_ascii=False, default=str))
    return 0 if report["status"] == "SUCCESS" else 2


if __name__ == "__main__":
    raise SystemExit(main())
