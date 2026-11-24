"""Stage 7C: read-only preparation CLI; persistence is exclusively an offline exercise."""
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
from uuid import UUID

from app.analysis.import_review import plan_report
from app.providers.multileague import digest


def _finish_offline_archive(session, store, source_key, *, now):
    from app.models import ActiveMatch, SystemSetting
    from app.storage import lifecycle as lc
    from app.storage.archive import SQLArchiveTracker, archive_partition
    from app.storage.import_review import ReviewBlocked, immutable_setting, offline_only
    offline_only(session, store)
    journal = session.get(SystemSetting, "controlled-import:" + source_key)
    if not journal:
        raise ReviewBlocked("IMPORT_JOURNAL_REQUIRED_NO_R2_ONLY_RESTORE")
    match = session.get(ActiveMatch, UUID(journal.value["match_id"]))
    if not match or lc.sql_snapshot(match) != journal.value["sql_snapshot"]:
        raise ReviewBlocked("JOURNALED_SQL_OR_IDENTITY_CHANGED")
    review = session.get(SystemSetting, "review-audit:" + journal.value["review_id"])
    evidence = session.get(SystemSetting, "review-evidence:" + journal.value["evidence_sha256"])
    audit = session.get(SystemSetting, "import-audit:" + digest([source_key, "SQL_COMMITTED"]))
    index = session.get(SystemSetting, lc.source_index_key(match))
    if (not review or digest(review.value) != journal.value["review_id"] or review.value["state"] != "APPROVED_FIXTURE"
            or not evidence or digest(evidence.value["evidence"]) != journal.value["evidence_sha256"]
            or evidence.value["evidence"]["identity"] != journal.value["identity"]
            or review.value["source_version"] != evidence.value["evidence"]["source_version"]
            or not audit or audit.value["match_id"] != str(match.id)
            or audit.value["review_id"] != journal.value["review_id"]
            or not index or index.value["match_id"] != str(match.id)):
        raise ReviewBlocked("DURABLE_IMPORT_PROOF_CHANGED")
    record = lc.make_record(session, match, match, now=now, hours=48)
    from app.storage.import_permission import ImmutableImportStore
    if isinstance(store, ImmutableImportStore):
        store.keys = {record.key}
    if set(record.blockers) & {"REUSE_PERMISSION_NOT_CONFIRMED", "PARTITION_IDENTITY_MISSING"}:
        raise ReviewBlocked("ARCHIVE_PERMISSION_OR_PARTITION_BLOCKED")
    # Exactly one row: use Stage 5 format/verifier and Stage 3's immutable archive API.
    receipt = archive_partition(store, dataset=record.dataset, season=record.season, league=record.league,
        day=record.day, rows=[record.row], part=record.part, tracker=SQLArchiveTracker(session), retries=1)
    if lc.verify_record(session, store, record):
        raise ReviewBlocked("ARCHIVE_SQL_CONTENT_READBACK_FAILED")
    receipts = [{"key": receipt.key, "sha256": receipt.checksum, "row_count": receipt.row_count,
                 "size_bytes": receipt.size, "content": "PASS", "sql_snapshot": "PASS"}]
    journal = session.get(SystemSetting, "controlled-import:" + source_key)
    index = session.get(SystemSetting, lc.source_index_key(match))
    journal.value = {**journal.value, "phase": "ARCHIVE_VERIFIED", "receipts": receipts,
                     "archive_verified_at": journal.value.get("archive_verified_at", now.isoformat())}
    if not index.value.get("first_verified_fixture_key"):
        index.value = {**index.value, "first_verified_fixture_key": record.key}
    immutable_setting(session, "import-audit:" + digest([source_key, "ARCHIVE_VERIFIED"]),
        {"phase": "ARCHIVE_VERIFIED", "match_id": str(match.id), "review_id": journal.value["review_id"],
         "receipts": receipts, "at": journal.value["archive_verified_at"]})
    session.commit()
    session.expire_all()
    journal = session.get(SystemSetting, "controlled-import:" + source_key)
    match = session.get(ActiveMatch, UUID(journal.value["match_id"]))
    if (journal.value["phase"] != "ARCHIVE_VERIFIED" or journal.value["receipts"] != receipts
            or lc.sql_snapshot(match) != journal.value["sql_snapshot"] or lc.verify_record(session, store, record)):
        raise ReviewBlocked("FINAL_SQL_R2_READBACK_FAILED")
    production = session.get_bind().dialect.name == "postgresql"
    return {"status": "VERIFIED" if production else "OFFLINE_VERIFIED", "match_id": str(match.id), "receipts": receipts,
            "LIVE_DELETE": "NOT_RUN", "production_writes": "VERIFIED" if production else "NOT_RUN"}


def exercise_offline_import(session, store, *, revalidate, source_key, review_id, now):
    """Test-only adapters enforced before callback, SQL, R2 or settings access."""
    from app.storage.import_review import ReviewBlocked, ReviewRepository, offline_import_lock, prepare_fixture_sql
    with offline_import_lock(session, store):
        try:
            report = revalidate()
            plan = plan_report(report, now=now)
            candidates = [c for c in plan["candidates"] if c["source_key"] == source_key]
            if len(candidates) != 1:
                raise ReviewBlocked("EXACT_SINGLE_CANDIDATE_REQUIRED")
            candidate = candidates[0]
            repo = ReviewRepository(session)
            repo.record(candidate, now=now)
            repo.approved(candidate, review_id, now=now)
            journal, match, created = prepare_fixture_sql(session, candidate, review_id=review_id, now=now)
            result = _finish_offline_archive(session, store, source_key, now=now)
            return {**result, "created": created}
        except ReviewBlocked:
            session.rollback()
            raise
        except Exception:
            session.rollback()
            return {"status": "OFFLINE_FAILED_RETRYABLE", "error_code": "SQL_R2_ACK_OR_READBACK_FAILURE",
                    "SQL_STATE": "UNKNOWN_RECONCILE_REQUIRED", "R2_STATE": "UNKNOWN_REVERIFY_REQUIRED",
                    "LIVE_DELETE": "NOT_RUN", "production_writes": "NOT_RUN"}


def recover_offline(session, store, *, source_key, now):
    """Repair an already committed reviewed snapshot; never fetch/insert a new match."""
    from app.storage.import_review import ReviewBlocked, offline_import_lock
    with offline_import_lock(session, store):
        try:
            return _finish_offline_archive(session, store, source_key, now=now)
        except ReviewBlocked:
            session.rollback()
            raise
        except Exception:
            session.rollback()
            return {"status": "OFFLINE_FAILED_RETRYABLE", "error_code": "ARCHIVE_RECOVERY_READBACK_FAILED",
                    "LIVE_DELETE": "NOT_RUN", "production_writes": "NOT_RUN"}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report", type=Path, help="Recorded Stage 7B JSON; read-only inspection")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true")
    mode.add_argument("--persist", action="store_true", help="Blocked in Stage 7C, reserved for a later reviewed pilot")
    args = parser.parse_args(argv)
    output = {"stage": "7C", "SQL_WRITE": "NOT_RUN", "R2_WRITE": "NOT_RUN", "LIVE_DELETE": "NOT_RUN",
              "AUTO_RETENTION_DELETE_ENABLED": False, "production_ready": False}
    if args.persist:
        output.update(status="BLOCKED", error_code="STAGE7C_PRODUCTION_PERSIST_DISABLED")
    else:
        try:
            if args.report is None or args.report.stat().st_size > 2_000_000:
                raise ValueError()
            report = json.loads(args.report.read_text(encoding="utf-8-sig"))
            output.update(plan_report(report, now=datetime.now(timezone.utc)), status="DRY_RUN_COMPLETE")
        except Exception:
            output.update(status="BLOCKED", error_code="SOURCE_REPORT_INVALID_OR_UNREADABLE")
    print(json.dumps(output, ensure_ascii=True, indent=2))
    return 0 if output["status"] == "DRY_RUN_COMPLETE" else 1


if __name__ == "__main__":
    raise SystemExit(main())
