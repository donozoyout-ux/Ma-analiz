"""Read-only destination preflight for the historic corners/cards R2 archive.

No data downloads, no R2 uploads, no SQL writes, no migrations, no status
transitions, no credentials printed. The check is an operator diagnostic only;
it does NOT prove provider licensing, valid source data or upload permission.
"""
from __future__ import annotations

import argparse
import json

from sqlalchemy import text
from sqlalchemy.engine import make_url

from app.jobs.historical_discipline_import import LEGACY_DATABASE_NAMES

EXPECTED_HEAD = "0011_historical_discipline"
_DATASET_PREFIX = "match-statistics/"


class DestinationBlocked(ValueError):
    """Preflight blocked without logging credentials or remote error bodies."""


def verify_target_names(cfg, *, expected_database: str, expected_bucket: str) -> str:
    """Fail before creating any SQL session or R2 client if identity is untrusted."""
    if not cfg.database_url_valid or not cfg.r2_configured:
        raise DestinationBlocked("DATABASE_OR_R2_NOT_CONFIGURED")
    actual_database = make_url(cfg.database_url).database or ""
    if (not expected_database or actual_database != expected_database
            or actual_database.casefold() in LEGACY_DATABASE_NAMES
            or actual_database.casefold().startswith("cb")):
        raise DestinationBlocked("DATABASE_TARGET_REVIEW_REQUIRED")
    if not expected_bucket or cfg.r2_bucket != expected_bucket:
        raise DestinationBlocked("R2_BUCKET_REVIEW_REQUIRED")
    return actual_database


def inspect_destination(session, s3_client, *, database_name: str, bucket: str) -> dict:
    """Reads only static PostgreSQL metadata and one bounded S3 LIST response.

    A successful LIST shows at most that the configured bucket is readable.
    It does not establish R2 PUT/conditional-write or downstream licensing.
    """
    actual = session.execute(text("SELECT current_database()")).scalar_one()
    if actual != database_name:
        raise DestinationBlocked("DATABASE_CONNECTION_IDENTITY_MISMATCH")
    rows = session.execute(text("SELECT version_num FROM public.alembic_version")).all()
    heads = [row[0] for row in rows]
    if heads != [EXPECTED_HEAD]:
        raise DestinationBlocked("ALEMBIC_HEAD_MISMATCH")
    tables = session.execute(text("""
        SELECT
          to_regclass('public.archive_jobs') IS NOT NULL AS archive_jobs,
          to_regclass('public.historical_discipline_matches') IS NOT NULL AS stats,
          to_regclass('public.active_matches') IS NOT NULL AS active_matches
    """)).one()
    if not all(tables):
        raise DestinationBlocked("REQUIRED_SQL_TABLE_MISSING")
    result = session.execute(text("""
        SELECT COUNT(*) AS verified_partitions
        FROM public.archive_jobs
        WHERE dataset_type='match-statistics' AND status='VERIFIED'
    """)).scalar_one()
    try:
        # Never enumerate, download, overwrite or delete any match-statistics.
        s3_client.list_objects_v2(Bucket=bucket, Prefix=_DATASET_PREFIX, MaxKeys=1)
    except Exception as exc:
        raise DestinationBlocked("R2_READ_ONLY_ACCESS_FAILED") from exc
    return {
        "status": "READ_ONLY_TARGET_PREFLIGHT_PASSED",
        "database_identity_match": True,
        "alembic_revision": EXPECTED_HEAD,
        "required_sql_tables_present": True,
        "r2_bucket_identity_match": True,
        "r2_prefix_list_access": True,
        "verified_sql_archive_receipts": result,
        "r2_write_capability_tested": False,
        "source_licence_verified": False,
        "real_match_data_uploaded": False,
        "write_operations_performed": 0,
    }


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Non-mutating historical corner/card SQL+R2 destination preflight")
    parser.add_argument("--expected-database", required=True,
                        help="Operator-reviewed current MA-ANALIZ Postgres database name")
    parser.add_argument("--expected-r2-bucket", required=True,
                        help="Operator-reviewed existing Cloudflare R2 bucket name")
    args = parser.parse_args(argv)
    try:
        from app.core.config import get_settings
        cfg = get_settings()
        database_name = verify_target_names(
            cfg, expected_database=args.expected_database, expected_bucket=args.expected_r2_bucket,
        )
        from app.db.session import SessionLocal
        from app.storage.r2 import R2ObjectStore
        if SessionLocal is None:
            raise DestinationBlocked("DATABASE_NOT_CONFIGURED")
        # SQL schema and connection must be verified before touching R2.
        with SessionLocal() as session:
            observed_db = session.execute(text("SELECT current_database()")).scalar_one()
            if observed_db != database_name:
                raise DestinationBlocked("DATABASE_CONNECTION_IDENTITY_MISMATCH")
            versions = [v[0] for v in session.execute(
                text("SELECT version_num FROM public.alembic_version")).all()]
            if versions != [EXPECTED_HEAD]:
                raise DestinationBlocked("ALEMBIC_HEAD_MISMATCH")
            store = R2ObjectStore(settings=cfg)
            try:
                report = inspect_destination(
                    session, store.client, database_name=database_name, bucket=cfg.r2_bucket,
                )
            finally:
                if hasattr(store.client, "close"):
                    store.client.close()
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0
    except Exception as exc:
        # Database URL / SDK exception text can contain credentials or names.
        code = str(exc) if isinstance(exc, DestinationBlocked) else "TARGET_PREFLIGHT_FAILED"
        print(json.dumps({"status": "BLOCKED", "reason": code,
                          "write_operations_performed": 0}))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
