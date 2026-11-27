"""Guarded operator entry point for the first reviewed Champions League import."""
from __future__ import annotations

import argparse
import hashlib
import json
import os

import httpx
from sqlalchemy import create_engine, select, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session

from app.historical.season_transfer import import_season
from app.models.season_bulk import season_fixtures
from app.providers.season_files import DownloadBudget, download, parse_season, source_for
from app.storage.r2 import R2ObjectStore

DATABASE = "cbcxbcxbcv"
INSTANCE = "dpg-dbtlovifngtc739kaf1g-a"
REVISION = "abfaeddc2ee3d14f99ecc163c9ddb46cb4d67cef"
SHA256 = "c2692b22af02c5df4587b69c5f430aebf7d936845b5337aee462ce8e649eb1b5"
SOURCE_URL = f"https://raw.githubusercontent.com/openfootball/champions-league/{REVISION}/2024-25/cl.txt"
APPROVAL = "IMPORT_189_UCL_2024_2025"


def validate_database_url(raw_url: str) -> None:
    try:
        url = make_url(raw_url)
        host = (url.host or "").lower()
        valid_host = host in {INSTANCE, INSTANCE + ".frankfurt-postgres.render.com"}
        if (url.get_backend_name() != "postgresql" or url.database != DATABASE or not valid_host
                or url.username != "cbcxbcxbcv_user" or url.query.get("sslmode") not in {"require", "verify-ca", "verify-full"}):
            raise ValueError
    except Exception:
        raise RuntimeError("TARGET_DATABASE_MISMATCH") from None


def validate_source(source, raw: bytes) -> list[dict]:
    if (source is None or source.league != "uefa.cl" or source.season != "2024-2025"
            or source.provider != "openfootball" or source.revision != REVISION
            or source.licence != "CC0-1.0" or source.url != SOURCE_URL):
        raise RuntimeError("SOURCE_SCOPE_MISMATCH")
    if hashlib.sha256(raw).hexdigest() != SHA256:
        raise RuntimeError("SOURCE_SHA256_MISMATCH")
    rows = parse_season(source, raw)
    if len(rows) != 189 or sum(row["regulation"] is not None for row in rows) != 189:
        raise RuntimeError("SOURCE_MATCH_COUNT_MISMATCH")
    return rows


def check_database(connection) -> None:
    if connection.scalar(text("SELECT current_database()")) != DATABASE:
        raise RuntimeError("ACTUAL_DATABASE_MISMATCH")
    revisions = connection.execute(text("SELECT version_num FROM alembic_version")).scalars().all()
    if revisions != ["0011_season_bulk"]:
        raise RuntimeError("MIGRATION_VERSION_MISMATCH")
    for table in ("season_teams", "season_fixtures", "season_odds", "season_imports"):
        if not connection.scalar(text("SELECT to_regclass(:name) IS NOT NULL"), {"name": "public." + table}):
            raise RuntimeError("MIGRATION_SCHEMA_INCOMPLETE")


def run(*, live: bool = False, approval: str = "") -> dict:
    if live and approval != APPROVAL:
        raise RuntimeError("OPERATOR_APPROVAL_REQUIRED")
    raw_url = os.environ.get("SEASON_BULK_OPERATOR_DATABASE_URL", "")
    validate_database_url(raw_url)
    engine = create_engine(raw_url, hide_parameters=True, connect_args={"connect_timeout": 5})
    try:
        with engine.connect() as connection:
            with connection.begin():
                connection.execute(text("SET TRANSACTION READ ONLY"))
                check_database(connection)
        source = source_for("uefa.cl", "2024-2025")
        with httpx.Client(trust_env=False) as client:
            raw = download(source, client, DownloadBudget(remaining_attempts=3))
        rows = validate_source(source, raw)
        if not live:
            return {"status": "DRY_RUN", "fixtures": len(rows), "sha256": SHA256}
        store = R2ObjectStore()
        with Session(engine) as session, session.begin():
            check_database(session.connection())
            receipt = import_season(session, store, source, raw, approval="IMPORT_REVIEWED_SEASON_ONLY")
            stored = session.execute(select(season_fixtures.c.id, season_fixtures.c.data).where(
                season_fixtures.c.league == "uefa.cl", season_fixtures.c.season == "2024-2025")).all()
            count = len(stored)
            expected = {row["id"]: row["regulation"] for row in rows}
            if (count != 189 or {key: data.get("regulation") for key, data in stored} != expected
                    or receipt["fixtures"] != 189 or receipt["regulation_scores"] != 189):
                raise RuntimeError("SQL_MATCH_COUNT_MISMATCH")
        return {"status": "COMPLETED", "fixtures": count, "sha256": SHA256,
                "archive_verification": receipt["verification"], "reused": receipt["reused"]}
    finally:
        engine.dispose()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--approval", default="")
    args = parser.parse_args(argv)
    try:
        report = run(live=args.live, approval=args.approval)
    except Exception as exc:
        known = {"OPERATOR_APPROVAL_REQUIRED", "TARGET_DATABASE_MISMATCH", "ACTUAL_DATABASE_MISMATCH",
                 "MIGRATION_VERSION_MISMATCH", "MIGRATION_SCHEMA_INCOMPLETE", "SOURCE_SCOPE_MISMATCH",
                 "SOURCE_SHA256_MISMATCH", "SOURCE_MATCH_COUNT_MISMATCH", "SQL_MATCH_COUNT_MISMATCH",
                 "ARCHIVE_RECEIPT_MISMATCH", "ARCHIVE_CHECKSUM_MISMATCH", "ARCHIVE_ROW_COUNT_MISMATCH"}
        reason = str(exc) if str(exc) in known else "PREFLIGHT_OR_IMPORT_FAILED"
        print(json.dumps({"status": "BLOCKED", "reason": reason}))
        return 1
    print(json.dumps(report))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
