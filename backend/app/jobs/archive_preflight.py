"""Secret-safe configuration check and strictly one-league/one-day archive smoke.

Never migrates, runs bulk backfills, or invokes database cleanup.
"""
from __future__ import annotations

import argparse
import contextlib
import io
import json
from datetime import date

from app.core.config import PROJECT_ROOT, get_settings


def check_postgres(settings):
    from alembic.config import Config
    from alembic.runtime.migration import MigrationContext
    from alembic.script import ScriptDirectory
    from sqlalchemy import create_engine, text
    url = settings.database_url
    if url.startswith("postgresql://"):
        url = url.replace("postgresql://", "postgresql+psycopg://", 1)
    engine = None
    try:
        engine = create_engine(url, hide_parameters=True, connect_args={"connect_timeout": 10})
        with engine.connect() as connection, connection.begin():
            connection.execute(text("SET TRANSACTION READ ONLY"))
            connection.execute(text("SET LOCAL statement_timeout = '10s'"))
            assert connection.execute(text("SELECT 1")).scalar_one() == 1
            current = set(MigrationContext.configure(connection).get_current_heads())
        cfg = Config(str(PROJECT_ROOT / "backend" / "alembic.ini"))
        cfg.set_main_option("script_location", str(PROJECT_ROOT / "backend" / "migrations"))
        expected = set(ScriptDirectory.from_config(cfg).get_heads())
        return {"connection": "PASS", "schema": "PASS" if current == expected else "NEEDS_MIGRATION"}
    except Exception as exc:
        return {"connection": "FAILED", "schema": "NOT_VERIFIED", "error_code": type(exc).__name__.upper()}
    finally:
        if engine is not None:
            engine.dispose()


def verify_scope_archives(league, season, day):
    from sqlalchemy import select
    from app.db.session import SessionLocal
    from app.models import ArchiveJob
    from app.storage.archive import _verify_object
    from app.storage.r2 import R2ObjectStore
    store = R2ObjectStore()
    counts = {"matches": 0, "results": 0, "statistics": 0}
    with SessionLocal() as session:
        jobs = session.scalars(select(ArchiveJob).where(
            ArchiveJob.league == league, ArchiveJob.archive_date == day,
            ArchiveJob.dataset_type.in_(counts), ArchiveJob.status.in_(("VERIFIED", "DELETED")),
        )).all()
        for job in jobs:
            prefix = f"{job.dataset_type}/season={season}/league={league}/date={day.isoformat()}"
            if not (job.r2_key == prefix + ".parquet" or
                    (job.r2_key.startswith(prefix + "/part=") and job.r2_key.endswith(".parquet"))):
                continue
            if not job.checksum or not job.verified_at or not job.row_count or not job.file_size_bytes:
                raise RuntimeError("ARCHIVE_RECEIPT_INCOMPLETE")
            rows = _verify_object(store, job.r2_key, expected_size=job.file_size_bytes,
                                   expected_rows=job.row_count, expected_checksum=job.checksum)
            counts[job.dataset_type] += len(rows)
    if not counts["matches"] or not counts["results"]:
        raise RuntimeError("REAL_MATCH_AND_RESULT_ARCHIVES_REQUIRED")
    return counts


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--smoke", action="store_true", help="connect, verify Parquet, and archive exactly one real day without cleanup")
    parser.add_argument("--league")
    parser.add_argument("--season")
    parser.add_argument("--date", type=date.fromisoformat)
    args = parser.parse_args(argv)
    settings = get_settings()
    missing = list(settings.r2_missing_env) + (["DATABASE_URL"] if settings.database_url_missing else [])
    invalid = list(settings.r2_invalid_env) + (["DATABASE_URL"] if not settings.database_url_missing and not settings.database_url_valid else [])
    report = {"status": "NOT_CONFIGURED" if missing or invalid else "CONFIGURED_NOT_VERIFIED",
              "missing_env": missing, "invalid_env": invalid,
              "configuration_locations": [str(PROJECT_ROOT / ".env"), "Render Environment: backend, worker, archive cron"],
              "postgres": {"connection": "NOT_RUN", "schema": "NOT_VERIFIED"},
              "r2": {"status": "NOT_RUN"}, "archive": "NOT_RUN", "cleanup": "NOT_RUN", "bulk": "NOT_RUN"}
    code = 1 if missing or invalid else 0
    if args.smoke and not missing and not invalid:
        valid_scope = bool(args.league and args.season and args.date and "," not in args.league and "," not in args.season
                           and args.league in settings.enabled_league_ids and args.season in settings.enabled_season_ids)
        if valid_scope:
            from app.jobs.historical_backfill import season_window
            try:
                window = season_window(args.season)
                valid_scope = bool(window and window[0] <= args.date <= window[1]
                                   and (not settings.historical_start_date or args.date >= settings.historical_start_date))
            except ValueError:
                valid_scope = False
        if not valid_scope:
            report.update(status="BLOCKED", error_code="SINGLE_ENABLED_LEAGUE_SEASON_DATE_REQUIRED")
            code = 2
        else:
            report["scope"] = {"league": args.league, "season": args.season, "date": str(args.date), "odds_requested": False}
            report["postgres"] = check_postgres(settings)
            if report["postgres"] != {"connection": "PASS", "schema": "PASS"}:
                report.update(status="BLOCKED", error_code="POSTGRES_NOT_READY")
                code = 1
            else:
                from app.jobs.r2_smoke_test import run_smoke
                report["r2"] = run_smoke(parquet=True)
                if report["r2"]["status"] != "CONNECTED":
                    report.update(status="BLOCKED", error_code="R2_SMOKE_FAILED")
                    code = 1
                else:
                    from app.jobs.historical_backfill import main as backfill
                    output = io.StringIO()
                    try:
                        with contextlib.redirect_stdout(output):
                            code = backfill(["--league", args.league, "--season", args.season,
                                             "--start-date", str(args.date), "--end-date", str(args.date),
                                             "--batch-size", "1", "--skip-odds"])
                        result = json.loads(output.getvalue().strip().splitlines()[-1])
                        report["archive"] = result
                        if code == 0:
                            report["verified_r2_rows"] = verify_scope_archives(args.league, args.season, args.date)
                        report["status"] = "SMOKE_PASS" if code == 0 else "FAILED"
                        code = 0 if report["status"] == "SMOKE_PASS" else 1
                    except Exception as exc:
                        report.update(status="FAILED", error_code=type(exc).__name__.upper())
                        code = 1
    print(json.dumps(report, ensure_ascii=False, default=str))
    return code


if __name__ == "__main__":
    raise SystemExit(main())
