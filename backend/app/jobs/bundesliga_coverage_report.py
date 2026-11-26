"""Read-only Bundesliga coverage and analysis readiness report.

Never writes to SQL or R2. Only queries existing data and fetches from
authorized APIs in dry-run mode. Reports coverage gaps and readiness
for subsequent controlled ingestion pipelines.

Usage:
    from app.jobs.bundesliga_coverage_report import generate_coverage_report
    report = generate_coverage_report(days=[date(2026, 8, 28)], matchday=1)
    print(report["summary"])
"""
from __future__ import annotations

import json
from datetime import date, datetime, timedelta, timezone
from typing import Any

from sqlalchemy.orm import Session, sessionmaker

from app.jobs.bundesliga_verified_runner import collect
from app.providers.football_data_org import fetch_bundesliga_finals
from app.models import ActiveMatch, MatchResult
from app.core.config import get_settings


DISCOVERY_BLOCKED = (
    get_settings().operational_delete_enabled
    or get_settings().auto_retention_delete_enabled
)


class CoverageReportException(Exception):
    pass


def _validate_days(days: list[date]) -> None:
    if not 1 <= len(days) <= 3:
        raise CoverageReportException("DAYS_MUST_BE_1_TO_3")
    today = datetime.now(timezone.utc).date()
    for d in days:
        if d < today - timedelta(days=120) or d > today:
            raise CoverageReportException(
                f"DAY_OUT_OF_RANGE: {d.isoformat()}"
            )


def _fetch_matches(days: list[date], matchday: int) -> dict[date, tuple[list[dict], list[dict], str]]:
    """Fetch from both APIs in dry-run mode. Returns day -> (primary, secondary, error)."""
    results: dict[date, tuple[list[dict], list[dict], str]] = {}
    for day in days:
        try:
            primary, secondary = collect(day, matchday, now=datetime.now(timezone.utc))
            if primary is None: primary = []
            if secondary is None: secondary = []
            results[day] = (primary, secondary, "")
        except Exception as exc:
            results[day] = ([], [], str(exc))
    return results


def _query_postgresql(session: Session, days: list[date]) -> dict[date, dict[str, Any]]:
    """Query existing ActiveMatch + MatchResult records from PostgreSQL/SQLite."""
    report: dict[date, dict[str, Any]] = {}
    for day in days:
        matches = session.scalars(
            select(ActiveMatch).where(
                ActiveMatch.league_id == "de.bl1",
                ActiveMatch.season == "2026-2027",
                ActiveMatch.start_time >= day,
                ActiveMatch.start_time < day + timedelta(days=1),
            )
        ).all()
        match_ids = [m.id for m in matches]
        results = {}
        if match_ids:
            match_results = session.scalars(
                select(MatchResult).where(MatchResult.match_id.in_(match_ids))
            ).all()
            for m in matches:
                rr = [r for r in match_results if r.match_id == m.id]
                results[m.provider_match_id] = {
                    "home_team": m.home_team,
                    "away_team": m.away_team,
                    "status": m.status,
                    "home_score": rr[0].home_score if rr else None,
                    "away_score": rr[0].away_score if rr else None,
                    "verified_at": rr[0].verified_at is not None if rr else False,
                    "provider": rr[0].provider if rr else m.provider,
                }
        else:
            results = {"<no_matches>": {"reason": "NO_SQL_MATCHES_FOUND"}}
        report[day] = {
            "sql_matches": len(matches),
            "sql_match_details": results,
        }
    return report


def _r2_archive_check(days: list[date]) -> dict[date, dict[str, Any]]:
    """Check R2 Parquet archive existence (read-only, in-memory check only)."""
    from app.storage.r2 import InMemoryObjectStore
    from app.core.config import get_settings
    settings = get_settings()
    if not settings.r2_configured:
        return {d.isoformat(): {"r2_status": "R2_NOT_CONFIGURED"} for d in days}

    store = InMemoryObjectStore(settings=settings)
    report: dict[date, dict[str, Any]] = {}
    for day in days:
        keys = list(store.list_keys(prefix=f"bundesliga/{day.isoformat()}"))
        report[day.isoformat()] = {
            "r2_status": "CONFIGURED" if keys else "R2_EMPTY",
            "archived_keys": keys[:10],
        }
    return report


def generate_coverage_report(
    *,
    days: list[date] | None = None,
    matchday: int | None = None,
    today: date | None = None,
    session: Session | None = None,
) -> dict[str, Any]:
    """Generate read-only Bundesliga coverage report.

    Keyword args:
        days: list of date objects (default: last 3 days from today)
        matchday: Bundesliga matchday (1-34, default: 1)
        today: reference date for default days (default: now)
        session: SQLAlchemy Session (optional; if not provided, SQL queries
                 are skipped and report notes session-unavailable)

    Returns dict with coverage metrics and readiness status.
    Never writes to SQL or R2.
    """
    if today is None:
        today = datetime.now(timezone.utc).date()
    if days is None:
        days = [today - timedelta(days=i) for i in range(2, -1, -1)]
    _validate_days(days)

    if DISCOVERY_BLOCKED:
        raise CoverageReportException("DELETE_GATES_MUST_BE_DISABLED")

    if matchday is None:
        matchday = 1
    if not 1 <= matchday <= 34:
        raise CoverageReportException("MATCHDAY_MUST_BE_1_TO_34")

    # Fetch from both APIs (always dry-run, no writes)
    fetched: dict[date, tuple[list[dict], list[dict], str]] = _fetch_matches(days, matchday)

    # Query PostgreSQL if session provided
    if session is not None:
        pg_status = _query_postgresql(session, days)
    else:
        pg_status = {
            day: {"sql_matches": 0, "sql_match_details": {"<no_session>": {"reason": "SQL_SESSION_REQUIRED"}}}
            for day in days
        }

    # Check R2 archive (read-only check)
    r2_status = _r2_archive_check(days)

    # Build report data
    total_fetched = 0
    total_sql = 0
    total_missing = 0
    candidates_for_analysis = []
    reasons: list[str] = []
    sql_session_missing = session is None

    for day in days:
        day_str = day.isoformat()
        primary, secondary, fetch_error = fetched.get(day, ([], [], "FETCH_ERROR"))
        sql_info = pg_status.get(day, {"sql_matches": 0, "sql_match_details": {}})
        r_info = r2_status.get(day_str, {"r2_status": "R2_NOT_CONFIGURED"})

        n_primary = len(primary)
        n_secondary = len(secondary)
        total_fetched += n_primary + n_secondary

        n_sql = sql_info.get("sql_matches", 0)
        total_sql += n_sql

        missing = max(0, n_primary + n_secondary - n_sql) if n_primary + n_secondary > 0 else 0
        total_missing += missing

        # Check if any candidates for analysis (finished, verified, scores match)
        for pm_id, pdata in sql_info.get("sql_match_details", {}).items():
            if pdata.get("status") == "FINISHED" and pdata.get("verified_at") and \
               pdata.get("home_score") is not None and pdata.get("away_score") is not None:
                candidates_for_analysis.append({
                    "date": day_str,
                    "match_id": pm_id,
                    "home_team": pdata["home_team"],
                    "away_team": pdata["away_team"],
                    "home_score": pdata["home_score"],
                    "away_score": pdata["away_score"],
                    "verified": pdata.get("verified_at") is not None,
                })

        # Reasons per day
        day_reasons: list[str] = []
        if n_primary == 0 and n_secondary == 0:
            day_reasons.append("NO_MATCHES_FETCHED")
            reasons.append("NO_MATCHES_FETCHED")
        if n_sql == 0 and n_primary + n_secondary > 0:
            day_reasons.append("NO_SQL_MATCHES")
            reasons.append("NO_SQL_MATCHES")
        if r_info.get("r2_status") == "R2_NOT_CONFIGURED":
            day_reasons.append("R2_NOT_CONFIGURED")
            reasons.append("R2_NOT_CONFIGURED")
        if not candidates_for_analysis and n_sql > 0 and sql_session_missing:
            day_reasons.append("SQL_SESSION_REQUIRED")
        if not candidates_for_analysis and n_sql > 0 and not sql_session_missing:
            day_reasons.append("NO_ANALYSIS_READY_DATA")

        if day_reasons:
            reasons.extend(day_reasons)

    # Determine overall readiness
    if sql_session_missing:
        readiness = "SESSION_REQUIRED"
        reason_code = "SQL_SESSION_REQUIRED"
    elif total_missing == 0 and candidates_for_analysis and "R2_NOT_CONFIGURED" not in reasons:
        readiness = "READY"
        reason_code = "FULL_COVERAGE"
    elif total_sql == 0 and total_fetched > 0:
        readiness = "NOT_READY"
        reason_code = "NO_SQL_RECORDS"
    elif total_fetched == 0:
        readiness = "NOT_READY"
        reason_code = "NO_MATCHES_FETCHED"
    else:
        readiness = "PARTIALLY_READY"
        reason_code = "PARTIAL_COVERAGE"

    # Build summary parts
    n_ol = len([p for d in fetched.values() for p in d[0]])
    n_fd = len([s for d in fetched.values() for s in d[1]])
    r2_vals = [v.get("r2_status") for v in r2_status.values()]
    r2_status_val = r2_vals[0] if r2_vals else "unknown"

    summary_parts = []
    summary_parts.append(f"Bundesliga Coverage Report ({len(days)} days, Matchday {matchday})")
    summary_parts.append(f"  Fetched from APIs: {total_fetched} ({n_ol} openligadb, {n_fd} football-data.org)")
    summary_parts.append(f"  In PostgreSQL: {total_sql} matches")
    summary_parts.append(f"  Missing from SQL: {total_missing} matches")
    summary_parts.append(f"  R2 Archive: {r2_status_val}")
    summary_parts.append(f"  Candidates for analysis: {len(candidates_for_analysis)}")
    if reasons:
        summary_parts.append(f"  Coverage Issues: {', '.join(set(reasons))}")
    summary_parts.append(f"  Readiness: {readiness}")

    report = {
        "summary": "\n".join(summary_parts),
        "total_days": len(days),
        "days": [d.isoformat() for d in days],
        "matchday": matchday,
        "fetched": {
            "openligadb": n_ol,
            "football_data_org": n_fd,
        },
        "postgresql": {
            "total_sql_matches": total_sql,
            "days": {day.isoformat(): sql_info.get("sql_matches", 0) for day, sql_info in pg_status.items()},
        },
        "r2_archive": r2_status,
        "missing_from_sql": total_missing,
        "candidates_for_analysis": candidates_for_analysis,
        "readiness": readiness,
        "reason_code": reason_code,
        "operation": "BUNDESLIGA_READ_ONLY_COVERAGE_REPORT",
        "sql_writes": 0,
        "r2_writes": 0,
        "sql_delete": False,
        "verified_at_modified": False,
    }

    return report


def main(argv=None) -> int:
    """CLI entry point for generating coverage report."""
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--days", required=False, default=None,
                        help="Comma-separated ISO dates, e.g. 2026-08-28,2026-08-29")
    parser.add_argument("--matchday", type=int, default=1, choices=range(1, 35),
                        help="Bundesliga matchday (1-34)")
    parser.add_argument("--session", default=None, help="SQLAlchemy connection string")
    args = parser.parse_args(argv)

    # Parse days
    days: list[date] | None = None
    if args.days:
        try:
            days = [date.fromisoformat(p.strip()) for p in args.days.split(",")]
        except ValueError:
            print(json.dumps({"error": "INVALID_DATE_FORMAT"}))
            return 2

    # Create session if connection string provided
    from sqlalchemy import create_engine
    session = None
    if args.session:
        engine = create_engine(args.session)
        session = sessionmaker(bind=engine)()

    try:
        report = generate_coverage_report(days=days, matchday=args.matchday, session=session)
    except CoverageReportException as exc:
        print(json.dumps({"status": "BLOCKED", "reason": str(exc)}))
        return 2
    except Exception as exc:
        print(json.dumps({"status": "ERROR", "reason": f"REPORT_FAILED: {str(exc)}"}))
        return 1

    # Output
    if args.out:
        with open(args.out, "w", encoding="utf-8") as f:
            json.dump(report, f, ensure_ascii=False, indent=2, default=str)
        print(json.dumps({"status": "WROTTEN", "path": args.out}))
    else:
        print(json.dumps(report, ensure_ascii=False, default=str, indent=2))

    return 0 if report["readiness"] in ("READY", "PARTIALLY_READY", "SESSION_REQUIRED") else 2


if __name__ == "__main__":
    raise SystemExit(main())