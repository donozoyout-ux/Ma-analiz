"""Production-only dependency gate for a deliberately bounded first backfill.

Run this command inside the Render service/worker environment so it inherits
Render's configured secrets. It never prints environment values. By default it
only checks readiness. The one-day backfill requires the explicit
``--run-backfill`` flag and runs only after every dependency and migration gate
passes. Migrations are never applied by this command.
"""
from __future__ import annotations

import argparse
import contextlib
import io
import json
import sys
from typing import Any, Callable

from alembic.config import Config
from alembic.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import text
from sqlalchemy.engine import Engine, make_url

from app.core.config import PROJECT_ROOT, get_settings
from app.db.session import engine as app_engine
from app.jobs.r2_smoke_test import run_smoke
from app.providers.base import ProviderFailure
from app.providers.registry import ProviderCaller, create_provider
from app.api.routes import health as internal_health


EXPECTED_ALEMBIC_HEAD = "0010_active_statistics"
LEGACY_DATABASE_NAMES = {
    "betappv33", "betappv33333", "kuponanaliziivv7", "kuponanaliziivv755",
}
FIRST_BACKFILL_ARGS = (
    "--league", "36", "--season", "2025-2026",
    "--start-date", "2025-08-15", "--end-date", "2025-08-15", "--batch-size", "1",
)


def _database_identity(settings: Any) -> tuple[str | None, str | None, bool]:
    """Return non-secret host/database identifiers and reject known old DBs."""
    try:
        parsed = make_url(settings.database_url or "")
    except Exception:
        return None, None, False
    database = parsed.database
    normalized = (database or "").casefold()
    legacy = normalized in LEGACY_DATABASE_NAMES or normalized.startswith("cb")
    return parsed.host, database, not legacy


def _check_postgres(settings: Any, db_engine: Engine | None) -> dict[str, Any]:
    result: dict[str, Any] = {
        "configured": "NOT_CONFIGURED", "connection": "FAIL", "identity": "FAIL",
        "version": "FAIL", "database": None, "host": None, "error_code": None,
    }
    if not settings.database_url or not settings.database_url_valid or db_engine is None:
        result["error_code"] = "DATABASE_NOT_CONFIGURED"
        return result
    result["configured"] = "CONFIGURED"
    host, database, allowed = _database_identity(settings)
    if not allowed:
        result["error_code"] = "LEGACY_DATABASE_REJECTED"
        return result
    try:
        with db_engine.connect() as connection:
            row = connection.execute(text(
                "SELECT 1 AS probe, version() AS version, current_database() AS database, "
                "current_schema() AS schema"
            )).mappings().one()
        if row["probe"] != 1:
            raise RuntimeError("DATABASE_PROBE_FAILED")
        actual_db = str(row["database"])
        if actual_db.casefold() in LEGACY_DATABASE_NAMES or actual_db.casefold().startswith("cb"):
            result["error_code"] = "LEGACY_DATABASE_REJECTED"
            return result
        if database and actual_db != database:
            result["error_code"] = "DATABASE_IDENTITY_MISMATCH"
            return result
        if not str(row["version"] or "").strip() or not str(row["schema"] or "").strip():
            result["error_code"] = "DATABASE_IDENTITY_INCOMPLETE"
            return result
        result.update(connection="PASS", identity="PASS", version="PASS",
                      database=actual_db, host=host)
    except Exception as exc:
        # Never serialize connection exception strings: drivers can include URLs.
        result["error_code"] = type(exc).__name__.upper()
    return result


def _check_alembic(settings: Any, db_engine: Engine | None,
                   *, script_directory_factory: Callable[[Config], Any] = ScriptDirectory.from_config) -> dict[str, Any]:
    result = {"status": "FAIL", "current": [], "heads": [], "error_code": None}
    if db_engine is None or not settings.database_url_valid:
        result["error_code"] = "DATABASE_NOT_CONFIGURED"
        return result
    try:
        config = Config(str(PROJECT_ROOT / "backend" / "alembic.ini"))
        config.set_main_option("script_location", str(PROJECT_ROOT / "backend" / "migrations"))
        scripts = script_directory_factory(config)
        heads = sorted(scripts.get_heads())
        with db_engine.connect() as connection:
            current = sorted(MigrationContext.configure(connection).get_current_heads())
        result.update(current=current, heads=heads)
        expected = [EXPECTED_ALEMBIC_HEAD]
        if heads == expected and current == expected:
            result["status"] = "PASS"
        elif heads == expected:
            result.update(status="NEEDS_MIGRATION", error_code="ALEMBIC_HEAD_MISMATCH")
        else:
            result.update(status="FAIL", error_code="CODE_ALEMBIC_HEAD_MISMATCH")
    except Exception as exc:
        result["error_code"] = type(exc).__name__.upper()
    return result


def _check_goaloo(settings: Any, *, provider_factory: Callable[[Any], Any] = create_provider) -> dict[str, Any]:
    result = {"status": "FAIL", "matches": 0, "results": 0, "odds": 0,
              "snapshots": [], "closing_rows": 0, "error_code": None}
    if (str(settings.provider_id or "").lower() != "goaloo"
            or "36" not in settings.enabled_league_ids
            or "2025-2026" not in settings.enabled_season_ids):
        result.update(status="NOT_CONFIGURED", error_code="GOALOO_SCOPE_NOT_CONFIGURED")
        return result
    provider = None
    try:
        provider = provider_factory(settings)
        caller = ProviderCaller(provider, requests_per_minute=settings.provider_requests_per_minute,
                                max_retries=settings.provider_retry_count)
        leagues = caller.call("get_leagues")
        if not any(str(row.get("id", row.get("league_id", ""))) == "36"
                   and "premier" in str(row.get("name", "")).casefold() for row in leagues):
            raise RuntimeError("GOALOO_LEAGUE_IDENTITY_MISMATCH")
        seasons = caller.call("get_seasons", "36")
        if not any(str(row.get("id", row.get("season_id", ""))) == "2025-2026" for row in seasons):
            raise RuntimeError("GOALOO_SEASON_UNAVAILABLE")
        from datetime import date
        day = date(2025, 8, 15)
        matches = caller.call("get_matches", day)
        matches = [row for row in matches if str(row.get("league_id")) == "36"
                   and str(row.get("season")) == "2025-2026"]
        if len(matches) != 1:
            raise RuntimeError("GOALOO_EXPECTED_MATCH_COUNT_MISMATCH")
        match_id = str(matches[0].get("provider_match_id", ""))
        if not match_id:
            raise RuntimeError("GOALOO_MATCH_ID_MISSING")
        results = caller.call("get_results", day)
        results = [row for row in results if str(row.get("provider_match_id")) == match_id]
        odds = caller.call("get_odds", match_id)
        if not results or not odds:
            raise RuntimeError("GOALOO_RESULT_OR_ODDS_MISSING")
        snapshots = sorted({str(row.get("snapshot_type")) for row in odds})
        closing = sum(str(row.get("snapshot_type", "")).upper() == "CLOSING" for row in odds)
        if closing:
            raise RuntimeError("GOALOO_CLOSING_SNAPSHOT_UNEXPECTED")
        result.update(status="PASS", matches=len(matches), results=len(results), odds=len(odds),
                      snapshots=snapshots, closing_rows=closing)
    except Exception as exc:
        result["error_code"] = getattr(exc, "code", None) or type(exc).__name__.upper()
    finally:
        close = getattr(provider, "close", None)
        if callable(close):
            close()
    return result


def _internal_health_snapshot(health_fn: Callable[[], dict[str, Any]] = internal_health) -> dict[str, str]:
    """Use in-process diagnostics; never call a public endpoint or bypass auth."""
    try:
        payload = health_fn()
        services = payload.get("services", {})
        worker = payload.get("worker")
        # The existing health API exposes last-job state, not a worker heartbeat.
        # Report that the worker has recorded activity as CONFIGURED; never
        # present a stale JobRun as proof of a currently live process.
        worker_status = "CONFIGURED" if worker not in {None, "UNKNOWN", "NO_JOB_RUNS"} else "FAIL"
        scanner = services.get("scanner")
        return {
            "api": "PASS" if payload.get("status") in {"ok", "healthy"} else "FAIL",
            "postgres": "PASS" if payload.get("database") == "CONNECTED" else "FAIL",
            "r2": "PASS" if payload.get("r2") == "CONNECTED" else "FAIL",
            "goaloo": "PASS" if services.get("goaloo") == "CONNECTED" else "FAIL",
            "historical": "PASS" if payload.get("archive") == "VERIFIED" else "FAIL",
            "worker": worker_status,
            "scanner": "CONFIGURED" if scanner == "CONFIGURED" else "FAIL",
        }
    except Exception:
        return {key: "FAIL" for key in ("api", "postgres", "r2", "goaloo", "historical", "worker", "scanner")}


def _gate_passed(report: dict[str, Any]) -> bool:
    return all(report[name] == "PASS" for name in ("postgres", "alembic", "r2", "goaloo"))


def _verify_backfill_objects(db_engine: Engine, *, store_factory: Callable[[], Any]) -> dict[str, Any]:
    """Verify only the three known objects for the single test day."""
    from datetime import date
    from sqlalchemy.orm import Session
    from app.models import ArchiveJob
    from app.storage.archive import ArchiveStatus, _verify_object

    store = store_factory()
    datasets = ("matches", "results", "odds")
    verified = []
    with Session(db_engine) as session:
        for dataset in datasets:
            key = f"{dataset}/season=2025-2026/league=36/date=2025-08-15.parquet"
            job = session.query(ArchiveJob).filter_by(r2_key=key).one_or_none()
            if job is None or job.status != ArchiveStatus.VERIFIED.value:
                raise RuntimeError("ARCHIVE_JOB_NOT_VERIFIED")
            info = store.head_object(key)
            if info is None or info.size <= 0 or not job.checksum or not job.row_count:
                raise RuntimeError("ARCHIVE_OBJECT_METADATA_INVALID")
            _verify_object(store, key, expected_size=job.file_size_bytes,
                           expected_rows=job.row_count, expected_checksum=job.checksum)
            rows = store.read_parquet(key)
            if len(rows) != job.row_count:
                raise RuntimeError("ARCHIVE_PARQUET_ROW_COUNT_MISMATCH")
            verified.append({"key": key, "rows": len(rows), "bytes": info.size,
                             "status": ArchiveStatus.VERIFIED.value})
    return {"objects": verified, "total_bytes": sum(item["bytes"] for item in verified)}


def run_readiness(*, settings: Any | None = None, db_engine: Engine | None = None,
                  postgres_check: Callable[..., dict[str, Any]] = _check_postgres,
                  alembic_check: Callable[..., dict[str, Any]] = _check_alembic,
                  r2_runner: Callable[..., dict[str, Any]] = run_smoke,
                  goaloo_check: Callable[..., dict[str, Any]] = _check_goaloo,
                  health_check: Callable[[], dict[str, str]] = _internal_health_snapshot) -> dict[str, Any]:
    cfg = settings or get_settings()
    db = app_engine if db_engine is None else db_engine
    pg = postgres_check(cfg, db)
    pg_pass = pg["connection"] == "PASS" and pg["identity"] == "PASS" and pg["version"] == "PASS"
    alembic = alembic_check(cfg, db)
    r2_result = r2_runner(settings=cfg)
    r2 = "PASS" if r2_result.get("status") == "CONNECTED" else "FAIL"
    goaloo_result = goaloo_check(cfg)
    goaloo = "PASS" if goaloo_result.get("status") == "PASS" else "FAIL"
    return {
        "postgres": "PASS" if pg_pass else "FAIL",
        "postgres_details": pg,
        "alembic": "PASS" if alembic.get("status") == "PASS" else "FAIL",
        "alembic_details": alembic,
        "r2": r2,
        "r2_details": r2_result,
        "goaloo": goaloo,
        "goaloo_details": goaloo_result,
        "internal_health": health_check(),
    }


def execute(*, run_backfill: bool, readiness: Callable[[], dict[str, Any]] = run_readiness,
            backfill: Callable[[list[str]], int] | None = None,
            verifier: Callable[[], dict[str, Any]] | None = None) -> tuple[dict[str, Any], int]:
    report = readiness()
    report["backfill"] = "BLOCKED" if not _gate_passed(report) else "NOT_RUN"
    if not _gate_passed(report):
        return report, 1
    report["backfill"] = "ALLOWED"
    if not run_backfill:
        return report, 0

    if backfill is None:
        from app.jobs.historical_backfill import main as backfill_main
        backfill = lambda args: backfill_main(args)
    # Suppress child output: even an unexpected third-party message must not
    # leak environment values into the operations log.
    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
        code = backfill(list(FIRST_BACKFILL_ARGS))
    if code != 0:
        report["backfill"] = "FAIL"
        report["backfill_error"] = "HISTORICAL_BACKFILL_FAILED"
        return report, int(code or 1)
    if verifier is None:
        from app.storage.r2 import R2ObjectStore
        verifier = lambda: _verify_backfill_objects(app_engine, store_factory=R2ObjectStore)
    try:
        report["backfill_verification"] = verifier()
        report["backfill"] = "PASS"
        return report, 0
    except Exception as exc:
        report["backfill"] = "FAIL"
        report["backfill_error"] = type(exc).__name__.upper()
        return report, 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-backfill", action="store_true",
                        help="run only the fixed one-day test backfill after all readiness gates pass")
    args = parser.parse_args(argv)
    report, exit_code = execute(run_backfill=args.run_backfill)
    # Allowlist the report: never dump arbitrary provider, driver, or config data.
    for key in ("postgres", "alembic", "r2", "goaloo", "backfill"):
        print(f"{key.upper()}={report.get(key, 'FAIL')}")
    pg = report.get("postgres_details", {})
    if pg.get("database"):
        print("DATABASE_NAME=" + str(pg["database"]))
    if pg.get("host"):
        print("DATABASE_HOST=" + str(pg["host"]))
    alembic = report.get("alembic_details", {})
    if alembic.get("status") == "NEEDS_MIGRATION":
        print("ALEMBIC_DETAIL=NEEDS_MIGRATION")
    print("ALEMBIC_CURRENT=" + (",".join(alembic.get("current", [])) or "NONE"))
    print("ALEMBIC_HEADS=" + (",".join(alembic.get("heads", [])) or "NONE"))
    for key, value in report.get("internal_health", {}).items():
        print(f"HEALTH_{key.upper()}={value}")
    for dataset in report.get("backfill_verification", {}).get("objects", []):
        print(f"R2_OBJECT={dataset['key']} ROWS={dataset['rows']} BYTES={dataset['bytes']} ARCHIVE={dataset['status']}")
    if report.get("backfill_error"):
        print("BACKFILL_ERROR=" + report["backfill_error"])
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
