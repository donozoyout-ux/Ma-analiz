"""Bounded one-day, one-league Sportmonks verification command.

This never launches a backfill. Provider calls are read-only. Add --persist to
upsert only the selected league/date and upload its partitions; cleanup is never
run by this command.
"""
from __future__ import annotations

import argparse
import json
import unicodedata
from collections import defaultdict
from datetime import date
from pathlib import Path
from typing import Any

from app.core.config import get_settings
from app.db.session import SessionLocal, engine
from app.providers.base import ProviderFailure
from app.providers.normalize import normalize_odds
from app.providers.registry import ProviderCaller, create_provider


def _fold(value: str) -> str:
    return "".join(char for char in unicodedata.normalize("NFKD", value.casefold())
                   if not unicodedata.combining(char))


def _find_turkish_super_lig(leagues: list[dict]) -> dict:
    matches = []
    for league in leagues:
        name = _fold(str(league.get("name") or ""))
        country = _fold(str(league.get("country") or ""))
        league_like = "super lig" in name or "superliga" in name
        turkey_like = country in {"turkey", "turkiye"} or "turkey" in name or "turkiye" in name
        if league_like and turkey_like:
            matches.append(league)
    if len(matches) != 1:
        raise ValueError("TURKISH_SUPER_LIG_NOT_FOUND" if not matches else "TURKISH_SUPER_LIG_AMBIGUOUS")
    return matches[0]


def _archive_rows(store: Any, rows: list[dict], *, dataset: str, season: str, league: str, day: date) -> dict:
    from app.storage.archive import archive_partition
    if not rows:
        return {"status": "SKIPPED", "row_count": 0}
    receipt = archive_partition(store, dataset=dataset, season=season, league=league, day=day,
                                rows=rows, tracker=None)
    info = store.head_object(receipt.key)
    actual = store.read_parquet(receipt.key)
    passed = bool(info and info.metadata and info.metadata.get("sha256") == receipt.checksum
                  and info.metadata.get("archive-key") == receipt.key
                  and info.metadata.get("row-count") == str(len(rows))
                  and len(actual) == len(rows) and actual == rows)
    if not passed:
        raise RuntimeError("R2_SMOKE_VERIFICATION_FAILED")
    return {"status": "PASS", "key": receipt.key, "row_count": receipt.row_count,
            "checksum": receipt.checksum, "size_bytes": receipt.size,
            "exists": True, "readback": True}


def _persist_postgres(provider, caller: ProviderCaller, matches: list[dict],
                      odds_by_match: dict[str, list[dict]], day: date) -> dict:
    if engine is None or SessionLocal is None:
        return {"status": "POSTGRES_NOT_CONFIGURED"}
    from alembic import command
    from alembic.config import Config
    from sqlalchemy import func, select
    from app.jobs.ingestion import upsert_matches, upsert_odds, ingest_results
    from app.jobs.results import settle_verified_results
    from app.models import ActiveMatch, ActiveOdds

    with engine.connect() as connection:
        connection.exec_driver_sql("SELECT 1")
    config = Config(str(Path(__file__).resolve().parents[2] / "alembic.ini"))
    command.upgrade(config, "head")
    provider_ids = {row["provider_match_id"] for row in matches}
    with SessionLocal() as session:
        existing = set(session.scalars(select(ActiveMatch.provider_match_id).where(
            ActiveMatch.provider == provider.name, ActiveMatch.provider_match_id.in_(provider_ids))).all())
        first_written, first_skipped = upsert_matches(session, matches)
        after_first = set(session.scalars(select(ActiveMatch.provider_match_id).where(
            ActiveMatch.provider == provider.name, ActiveMatch.provider_match_id.in_(provider_ids))).all())
        second_written, second_skipped = upsert_matches(session, matches)
        after_second = set(session.scalars(select(ActiveMatch.provider_match_id).where(
            ActiveMatch.provider == provider.name, ActiveMatch.provider_match_id.in_(provider_ids))).all())
        ids = session.scalars(select(ActiveMatch.id, ActiveMatch.provider_match_id).where(
            ActiveMatch.provider == provider.name, ActiveMatch.provider_match_id.in_(provider_ids))).all()
        match_rows = {str(provider_id): session.get(ActiveMatch, match_id) for match_id, provider_id in ids}
        odds_first = odds_second = odds_count = 0
        for provider_match_id, payloads in odds_by_match.items():
            match = match_rows.get(provider_match_id)
            if match is None:
                continue
            first, _ = upsert_odds(session, provider.name, match, payloads)
            second, _ = upsert_odds(session, provider.name, match, payloads)
            odds_first += first
            odds_second += second
        session.commit()
        db_match_count = session.scalar(select(func.count()).select_from(ActiveMatch).where(
            ActiveMatch.provider == provider.name, ActiveMatch.provider_match_id.in_(provider_ids))) or 0
        duplicate_matches = max(0, db_match_count - len(provider_ids))
        result_ingest = ingest_results(session, caller, day)
        settlement_first = settle_verified_results(session, day=day)
        settlement_second = settle_verified_results(session, day=day)
        odds_rows = session.scalar(select(func.count()).select_from(ActiveOdds).where(
            ActiveOdds.match_id.in_(select(ActiveMatch.id).where(
                ActiveMatch.provider == provider.name, ActiveMatch.provider_match_id.in_(provider_ids))))) or 0
    return {
        "status": "PASS", "migration": "PASS", "matches_after_first_run": len(after_first),
        "new_matches_first_run": len(after_first - existing),
        "new_matches_second_run": len(after_second - after_first),
        "upsert_attempts": {"first": first_written, "second": second_written},
        "upsert_skipped": {"first": first_skipped, "second": second_skipped},
        "duplicate_matches": duplicate_matches, "odds_rows": odds_rows,
        "odds_first_run_written": odds_first, "odds_second_run_written": odds_second,
        "results": result_ingest,
        "settlement_first": settlement_first, "settlement_second": settlement_second,
    }


def run(day: date, *, persist: bool = False) -> dict:
    settings = get_settings()
    try:
        provider = create_provider(settings)
    except ProviderFailure as exc:
        return {"provider": "SPORTMONKS", "provider_status": exc.code, "smoke_status": "LIVE SMOKE TEST BLOCKED",
                "league": "Turkish Süper Lig", "date": day.isoformat(), "matches_found": None,
                "matches_normalized": None, "matches_rejected": None, "matches_with_odds": None,
                "odds_records": None, "results_found": None, "errors": [exc.code],
                "postgres": "POSTGRES_NOT_CONFIGURED" if SessionLocal is None else "NOT_RUN",
                "r2": "R2_NOT_CONFIGURED" if not (settings.r2_endpoint and settings.r2_access_key_id and settings.r2_secret_access_key) else "NOT_RUN"}
    caller = ProviderCaller(provider, requests_per_minute=settings.provider_requests_per_minute,
                            max_retries=settings.provider_retry_count)
    latencies: list[float] = []
    errors: list[str] = []

    def call(method: str, *args):
        value = caller.call(method, *args)
        latency = getattr(provider, "last_latency_ms", None)
        if latency is not None:
            latencies.append(float(latency))
        return value

    try:
        call("health_check")
        leagues = call("get_leagues")
        league = _find_turkish_super_lig(leagues)
        seasons = call("get_seasons", league["id"])
        fixtures = call("get_matches", day)
    except (ProviderFailure, ValueError) as exc:
        code = getattr(exc, "code", str(exc))
        errors.append(code)
        return {"provider": "SPORTMONKS", "provider_status": code, "smoke_status": "FAILED",
                "league": "Turkish Süper Lig", "league_id": None, "seasons_discovered": [],
                "date": day.isoformat(), "matches_found": 0, "matches_normalized": 0,
                "matches_rejected": getattr(provider, "last_records_rejected", None),
                "matches_with_odds": 0, "odds_records": 0, "results_found": 0,
                "provider_latency_ms_avg": round(sum(latencies) / len(latencies), 2) if latencies else None,
                "errors": errors}

    league_matches = [row for row in fixtures if row["league_id"] == league["id"]]
    matches = []
    rejected = getattr(provider, "last_records_rejected", 0)
    season_by_name = {item["name"]: item["id"] for item in seasons}
    for row in league_matches:
        if row.get("season") not in season_by_name or row.get("provider_season_id") != season_by_name.get(row.get("season")):
            rejected += 1
            continue
        matches.append(row)

    odds_by_match: dict[str, list[dict]] = {}
    odds_flat: list[dict] = []
    for match in matches:
        try:
            payloads = call("get_odds", match["provider_match_id"])
            normalized = [normalize_odds(item, match_id=match["provider_match_id"], provider=provider.name)
                          for item in payloads]
            odds_by_match[match["provider_match_id"]] = payloads
            odds_flat.extend(normalized)
        except ProviderFailure as exc:
            errors.append(exc.code)
            break
        except (TypeError, ValueError):
            errors.append("PROVIDER_INVALID_RESPONSE")
            break

    match_ids = {row["provider_match_id"] for row in matches}
    try:
        provider_results = call("get_results", day)
        results = [row for row in provider_results if row["provider_match_id"] in match_ids]
    except ProviderFailure as exc:
        errors.append(exc.code)
        results = []

    report: dict[str, Any] = {
        "provider": "SPORTMONKS", "provider_status": "PROVIDER_OK", "league": league["name"],
        "league_id": league["id"], "season_names": sorted({row["season"] for row in matches}),
        "season_ids": sorted({row["provider_season_id"] for row in matches}),
        "seasons_discovered": seasons, "date": day.isoformat(),
        "matches_found": len(league_matches), "matches_normalized": len(matches),
        "matches_rejected": rejected,
        "matches_with_odds": len({row["match_id"] for row in odds_flat}),
        "odds_records": len(odds_flat), "markets_available": sorted({row["market"] for row in odds_flat}),
        "bookmakers_available": sorted({row["bookmaker"] for row in odds_flat}),
        "historical_odds_supported": provider.historical_odds_supported,
        "opening_odds_supported": provider.opening_odds_supported,
        "current_odds_supported": provider.current_odds_supported,
        "results_found": len(results), "provider_latency_ms_avg": round(sum(latencies) / len(latencies), 2) if latencies else None,
        "errors": errors,
    }
    report["smoke_status"] = "LIVE SMOKE TEST BLOCKED" if errors or not matches else "PROVIDER DATA VERIFIED"

    if not persist:
        report["postgres"] = "NOT_RUN (pass --persist to write one-day smoke data)"
        report["r2"] = "NOT_RUN (pass --persist to archive one-day smoke data)"
        return report

    if not matches:
        report["postgres"] = "NOT_RUN_NO_MATCHES"
        report["r2"] = "NOT_RUN_NO_MATCHES"
        report["smoke_status"] = "LIVE SMOKE TEST BLOCKED"
        return report

    from app.storage.r2 import R2NotConfigured, R2ObjectStore
    try:
        store = R2ObjectStore()
        match_groups: dict[tuple[str, str], list[dict]] = defaultdict(list)
        for match in matches:
            match_groups[(match["season"], match["league_id"])].append(match)
        r2_objects = []
        for (season, league_id), rows in match_groups.items():
            r2_objects.append(_archive_rows(store, rows, dataset="matches", season=season,
                                            league=league_id, day=day))
            related_odds = [row for row in odds_flat if row["match_id"] in {m["provider_match_id"] for m in rows}]
            if related_odds:
                r2_objects.append(_archive_rows(store, related_odds, dataset="odds", season=season,
                                                league=league_id, day=day))
            related_results = [dict(row, league_id=league_id, season=season) for row in results
                               if row["provider_match_id"] in {m["provider_match_id"] for m in rows}]
            if related_results:
                r2_objects.append(_archive_rows(store, related_results, dataset="results", season=season,
                                                league=league_id, day=day))
        report["r2"] = {"status": "PASS", "objects": r2_objects}
    except R2NotConfigured:
        report["r2"] = "R2_NOT_CONFIGURED"
    except Exception:
        report["r2"] = {"status": "FAILED", "error_code": "R2_SMOKE_VERIFICATION_FAILED"}

    try:
        report["postgres"] = _persist_postgres(provider, caller, matches, odds_by_match, day)
    except Exception as exc:
        # Do not include driver exception text, which can contain a DSN or credentials.
        report["postgres"] = {"status": "POSTGRES_UNAVAILABLE", "error_code": type(exc).__name__.upper()}
    if (not isinstance(report["r2"], dict) or report["r2"].get("status") != "PASS"
            or not isinstance(report["postgres"], dict) or report["postgres"].get("status") != "PASS"):
        report["smoke_status"] = "LIVE SMOKE TEST BLOCKED"
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description="One-day Turkish Süper Lig Sportmonks smoke test; no backfill or cleanup")
    parser.add_argument("--date", type=date.fromisoformat, required=True, help="One date selected from verified provider coverage")
    parser.add_argument("--persist", action="store_true", help="Upsert only this date/league and archive its R2 partitions")
    args = parser.parse_args()
    report = run(args.date, persist=args.persist)
    print(json.dumps(report, ensure_ascii=False, default=str, indent=2))
    if report.get("smoke_status") in {"FAILED", "LIVE SMOKE TEST BLOCKED"}:
        raise SystemExit(2)


if __name__ == "__main__":
    main()
