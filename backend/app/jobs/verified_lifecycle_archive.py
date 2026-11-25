"""Single-fixture read-only preflight or explicitly approved R2 lifecycle archiving.

Only Bayern-Stuttgart (2026-08-28, OpenLigaDB 83156) is in scope.
No SQL deletion, no catch-all fixture scan, and no archive overwrite.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from uuid import UUID

from sqlalchemy import select

from app.models import ActiveMatch, MatchResult, SystemSetting
from app.storage import lifecycle as lc
from app.storage.archive import SQLArchiveTracker, archive_partition
from app.providers.result_sources import utc

MATCH_ID = UUID("952969f9-ca6c-4e1a-9ef2-cf7b8f9e578a")
SOURCE_ID = "83156"
KICKOFF = datetime(2026, 8, 28, 18, 30, tzinfo=timezone.utc)


class LifecycleBlocked(ValueError):
    pass


def _validate(session, now):
    match = session.get(ActiveMatch, MATCH_ID)
    if (match is None or match.provider != "openligadb" or
            match.provider_match_id != SOURCE_ID or match.league_id != "de.bl1" or
            match.season != "2026-2027" or match.status != "FINISHED" or
            utc(match.start_time) != KICKOFF or
            match.home_team != "FC Bayern München" or match.away_team != "VfB Stuttgart"):
        raise LifecycleBlocked("TARGET_MATCH_IDENTITY_NOT_VERIFIED")
    results = session.scalars(select(MatchResult).where(MatchResult.match_id == MATCH_ID).limit(2)).all()
    if len(results) != 1:
        raise LifecycleBlocked("TARGET_RESULT_NOT_UNIQUE")
    result = results[0]
    if (result.status != "FINAL" or result.provider != "openligadb" or
            result.home_score != 5 or result.away_score != 1 or
            utc(result.verified_at) is None or utc(result.verified_at) > now):
        raise LifecycleBlocked("TARGET_RESULT_NOT_INDEPENDENTLY_VERIFIED")
    return match, result


def run(session, store=None, *, live=False, approval="", now=None):
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        raise LifecycleBlocked("AWARE_NOW_REQUIRED")
    if live and (approval != "ARCHIVE_ONE_VERIFIED_MATCH_ONLY" or store is None):
        raise LifecycleBlocked("EXPLICIT_ARCHIVE_APPROVAL_AND_R2_REQUIRED")
    match, result = _validate(session, now)
    records = [lc.make_record(session, match, entity, now=now, hours=32) for entity in (match, result)]
    for record in records:
        if record.blockers:
            raise LifecycleBlocked("ARCHIVE_POLICY_OR_RETENTION_BLOCKED")
    if not live:
        return {"status": "DRY_RUN_READY", "match_id": str(MATCH_ID), "records": [
            {"dataset": r.dataset, "key": r.key, "sha256": r.checksum,
             "size_bytes": r.size} for r in records],
            "r2_writes": 0, "sql_result_writes": 0, "sql_delete": False}
    tracker = SQLArchiveTracker(session)
    receipts = []
    for record in records:
        receipt = archive_partition(
            store, tracker=tracker, dataset=record.dataset,
            season=record.season, league=record.league, day=record.day,
            rows=[record.row], part=record.part, retries=1)
        issue = lc.verify_record(session, store, record)
        if issue:
            raise LifecycleBlocked("ARCHIVE_READBACK_NOT_VERIFIED")
        receipts.append({"dataset": record.dataset, "r2_key": receipt.key,
                         "sha256": receipt.checksum, "row_count": receipt.row_count})
    # Preserve immutable identity index; refuse to change any existing mapping.
    key = lc.source_index_key(match)
    index = session.get(SystemSetting, key)
    if index is not None:
        if (index.value.get("match_id") != str(MATCH_ID) or
                index.value.get("provider") != "openligadb" or
                index.value.get("provider_match_id") != SOURCE_ID):
            raise LifecycleBlocked("SOURCE_IDENTITY_INDEX_CONFLICT")
    else:
        session.add(SystemSetting(key=key, value={
            "provider": match.provider, "provider_match_id": match.provider_match_id,
            "match_id": str(match.id), "season": match.season,
            "league_id": match.league_id,
            "first_verified_fixture_key": records[0].key}))
        session.commit()
    from app.api.match_center import detail_view
    view = detail_view(session, MATCH_ID, source="history", now=now, store=store)
    if (view.get("storage") != "R2" or
            (view.get("result") or {}).get("home_score") != 5 or
            (view.get("result") or {}).get("away_score") != 1):
        raise LifecycleBlocked("ARCHIVED_HISTORY_READBACK_INCOMPLETE")
    return {"status": "ARCHIVED_AND_HISTORY_READBACK_VERIFIED",
            "match_id": str(MATCH_ID), "receipts": receipts,
            "historical_score": "5-1", "sql_result_writes": 0,
            "sql_delete": False}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--approval", default="")
    args = parser.parse_args(argv)
    from app.core.config import get_settings
    cfg = get_settings()
    if cfg.auto_retention_delete_enabled or cfg.operational_delete_enabled:
        print(json.dumps({"status":"BLOCKED","reason":"DELETE_GATES_MUST_BE_DISABLED"}))
        return 2
    if args.live and args.approval != "ARCHIVE_ONE_VERIFIED_MATCH_ONLY":
        print(json.dumps({"status":"BLOCKED","reason":"LIVE_APPROVAL_REQUIRED"}))
        return 2
    if not cfg.database_url_valid or (args.live and not cfg.r2_configured):
        print(json.dumps({"status":"BLOCKED","reason":"SQL_OR_R2_NOT_CONFIGURED"}))
        return 2
    try:
        from app.db.session import SessionLocal
        from app.storage.r2 import R2ObjectStore
        if SessionLocal is None: raise LifecycleBlocked("SQL_NOT_CONFIGURED")
        with SessionLocal() as session:
            report = run(session,
                R2ObjectStore(settings=cfg) if args.live else None,
                live=args.live, approval=args.approval)
            if not args.live: session.rollback()
    except LifecycleBlocked as exc:
        print(json.dumps({"status":"BLOCKED","reason":str(exc),"sql_delete":False}))
        return 2
    except Exception:
        print(json.dumps({"status":"FAILED","reason":"ARCHIVE_OR_READBACK_FAILED","sql_delete":False}))
        return 1
    print(json.dumps(report,default=str,ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
