"""One-time audited upload of nine previously downloaded, licensed season CSVs.

This is a *manual* operator command; no scheduler, scraper, server route, or
CI R2 credentials. Validates the complete 9-file manifest before one write,
then R2 Parquet verification and SQL indexing for each file. Partial progress
is idempotent; never claims an all-nine success on an interrupted batch.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import re

from app.historical.discipline_csv import LEAGUES, SEASONS, preview
from app.jobs.discipline_source_download import SCOPE, _approved_permission
from app.jobs.historical_discipline_import import archive_then_index, LEGACY_DATABASE_NAMES

SHA = re.compile(r"^[0-9a-f]{64}$")
MAX_MANIFEST_BYTES = 15_000


def load_batch_plan(directory: Path, *, rights_ref: str) -> list[dict]:
    """Exactly 9 source files, current hashes, source identities and coverage."""
    ref = _approved_permission(rights_ref)
    directory = directory.resolve(strict=True)
    path = directory / "manifest.json"
    if not path.is_file() or path.is_symlink() or path.stat().st_size > MAX_MANIFEST_BYTES:
        raise ValueError("SOURCE_MANIFEST_MISSING_OR_UNSAFE")
    try:
        manifest = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ValueError("SOURCE_MANIFEST_INVALID") from exc
    if (not isinstance(manifest, dict) or manifest.get("source") != "football-data.co.uk"
            or manifest.get("permission_reference") != ref or
            manifest.get("requested_files") != 9 or manifest.get("received_files") != 9 or
            not isinstance(manifest.get("files"), list) or len(manifest["files"]) != 9):
        raise ValueError("SOURCE_MANIFEST_SCOPE_OR_RIGHTS_MISMATCH")

    expected = {(s, d) for s, d in SCOPE}
    found = set()
    ready = []
    for item in manifest["files"]:
        if not isinstance(item, dict) or not isinstance(item.get("file"), str):
            raise ValueError("SOURCE_MANIFEST_ENTRY_INVALID")
        name = item["file"]
        pair = tuple(name.removesuffix(".csv").split("_"))
        if (not name.endswith(".csv") or len(pair) != 2 or pair not in expected
                or pair in found):
            raise ValueError("SOURCE_FILE_SCOPE_CONFLICT")
        found.add(pair)
        season, league = pair
        candidate = directory / name
        if not candidate.is_file() or candidate.is_symlink():
            raise ValueError("SOURCE_CSV_MISSING_OR_SYMLINK")
        report = preview(candidate, league=league, season_code=season)
        if (not isinstance(item.get("sha256"), str) or not SHA.fullmatch(item["sha256"])
                or report["file_sha256"] != item["sha256"] or
                report["season"] != item.get("season") or
                report["league"] != item.get("league") or
                any(report[k] != item.get(k) for k in (
                    "matches_with_any_stat","complete_corners","complete_yellows","complete_reds"))):
            raise ValueError("SOURCE_MANIFEST_HASH_OR_COVERAGE_CHANGED")
        ready.append(report)
    if found != expected:
        raise ValueError("SOURCE_MANIFEST_INCOMPLETE")
    return sorted(ready, key=lambda r: (r["season"], r["league"]))


def run_upload(session_factory, store, plans: list[dict], *, permission_reference: str) -> list[dict]:
    """Fully validated plans only; one independently verified R2+SQL transaction per file.

    An R2 object may persist if SQL fails; a subsequent idempotent rerun
    reconciles the object, never deletes it. Do not expose partial count as
    overall success.
    """
    if len(plans) != 9 or {(p["season"],p["league"]) for p in plans} != {
            (SEASONS[season], LEAGUES[league][0]) for season, league in SCOPE}:
        raise ValueError("UPLOAD_REQUIRES_COMPLETE_NINE_FILE_PLAN")
    summary = []
    for report in plans:
        with session_factory() as session:
            try:
                result = archive_then_index(session, store, report=report,
                                            permission_reference=permission_reference)
            except Exception:
                session.rollback()
                raise
        summary.append({
            "league": report["league"], "season": report["season"],
            "source_sha256": report["file_sha256"],
            "r2_key": result["r2_key"], "r2_sha256": result["r2_sha256"],
            "archive_status": result["archive_status"], "inserted": result["inserted"],
            "unchanged": result["unchanged"], "r2_rows": result["r2_rows"],
        })
    return summary


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Dry-run by default; authorized 9-file R2-first SQL import")
    parser.add_argument("--directory", required=True, type=Path)
    parser.add_argument("--permission-reference", required=True)
    parser.add_argument("--apply", action="store_true", help="Explicit R2+SQL write after source rights review")
    parser.add_argument("--acknowledge-source-terms", action="store_true")
    parser.add_argument("--confirm-r2-upload", action="store_true")
    parser.add_argument("--confirm-database-name", help="Exact operator-reviewed target PostgreSQL name")
    args = parser.parse_args(argv)
    try:
        plan = load_batch_plan(args.directory, rights_ref=args.permission_reference)
        coverage = [{"league": r["league"], "season": r["season"],
                     "source_sha256": r["file_sha256"],
                     "rows": len(r["rows"])} for r in plan]
        if not args.apply:
            print(json.dumps({"status":"DRY_RUN", "validated_files":9,"r2_uploaded":False,
                              "sql_written":False, "coverage":coverage}, ensure_ascii=False,indent=2))
            return 0
        if not (args.acknowledge_source_terms and args.confirm_r2_upload
                and args.confirm_database_name):
            raise ValueError("LIVE_PERMISSION_TARGET_AND_R2_CONFIRMATION_REQUIRED")

        from sqlalchemy.engine import make_url
        from app.core.config import get_settings
        from app.db.session import SessionLocal
        from app.storage.r2 import R2ObjectStore
        cfg = get_settings()
        if SessionLocal is None or not cfg.database_url_valid or not cfg.r2_configured:
            raise ValueError("DATABASE_OR_R2_NOT_CONFIGURED")
        db_name = (make_url(cfg.database_url).database or "")
        if (db_name != args.confirm_database_name or
                db_name.casefold() in LEGACY_DATABASE_NAMES or db_name.casefold().startswith("cb")):
            raise ValueError("DATABASE_TARGET_REVIEW_REQUIRED")
        store = R2ObjectStore(settings=cfg)
        try:
            results = run_upload(SessionLocal,store,plan,permission_reference=args.permission_reference)
        finally:
            if hasattr(store.client,"close"):
                store.client.close()
        print(json.dumps({"status":"ALL_NINE_R2_VERIFIED_AND_SQL_INDEXED",
                          "file_count":len(results),"files":results},ensure_ascii=False,indent=2))
        return 0
    except (ValueError,OSError) as exc:
        code = str(exc) if isinstance(exc, ValueError) and str(exc).isupper() and len(str(exc)) < 120 else "BATCH_UPLOAD_FAILED"
        print(json.dumps({"status":"BLOCKED_OR_PARTIAL","reason":code,
                          "all_nine_verified":False},ensure_ascii=False))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
