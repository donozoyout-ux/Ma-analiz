"""Controlled, resumable Goaloo historical backfill to verified R2 partitions."""
from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import date, datetime, timedelta, timezone
from typing import Any

from app.core.config import get_settings
from app.db.session import SessionLocal, engine
from app.jobs.ingestion import historical_ingest
from app.providers.base import ProviderFailure
from app.providers.registry import ProviderCaller, create_provider
from app.storage.monitor import StorageMonitor
from app.storage.r2 import R2ObjectStore

_TOTAL_FIELDS = (
    "records_seen", "matches_seen", "dates_requested", "dates_completed", "dates_skipped", "failed_dates",
    "matches_written", "results_written", "odds_written", "duplicates", "invalid_rows", "r2_objects",
    "r2_bytes", "checksum_failures", "postgres_rows_written", "postgres_rows_deleted",
)


def empty_totals() -> dict[str, int]:
    return dict.fromkeys(_TOTAL_FIELDS, 0)


def season_window(season: str, *, today: date | None = None) -> tuple[date, date] | None:
    """Map Goaloo's YYYY-YYYY season ID to its July-June competition window."""
    if not re.fullmatch(r"\d{4}-\d{4}", season or ""):
        raise ValueError("season must use Goaloo's YYYY-YYYY format")
    try:
        start_year, end_year = (int(value) for value in season.split("-", maxsplit=1))
    except (AttributeError, ValueError):
        raise ValueError("season must use Goaloo's YYYY-YYYY format") from None
    if end_year != start_year + 1:
        raise ValueError("season must use Goaloo's YYYY-YYYY format")
    current = today or datetime.now(timezone.utc).date()
    start, end = date(start_year, 7, 1), min(date(end_year, 6, 30), current)
    return (start, end) if start <= end else None


def _csv_arg(value: str | None) -> list[str] | None:
    if value is None:
        return None
    values = [part.strip() for part in value.split(",") if part.strip()]
    return values or None


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--league", help="one or more configured provider league IDs, comma-separated")
    parser.add_argument("--season", help="one or more configured YYYY-YYYY seasons, comma-separated")
    parser.add_argument("--start-date", type=date.fromisoformat)
    parser.add_argument("--end-date", type=date.fromisoformat)
    parser.add_argument("--dry-run", action="store_true", help="show planned scopes without DB/R2 writes")
    parser.add_argument("--batch-size", type=int, default=90, help="maximum dates processed per DB batch")
    return parser


def _scope_dates(season: str, settings: Any, start: date | None, end: date | None) -> tuple[date, date] | None:
    window = season_window(season)
    if window is None:
        return None
    lower, upper = window
    if settings.historical_start_date:
        lower = max(lower, settings.historical_start_date)
    lower = max(lower, start) if start else lower
    upper = min(upper, end) if end else upper
    if lower > upper:
        return None
    return lower, upper


def _add_totals(total: dict[str, int], outcome: dict[str, Any]) -> None:
    for field in ("records_seen", "matches_seen", "matches_written", "results_written", "odds_written", "duplicates",
                  "invalid_rows", "r2_objects", "r2_bytes", "checksum_failures",
                  "postgres_rows_written", "postgres_rows_deleted", "dates_requested", "dates_skipped"):
        total[field] += int(outcome.get(field, 0) or 0)
    total["dates_completed"] += int(outcome.get("dates_completed", 0) or 0)
    total["failed_dates"] += int(outcome.get("dates_failed", 0) or 0) + int(outcome.get("dates_partial", 0) or 0)


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    settings = get_settings()
    if args.batch_size < 1:
        print(json.dumps({"status": "FAILED", "error_code": "INVALID_BATCH_SIZE"}), flush=True)
        return 2
    if bool(args.start_date) != bool(args.end_date) or (args.start_date and args.end_date < args.start_date):
        print(json.dumps({"status": "FAILED", "error_code": "INVALID_DATE_RANGE"}), flush=True)
        return 2

    configured_leagues, configured_seasons = settings.enabled_league_ids, settings.enabled_season_ids
    leagues, seasons = _csv_arg(args.league) or list(configured_leagues), _csv_arg(args.season) or list(configured_seasons)
    if not leagues or not seasons:
        print(json.dumps({"status": "BLOCKED", "error_code": "HISTORICAL_SCOPE_NOT_CONFIGURED",
                          "required": ["ENABLED_LEAGUES", "ENABLED_SEASONS"]}), flush=True)
        return 1
    if set(leagues) - set(configured_leagues) or set(seasons) - set(configured_seasons):
        print(json.dumps({"status": "FAILED", "error_code": "SCOPE_NOT_ENABLED",
                          "league_ids_must_be_enabled": sorted(set(leagues) - set(configured_leagues)),
                          "seasons_must_be_enabled": sorted(set(seasons) - set(configured_seasons))}), flush=True)
        return 2
    plan = []
    for season in seasons:
        try:
            window = _scope_dates(season, settings, args.start_date, args.end_date)
        except ValueError:
            plan.append({"season": season, "status": "SKIPPED", "reason": "INVALID_SEASON_ID"})
            continue
        if window is None:
            plan.append({"season": season, "status": "SKIPPED", "reason": "SEASON_NOT_AVAILABLE_OR_OUTSIDE_DATE_SCOPE"})
            continue
        start_day, end_day = window
        for league in leagues:
            plan.append({"league": league, "season": season, "start_date": start_day,
                         "end_date": end_day, "dates_requested": (end_day - start_day).days + 1})
    if args.dry_run:
        print(json.dumps({"status": "DRY_RUN", "batch_size": args.batch_size,
                          "scopes": plan, "writes": False}, default=str), flush=True)
        return 0
    if SessionLocal is None or engine is None:
        print(json.dumps({"status": "BLOCKED", "error_code": "DATABASE_NOT_CONFIGURED"}), flush=True)
        return 1
    if settings.r2_configuration_issues:
        print(json.dumps({"status": "BLOCKED", "error_code": "R2_NOT_CONFIGURED",
                          "missing_or_invalid": list(settings.r2_configuration_issues)}), flush=True)
        return 1
    storage_status = StorageMonitor(engine, settings).historical_ingestion_status()
    if storage_status == "critical":
        print(json.dumps({"status": "BLOCKED", "error_code": "STORAGE_CRITICAL",
                          "storage_status": storage_status}), flush=True)
        return 1
    if storage_status == "DATABASE_STORAGE_CHECK_FAILED":
        print(json.dumps({"status": "BLOCKED", "error_code": storage_status}), flush=True)
        return 1

    try:
        provider = create_provider(settings)
        caller = ProviderCaller(provider, requests_per_minute=settings.provider_requests_per_minute,
                                max_retries=settings.provider_retry_count)
        store = R2ObjectStore()
    except ProviderFailure as exc:
        print(json.dumps({"status": "BLOCKED", "error_code": exc.code}), flush=True)
        return 1
    except Exception as exc:
        print(json.dumps({"status": "BLOCKED", "error_code": type(exc).__name__.upper()}), flush=True)
        return 1

    totals = empty_totals()
    outcomes: list[dict[str, Any]] = []
    try:
        for scope in plan:
            if scope.get("status") == "SKIPPED":
                outcomes.append(scope)
                continue
            league, season = scope["league"], scope["season"]
            available = {str(row.get("id", row.get("season_id", "")))
                         for row in caller.call("get_seasons", league)}
            if season not in available:
                outcomes.append({"league": league, "season": season, "status": "SKIPPED",
                                 "reason": "SEASON_NOT_AVAILABLE", "dates_requested": scope["dates_requested"]})
                continue
            cursor = scope["start_date"]
            while cursor <= scope["end_date"]:
                chunk_end = min(scope["end_date"], cursor + timedelta(days=args.batch_size - 1))
                with SessionLocal() as session:
                    result = historical_ingest(session, caller, store, season=season, league=league,
                                               start_date=cursor, end_date=chunk_end)
                metrics = {**result, "dates_requested": (chunk_end - cursor).days + 1,
                           "matches_seen": result.get("records_seen", 0),
                           "failed_dates": int(result.get("dates_failed", 0) or 0)
                           + int(result.get("dates_partial", 0) or 0)}
                item = {"league": league, "season": season, "start_date": cursor,
                        "end_date": chunk_end, **metrics}
                outcomes.append(item)
                _add_totals(totals, metrics)
                if result.get("status") == "BLOCKED":
                    print(json.dumps({"status": "BLOCKED", "error_code": result.get("error_code"),
                                      "storage_status": result.get("storage_status"),
                                      "completed_scopes": outcomes, **totals}, default=str), flush=True)
                    return 1
                cursor = chunk_end + timedelta(days=1)
    except KeyboardInterrupt:
        print(json.dumps({"status": "PARTIAL", "error_code": "JOB_INTERRUPTED",
                          "completed_scopes": outcomes, "resume": "rerun the same command; completed dates are idempotent",
                          **totals}, default=str), flush=True)
        return 130
    except Exception as exc:
        # Exception strings can contain request URLs or configuration. Emit type only.
        print(json.dumps({"status": "FAILED", "error_code": type(exc).__name__.upper(),
                          "completed_scopes": outcomes, **totals}, default=str), flush=True)
        return 1
    finally:
        close = getattr(provider, "close", None)
        if callable(close):
            close()

    failed = any(item.get("status") in {"FAILED", "PARTIAL", "BLOCKED"} for item in outcomes)
    total_written = totals["matches_written"] + totals["results_written"] + totals["odds_written"]
    status = "FAILED" if failed else "SUCCESS" if total_written else "NO_DATA"
    print(json.dumps({"status": status, "scopes": outcomes, **totals}, default=str), flush=True)
    return 1 if failed or not total_written else 0


if __name__ == "__main__":
    sys.exit(main())
