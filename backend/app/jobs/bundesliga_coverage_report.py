"""Read-only Bundesliga source-to-SQL coverage report.

Source observations are never persisted. An unavailable SQL session or R2
verification is explicitly UNKNOWN, never counted as complete coverage.
"""
from __future__ import annotations

import argparse
from datetime import date, datetime, timedelta, time, timezone
import json
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.analysis.football_data_corroboration import corroborate
from app.jobs.bundesliga_verified_runner import collect
from app.models import ActiveMatch, MatchResult
from app.providers.result_sources import utc
from app.storage.lifecycle import aware


class CoverageReportException(ValueError):
    pass


def _validate_days(days: list[date], today: date) -> None:
    if not 1 <= len(days) <= 3 or len(days) != len(set(days)):
        raise CoverageReportException("ONE_TO_THREE_DISTINCT_DAYS_REQUIRED")
    if any(d < today - timedelta(days=120) or d > today or
           (d.year if d.month >= 7 else d.year - 1) != 2026 for d in days):
        raise CoverageReportException("INVALID_SEASON_OR_DATE")


def generate_coverage_report(
    *, days: list[date] | None = None, matchday: int = 1,
    today: date | None = None, session: Session | None = None,
    collector=collect, archive_checker=None,
) -> dict[str, Any]:
    """Read only, bounded to 3 days and 2 source calls per day.

    archive_checker is an optional trusted, read-only verification callback
    taking (match, result) and returning True only for independently verified
    R2 readback/receipt. None means NOT_CHECKED, never a negative assertion.
    """
    today = today or datetime.now(timezone.utc).date()
    days = days if days is not None else [today - timedelta(days=i) for i in (2, 1, 0)]
    _validate_days(days, today)
    if type(matchday) is not int or not 1 <= matchday <= 34:
        raise CoverageReportException("INVALID_MATCHDAY")

    from app.core.config import get_settings
    settings = get_settings()
    if settings.operational_delete_enabled or settings.auto_retention_delete_enabled:
        raise CoverageReportException("DELETE_GATES_MUST_BE_DISABLED")

    report: dict[str, Any] = {
        "operation": "BUNDESLIGA_READ_ONLY_COVERAGE_REPORT",
        "days": [d.isoformat() for d in days], "matchday": matchday,
        "fetched": {"openligadb": 0, "football_data_org": 0},
        "matched_both_sources": 0, "postgresql": {"total_sql_matches": 0, "days": {}},
        "missing_from_sql": 0, "candidates_for_analysis": [],
        "held": [], "r2_archive": {}, "verified_r2_archives": 0,
        "sql_writes": 0, "r2_writes": 0, "sql_delete": False,
        "verified_at_modified": False, "api_requests_max": 2 * len(days),
    }
    for day in days:
        day_str = day.isoformat()
        report["postgresql"]["days"][day_str] = None if session is None else 0
        report["r2_archive"][day_str] = {"r2_status": "NOT_CHECKED"}
        try:
            primary, secondary = collector(day, matchday, now=datetime.now(timezone.utc))
            if not isinstance(primary, list) or not isinstance(secondary, list):
                raise ValueError("INVALID_SOURCE_PAYLOAD")
            report["fetched"]["openligadb"] += len(primary)
            report["fetched"]["football_data_org"] += len(secondary)
            decision = corroborate(primary, secondary)
        except Exception:
            report["held"].append({"date": day_str, "reason": "SOURCE_FETCH_OR_PARSE_FAILED"})
            continue

        if decision["conflicts"] or decision["unmatched"]:
            report["held"].append({"date": day_str, "reason": "SOURCE_DISAGREEMENT",
                                   "conflicts": len(decision["conflicts"]),
                                   "unmatched": len(decision["unmatched"])})
        report["matched_both_sources"] += len(decision["matches"])
        if session is None:
            if decision["matches"]:
                report["held"].append({"date": day_str, "reason": "SQL_SESSION_REQUIRED"})
            continue

        start = datetime.combine(day, time.min, tzinfo=timezone.utc)
        end = start + timedelta(days=1)
        fixtures = session.scalars(select(ActiveMatch).where(
            ActiveMatch.league_id == "de.bl1",
            ActiveMatch.season == "2026-2027",
            ActiveMatch.start_time >= start, ActiveMatch.start_time < end,
        )).all()
        report["postgresql"]["days"][day_str] = len(fixtures)
        report["postgresql"]["total_sql_matches"] += len(fixtures)

        by_id: dict[str, list] = {}
        for m in fixtures:
            if m.provider == "openligadb":
                by_id.setdefault(str(m.provider_match_id), []).append(m)
        for candidate in decision["matches"]:
            source_id = candidate["openligadb_match_id"]
            matching = by_id.get(source_id, [])
            if len(matching) != 1:
                reason = "SQL_MATCH_NOT_FOUND" if not matching else "SQL_MULTIPLE_MATCHES"
                report["held"].append({"date": day_str, "source_match_id": source_id,
                                       "reason": reason})
                if not matching:
                    report["missing_from_sql"] += 1
                continue
            match = matching[0]
            results = session.scalars(select(MatchResult).where(
                MatchResult.match_id == match.id).limit(2)).all()
            observations = [x for x in primary if x.get("provider") == "openligadb"
                            and str(x.get("source_match_id")) == source_id]
            if len(observations) != 1 or len(results) != 1:
                reason = "SQL_MULTIPLE_RESULTS" if len(results) > 1 else (
                    "SQL_MATCH_RESULT_MISSING" if not results else "AMBIGUOUS_SOURCE_ID")
                report["held"].append({"date": day_str, "source_match_id": source_id,
                                       "reason": reason})
                continue
            result = results[0]
            source = observations[0]
            if (match.status != "FINISHED" or result.status != "FINAL" or
                result.provider != "openligadb" or
                aware(match.start_time) != utc(candidate["start_time"]) or
                match.home_team != source["home_team"] or
                match.away_team != source["away_team"] or
                [result.home_score, result.away_score] != candidate["score"]):
                report["held"].append({"date": day_str, "source_match_id": source_id,
                                       "reason": "SQL_FINAL_OR_IDENTITY_CONFLICT"})
                continue
            # SQL verified_at is not by itself proof of a valid R2 archive.
            archive_status = "NOT_CHECKED"
            if archive_checker is not None:
                try:
                    archive_status = "VERIFIED" if archive_checker(match, result) is True else "UNVERIFIED"
                except Exception:
                    archive_status = "CHECK_FAILED"
            if archive_status == "VERIFIED":
                report["verified_r2_archives"] += 1
            report["r2_archive"][day_str]["r2_status"] = archive_status
            report["candidates_for_analysis"].append({
                "date": day_str, "match_id": str(match.id),
                "openligadb_match_id": source_id,
                "football_data_match_id": candidate["football_data_match_id"],
                "home_team": match.home_team, "away_team": match.away_team,
                "home_score": result.home_score, "away_score": result.away_score,
                "independently_corrob_observed": True,
                "sql_verified": result.verified_at is not None,
                "r2_status": archive_status,
            })

    candidates = report["candidates_for_analysis"]
    if session is None:
        reason = "SQL_SESSION_REQUIRED"
        readiness = "UNKNOWN"
    elif report["held"]:
        reason = report["held"][0]["reason"]
        readiness = "PARTIAL" if candidates else "NOT_READY"
    elif not candidates:
        reason = "NO_MATCHES_FETCHED" if not sum(report["fetched"].values()) else "NO_MATCHES_CORROBORATED"
        readiness = "NOT_READY"
    elif all(x["sql_verified"] and x["r2_status"] == "VERIFIED" for x in candidates):
        reason = "FULL_COVERAGE"
        readiness = "READY"
    else:
        reason = "R2_OR_SQL_VERIFICATION_INCOMPLETE"
        readiness = "PARTIAL"
    report["reason_code"] = reason
    report["readiness"] = readiness
    report["summary"] = (
        f"Bundesliga coverage: {report['matched_both_sources']} cross-source matches; "
        f"{report['postgresql']['total_sql_matches']} SQL fixtures; "
        f"{report['missing_from_sql']} missing SQL fixtures; "
        f"{len(candidates)} candidate rows; "
        f"{report['verified_r2_archives']} independently checked R2 archives; "
        f"readiness={readiness} ({reason})"
    )
    return report


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--days", default=None)
    parser.add_argument("--matchday", type=int, default=1)
    parser.add_argument("--read-sql", action="store_true",
                        help="Explicit opt-in to read existing SQL using a read-only transaction")
    args = parser.parse_args(argv)
    try:
        days = [date.fromisoformat(p.strip()) for p in args.days.split(",")] if args.days else None
        if args.read_sql:
            from app.db.session import SessionLocal
            if SessionLocal is None:
                raise CoverageReportException("DATABASE_NOT_CONFIGURED")
            from sqlalchemy import text
            with SessionLocal() as session:
                # PostgreSQL blocks every data-changing statement in this transaction.
                if session.get_bind().dialect.name != "postgresql":
                    raise CoverageReportException("POSTGRESQL_REQUIRED_FOR_SQL_READ")
                session.execute(text("SET TRANSACTION READ ONLY"))
                try:
                    report = generate_coverage_report(
                        days=days, matchday=args.matchday, session=session)
                finally:
                    session.rollback()
        else:
            # No implicit production credentials or SQL session: explicitly UNKNOWN.
            report = generate_coverage_report(days=days, matchday=args.matchday)
    except (CoverageReportException, ValueError):
        print(json.dumps({"status": "BLOCKED", "reason": "INVALID_DISCOVERY_SCOPE"}))
        return 2
    print(json.dumps(report, ensure_ascii=False, default=str))
    return 0 if report["readiness"] in ("READY", "PARTIAL", "UNKNOWN") else 2


if __name__ == "__main__":
    raise SystemExit(main())
