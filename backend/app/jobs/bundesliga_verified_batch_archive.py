"""Bounded Bundesliga lifecycle archive for independently verified SQL results.

Manual, read-only by default. Only archives matches backed by an immutable,
readback-verified, independently corroborated verification-evidence receipt.
Never fetches unlicensed raw scores, updates result rows, or deletes SQL.
"""
from __future__ import annotations

import argparse
from datetime import date, datetime, timezone
import json

from sqlalchemy import select

from app.models import ActiveMatch, ArchiveJob, MatchResult, SystemSetting
from app.storage import lifecycle as lc
from app.storage.archive import SQLArchiveTracker, _verify_object, archive_partition


class BatchArchiveBlocked(ValueError):
    pass


def _receipt_for(session, store, match, result, day):
    """Find exactly one matching, verified and independently corroborated proof."""
    prefix = (f"verification-evidence/season=2026-2027/league=de.bl1/"
              f"date={day.isoformat()}/part=independent-")
    jobs = session.scalars(select(ArchiveJob).where(
        ArchiveJob.dataset_type == "verification-evidence",
        ArchiveJob.r2_key.like(prefix + "%"),
        ArchiveJob.status == "VERIFIED",
    ).limit(100)).all()
    found = []
    for job in jobs:
        if (job.row_count != 1 or job.file_size_bytes is None or
                not job.checksum or job.verified_at is None):
            continue
        try:
            rows = _verify_object(store, job.r2_key, expected_size=job.file_size_bytes,
                                  expected_rows=1, expected_checksum=job.checksum)
        except Exception:
            # Never accept an unverifiable proof, even if the ledger says VERIFIED.
            raise BatchArchiveBlocked("INDEPENDENT_EVIDENCE_READBACK_FAILED") from None
        if (str(rows[0].get("match_id")) == str(match.id) and
                str(rows[0].get("result_id")) == str(result.id)):
            found.append(rows[0])
    if len(found) != 1:
        raise BatchArchiveBlocked("ONE_INDEPENDENT_EVIDENCE_REQUIRED")
    proof = found[0]
    if (proof.get("decision") != "EXACT_IDENTITIES_AND_FINAL_SCORE_AGREE" or
            proof.get("openligadb_match_id") != match.provider_match_id or
            proof.get("league") != "de.bl1" or proof.get("season") != "2026-2027" or
            proof.get("utc_kickoff") != lc.aware(match.start_time).isoformat() or
            int(proof.get("home_score", -1)) != result.home_score or
            int(proof.get("away_score", -1)) != result.away_score or
            not proof.get("football_data_match_id")):
        raise BatchArchiveBlocked("INDEPENDENT_EVIDENCE_SQL_CONFLICT")
    return True


def archive_one(session, store, match, result, *, now, live):
    if (match.provider != "openligadb" or match.league_id != "de.bl1" or
            match.season != "2026-2027" or match.status != "FINISHED" or
            result.provider != "openligadb" or result.status != "FINAL" or
            result.verified_at is None or lc.aware(result.verified_at) > now or
            any(type(x) is not int or x < 0 for x in (result.home_score, result.away_score))):
        raise BatchArchiveBlocked("EXISTING_FINAL_SQL_NOT_ELIGIBLE")
    day = lc.aware(match.start_time).date()
    _receipt_for(session, store, match, result, day)
    records = [lc.make_record(session, match, row, now=now, hours=32)
               for row in (match, result)]
    for rec in records:
        if set(rec.blockers) - {"WITHIN_RETENTION_WINDOW"}:
            raise BatchArchiveBlocked("LIFECYCLE_POLICY_BLOCKED")
    index_key = lc.source_index_key(match)
    index = session.get(SystemSetting, index_key)
    if index is not None and (index.value.get("match_id") != str(match.id) or
                              index.value.get("provider") != match.provider or
                              index.value.get("provider_match_id") != match.provider_match_id):
        raise BatchArchiveBlocked("SOURCE_IDENTITY_INDEX_CONFLICT")
    if not live:
        return {"status": "DRY_RUN_READY", "match_id": str(match.id), "parts": [
            {"dataset": r.dataset, "key": r.key, "sha256": r.checksum} for r in records]}
    tracker = SQLArchiveTracker(session)
    receipts = []
    for record in records:
        receipt = archive_partition(store, tracker=tracker, dataset=record.dataset,
            season=record.season, league=record.league, day=record.day,
            rows=[record.row], part=record.part, retries=1)
        if lc.verify_record(session, store, record) is not None:
            raise BatchArchiveBlocked("LIFECYCLE_READBACK_FAILED")
        receipts.append({"dataset": record.dataset, "key": receipt.key,
                         "sha256": receipt.checksum, "rows": receipt.row_count})
    if index is None:
        session.add(SystemSetting(key=index_key, value={
            "provider": match.provider, "provider_match_id": match.provider_match_id,
            "match_id": str(match.id), "season": match.season,
            "league_id": match.league_id,
            "first_verified_fixture_key": records[0].key}))
        session.commit()
    from app.api.match_center import detail_view
    detail = detail_view(session, match.id, source="history", now=now, store=store)
    if (detail.get("storage") != "R2" or
            (detail.get("result") or {}).get("home_score") != result.home_score or
            (detail.get("result") or {}).get("away_score") != result.away_score):
        raise BatchArchiveBlocked("HISTORY_READBACK_SCORE_CONFLICT")
    return {"status": "ARCHIVED_AND_HISTORY_READBACK_VERIFIED",
            "match_id": str(match.id), "score": [result.home_score, result.away_score],
            "receipts": receipts}


def run(session, store, *, day: date, limit=10, live=False, approval="", now=None):
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None or day.year not in (2026, 2027):
        raise BatchArchiveBlocked("INVALID_UTC_SCOPE")
    if not 1 <= limit <= 10:
        raise BatchArchiveBlocked("BATCH_LIMIT_EXCEEDED")
    if store is None:
        raise BatchArchiveBlocked("R2_READBACK_REQUIRED_EVEN_FOR_DRY_RUN")
    if live and approval != "ARCHIVE_VERIFIED_BATCH_ONLY":
        raise BatchArchiveBlocked("EXPLICIT_BATCH_APPROVAL_REQUIRED")
    from datetime import timedelta
    start = datetime.combine(day, datetime.min.time(), tzinfo=timezone.utc)
    end = start + timedelta(days=1)
    # Full bounded scope: never silently truncate matched fixtures.
    pairs = session.execute(select(ActiveMatch, MatchResult).join(
        MatchResult, MatchResult.match_id == ActiveMatch.id).where(
        ActiveMatch.provider == "openligadb",
        ActiveMatch.league_id == "de.bl1",
        ActiveMatch.season == "2026-2027",
        ActiveMatch.start_time >= start, ActiveMatch.start_time < end,
        ActiveMatch.status == "FINISHED",
        MatchResult.status == "FINAL",
        MatchResult.verified_at.is_not(None),
    ).order_by(ActiveMatch.start_time, ActiveMatch.id).limit(limit + 1)).all()
    if len(pairs) > limit:
        raise BatchArchiveBlocked("DAY_HAS_MORE_MATCHES_THAN_APPROVED_LIMIT")
    results = []
    for match, result in pairs:
        try:
            results.append(archive_one(session, store, match, result, now=now, live=live))
        except Exception as exc:
            session.rollback()
            if isinstance(exc, BatchArchiveBlocked):
                reason = str(exc)
            else:
                reason = "LIFECYCLE_ARCHIVE_OR_READBACK_FAILED"
            results.append({"match_id": str(match.id), "status": "HELD", "reason": reason})
    return {"status": "PARTIAL" if any(x["status"] == "HELD" for x in results)
            else "SUCCESS", "day": day.isoformat(), "dry_run": not live,
            "processed": len(results),
            "archived": sum(x["status"] == "ARCHIVED_AND_HISTORY_READBACK_VERIFIED" for x in results),
            "held": sum(x["status"] == "HELD" for x in results),
            "results": results, "sql_delete": False, "result_updates": 0}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--day", required=True, type=date.fromisoformat)
    parser.add_argument("--limit", type=int, default=10)
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--approval", default="")
    args = parser.parse_args(argv)
    from app.core.config import get_settings
    cfg = get_settings()
    if (cfg.auto_retention_delete_enabled or cfg.operational_delete_enabled or
            not cfg.database_url_valid or not cfg.r2_configured or
            args.live and args.approval != "ARCHIVE_VERIFIED_BATCH_ONLY"):
        print(json.dumps({"status": "BLOCKED", "reason": "CONFIG_OR_APPROVAL_GATE"}))
        return 2
    try:
        from app.db.session import SessionLocal
        from app.storage.r2 import R2ObjectStore
        if SessionLocal is None:
            raise BatchArchiveBlocked("DATABASE_NOT_CONFIGURED")
        with SessionLocal() as session:
            report = run(session, R2ObjectStore(settings=cfg), day=args.day,
                         limit=args.limit, live=args.live, approval=args.approval)
            if not args.live:
                session.rollback()
    except BatchArchiveBlocked as exc:
        print(json.dumps({"status": "BLOCKED", "reason": str(exc)}))
        return 2
    except Exception:
        print(json.dumps({"status": "FAILED", "reason": "BATCH_EXECUTION_FAILED"}))
        return 1
    print(json.dumps(report, ensure_ascii=True, default=str))
    return 0 if report["status"] == "SUCCESS" else 2


if __name__ == "__main__":
    raise SystemExit(main())
