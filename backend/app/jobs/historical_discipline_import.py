"""Manual licensed CSV import. Dry-run by default; never fetches external sources.

This deliberately does NOT link to OpenLigaDB canonical IDs or backfill model
training as if observations existed before the import time.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from sqlalchemy import select

from app.historical.discipline_csv import LEAGUES, METRICS, SEASONS, preview
from app.models.historical_discipline import HistoricalDisciplineMatch

FIELDS = ("home_corners", "away_corners", "home_yellows", "away_yellows", "home_reds", "away_reds")
LEGACY_DATABASE_NAMES = {"betappv33333", "kuponanaliziivv7", "kuponanaliziivv755"}


def write_verified_file(session, *, report: dict, permission_reference: str) -> dict:
    """Insert only previously unseen source identities in caller-owned transaction.

    "verified" means the operator approved the import permission; this
    function does not independently verify score correctness or licence rights.
    On conflict, fail the entire transaction rather than rewriting history.
    """
    if not isinstance(permission_reference, str) or len(permission_reference.strip()) < 12:
        raise ValueError("EXPLICIT_PERMISSION_REFERENCE_REQUIRED")
    imported = skipped = 0
    for row in report["rows"]:
        current = session.get(HistoricalDisciplineMatch, row.source_key)
        if current is not None:
            if any(getattr(current, key) != getattr(row, key) for key in (
                "league_id", "season", "match_date", "home_team", "away_team", *FIELDS)):
                raise ValueError("HISTORICAL_STAT_SOURCE_CONFLICT_MANUAL_REVIEW_REQUIRED")
            skipped += 1
            continue
        session.add(HistoricalDisciplineMatch(
            source_key=row.source_key,
            source=report["source"],
            source_url=report["source_url"],
            source_file_sha256=report["file_sha256"],
            permission_reference=permission_reference.strip(),
            league_id=row.league_id, league_name=row.league_name, season=row.season,
            match_date=row.match_date, home_team=row.home_team, away_team=row.away_team,
            **{key: getattr(row, key) for key in FIELDS},
        ))
        imported += 1
    session.flush()
    return {"inserted": imported, "unchanged": skipped, "conflicts": 0}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Manual licensed corner/card CSV loader (dry run by default)")
    parser.add_argument("--file", required=True, type=Path)
    parser.add_argument("--league", required=True, choices=list(LEAGUES))
    parser.add_argument("--season", required=True, choices=list(SEASONS))
    parser.add_argument("--expected-sha256", help="Digest from a separately inspected local file")
    parser.add_argument("--permission-reference", help="Documented independently reviewed source usage permission")
    parser.add_argument("--write", action="store_true", help="Explicitly permit SQL inserts after review and migration")
    parser.add_argument("--acknowledge-source-terms", action="store_true")
    args = parser.parse_args(argv)
    try:
        report = preview(args.file, league=args.league, season_code=args.season)
        public = {key: value for key, value in report.items() if key != "rows"}
        public["status"] = "DRY_RUN_ONLY"
        public["database_writes"] = 0

        if args.write:
            if not args.acknowledge_source_terms or not args.permission_reference or (
                    args.expected_sha256 != report["file_sha256"]):
                raise ValueError("PERMISSION_AND_SHA256_CONFIRMATION_REQUIRED")
            from app.core.config import get_settings
            from app.db.session import SessionLocal
            from sqlalchemy.engine import make_url
            cfg = get_settings()
            if not cfg.database_url_valid or SessionLocal is None:
                raise ValueError("DATABASE_NOT_CONFIGURED")
            database = (make_url(cfg.database_url).database or "").casefold()
            if database in LEGACY_DATABASE_NAMES or database.startswith("cb"):
                raise ValueError("LEGACY_DATABASE_REJECTED")
            with SessionLocal.begin() as session:
                result = write_verified_file(session, report=report,
                                             permission_reference=args.permission_reference)
            public.update(status="IMPORTED", database_writes=result["inserted"],
                          unchanged=result["unchanged"])
        print(json.dumps(public, ensure_ascii=False, indent=2))
        return 0
    except (ValueError, OSError) as exc:
        code = str(exc) if str(exc).isupper() and len(str(exc)) < 100 else "DISCIPLINE_IMPORT_FAILED"
        parser.exit(2, code + "\n")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
