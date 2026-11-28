"""One-file, manually authorized corners/cards import: immutable R2 FIRST, SQL SECOND.

Dry-run by default. No HTTP, automatic scheduler, live ingestion, model
training, prediction settlement or destructive cleanup.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.engine import make_url

from app.historical.discipline_csv import LEAGUES, SEASONS, preview
from app.historical.discipline_archive import archive_source_file, _validate_report, DATASET
from app.models.historical_discipline import HistoricalDisciplineMatch
from app.models.metadata import ArchiveJob
from app.storage.archive import ArchiveReceipt, SQLArchiveTracker

FIELDS = ("home_corners", "away_corners", "home_yellows", "away_yellows", "home_reds", "away_reds")
LEGACY_DATABASE_NAMES = {"betappv33333", "kuponanaliziivv7", "kuponanaliziivv755"}


def preflight(session, *, report: dict, permission_reference: str) -> int:
    """Fail on changed previously imported identities, before any R2 operation.

    Do not assume same source identity equals same observations across CSV
    revisions. A change needs a separately reviewed append-only correction.
    """
    _validate_report(report, permission_reference)
    old = 0
    for row in report["rows"]:
        current = session.get(HistoricalDisciplineMatch, row.source_key)
        if current is None:
            continue
        if (current.source_file_sha256 != report["file_sha256"] or
                current.permission_reference != permission_reference.strip() or
                any(getattr(current, key) != getattr(row, key) for key in (
                    "league_id", "season", "match_date", "home_team", "away_team", *FIELDS))):
            raise ValueError("HISTORICAL_STAT_SOURCE_CONFLICT_MANUAL_REVIEW_REQUIRED")
        old += 1
    return old


def write_verified_file(
    session, *, report: dict, permission_reference: str, receipt: ArchiveReceipt,
) -> dict:
    """Index only an exact VERIFIED R2 archive receipt, never source-only CSV.

    Caller provides a receipt returned by `archive_source_file` and its
    SQLArchiveTracker. This function does not grant permission by itself.
    """
    league, season, anchor = _validate_report(report, permission_reference)
    preflight(session, report=report, permission_reference=permission_reference)
    job = session.scalar(select(ArchiveJob).where(ArchiveJob.r2_key == receipt.key))
    if (job is None or job.status != "VERIFIED" or job.verified_at is None or
            job.dataset_type != DATASET or job.league != league or job.archive_date != anchor or
            job.checksum != receipt.checksum or job.file_size_bytes != receipt.size or
            job.row_count != receipt.row_count or receipt.row_count != len(report["rows"])):
        raise ValueError("VERIFIED_R2_RECEIPT_REQUIRED")
    expected_prefix = f"{DATASET}/season={season}/league={league}/date={anchor.isoformat()}/part=source-{report['file_sha256']}.parquet"
    if receipt.key != expected_prefix:
        raise ValueError("R2_SOURCE_PARTITION_IDENTITY_MISMATCH")
    imported = skipped = 0
    for row in report["rows"]:
        current = session.get(HistoricalDisciplineMatch, row.source_key)
        if current is not None:
            if current.r2_archive_key != receipt.key or current.r2_archive_checksum != receipt.checksum:
                raise ValueError("R2_ARCHIVE_RECEIPT_CONFLICT")
            skipped += 1
            continue
        session.add(HistoricalDisciplineMatch(
            source_key=row.source_key,
            source=report["source"],
            source_url=report["source_url"],
            source_file_sha256=report["file_sha256"],
            r2_archive_key=receipt.key, r2_archive_checksum=receipt.checksum,
            permission_reference=permission_reference.strip(),
            league_id=row.league_id, league_name=row.league_name, season=row.season,
            match_date=row.match_date, home_team=row.home_team, away_team=row.away_team,
            **{key: getattr(row, key) for key in FIELDS},
        ))
        imported += 1
    session.flush()
    return {"inserted": imported, "unchanged": skipped, "conflicts": 0}


def archive_then_index(session, store, *, report: dict, permission_reference: str) -> dict:
    """No SQL statistic rows unless immutable Parquet R2 is read-back verified."""
    preflight(session, report=report, permission_reference=permission_reference)
    receipt = archive_source_file(
        store, SQLArchiveTracker(session), report=report, permission_reference=permission_reference,
    )
    outcome = write_verified_file(session, report=report, permission_reference=permission_reference,
                                  receipt=receipt)
    session.commit()
    return {**outcome, "r2_key": receipt.key, "r2_sha256": receipt.checksum,
            "r2_rows": receipt.row_count, "r2_size_bytes": receipt.size, "archive_status": "VERIFIED"}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Manual licensed CSV R2-first archiver (dry run by default)")
    parser.add_argument("--file", required=True, type=Path)
    parser.add_argument("--league", required=True, choices=list(LEAGUES))
    parser.add_argument("--season", required=True, choices=list(SEASONS))
    parser.add_argument("--expected-sha256", help="Exact checksum of manually inspected local input")
    parser.add_argument("--permission-reference", help="Reviewed written provider licence / agreement ID")
    parser.add_argument("--write", action="store_true", help="Explicitly request R2 upload and SQL index")
    parser.add_argument("--confirm-r2-upload", action="store_true", help="Independent explicit upload confirmation")
    parser.add_argument("--acknowledge-source-terms", action="store_true")
    args = parser.parse_args(argv)
    try:
        report = preview(args.file, league=args.league, season_code=args.season)
        public = {key: value for key, value in report.items() if key != "rows"}
        public.update(status="DRY_RUN_ONLY", database_writes=0, r2_writes=0, r2="NOT_RUN")
        if args.write:
            if not args.acknowledge_source_terms or not args.confirm_r2_upload or not args.permission_reference or (
                    args.expected_sha256 != report["file_sha256"]):
                raise ValueError("PERMISSION_AND_SHA256_R2_CONFIRMATION_REQUIRED")
            from app.core.config import get_settings
            from app.db.session import SessionLocal
            from app.storage.r2 import R2ObjectStore
            cfg = get_settings()
            if not cfg.database_url_valid or SessionLocal is None or not cfg.r2_configured:
                raise ValueError("SQL_OR_R2_NOT_CONFIGURED")
            database = (make_url(cfg.database_url).database or "").casefold()
            if database in LEGACY_DATABASE_NAMES or database.startswith("cb"):
                raise ValueError("LEGACY_DATABASE_REJECTED")
            store = R2ObjectStore(settings=cfg)
            try:
                with SessionLocal() as session:
                    result = archive_then_index(session, store, report=report,
                                                permission_reference=args.permission_reference)
            finally:
                if hasattr(store.client, "close"):
                    store.client.close()
            public.update(status="VERIFIED_R2_AND_SQL_INDEXED", database_writes=result["inserted"],
                          unchanged=result["unchanged"], r2="VERIFIED",
                          r2_key=result["r2_key"], r2_sha256=result["r2_sha256"],
                          r2_rows=result["r2_rows"], r2_bytes=result["r2_size_bytes"])
        print(json.dumps(public, ensure_ascii=False, indent=2))
        return 0
    except (ValueError, OSError) as exc:
        code = str(exc) if str(exc).isupper() and len(str(exc)) < 120 else "DISCIPLINE_IMPORT_FAILED"
        parser.exit(2, code + "\n")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
