"""Read-only, explicitly targeted PostgreSQL schema preflight for season-bulk imports.

Does not apply migrations, perform imports, or access R2. Never prints credentials.
"""
from __future__ import annotations

import argparse
import json
import os

from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--expected-database", required=True)
    parser.add_argument("--require-migrated", action="store_true")
    args = parser.parse_args(argv)

    raw_url = os.environ.get("SEASON_BULK_PREFLIGHT_DATABASE_URL", "")
    if not raw_url:
        print(json.dumps({"status": "BLOCKED", "reason": "DATABASE_NOT_CONFIGURED"}))
        return 2
    parsed = make_url(raw_url)
    if (parsed.get_backend_name() != "postgresql"
            or not args.expected_database.strip()
            or parsed.database != args.expected_database):
        print(json.dumps({"status": "BLOCKED", "reason": "TARGET_DATABASE_MISMATCH"}))
        return 2

    engine = create_engine(raw_url, connect_args={"connect_timeout": 5})
    try:
        with engine.connect() as conn:
            with conn.begin():
                conn.execute(text("SET TRANSACTION READ ONLY"))
                actual = conn.scalar(text("SELECT current_database()"))
                if actual != args.expected_database:
                    print(json.dumps({"status": "BLOCKED", "reason": "ACTUAL_DATABASE_MISMATCH"}))
                    return 2
                rev = conn.scalar(text("SELECT version_num FROM alembic_version LIMIT 1"))
                tables = {t: bool(conn.scalar(text("SELECT to_regclass(:name) IS NOT NULL"),
                                               {"name": "public." + t})) for t in (
                    "season_teams", "season_fixtures", "season_odds", "season_imports"
                )}
                active = conn.scalar(text("SELECT count(*) FROM active_matches"))
                if rev not in {"0010_active_statistics", "0011_season_bulk"}:
                    status = "BLOCKED_UNEXPECTED_REVISION"
                elif rev == "0011_season_bulk" and not all(tables.values()):
                    status = "BLOCKED_INCOMPLETE_SCHEMA"
                elif rev == "0010_active_statistics" and any(tables.values()):
                    status = "BLOCKED_UNEXPECTED_TABLES"
                elif args.require_migrated and rev != "0011_season_bulk":
                    status = "MIGRATION_REQUIRED"
                else:
                    status = "READY_FOR_REVIEW" if rev == "0010_active_statistics" else "MIGRATION_PRESENT"
        print(json.dumps({"status": status, "database": actual, "alembic": rev,
                          "season_tables_present": sum(tables.values()),
                          "active_matches": active, "read_only": True}, sort_keys=True))
        return 0 if status in {"READY_FOR_REVIEW", "MIGRATION_PRESENT"} else 1
    finally:
        engine.dispose()


if __name__ == "__main__":
    try:
        exit_code = main()
    except Exception:
        # Database and SDK exceptions may contain connection credentials.
        print(json.dumps({"status": "BLOCKED", "reason": "OPERATION_FAILED"}))
        exit_code = 1
    raise SystemExit(exit_code)
