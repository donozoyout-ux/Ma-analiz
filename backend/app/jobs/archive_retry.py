"""Manual single-match archive retry. Default SELECT/HEAD/GET only; never DELETE."""
import argparse
from contextlib import contextmanager, closing
from datetime import datetime, timezone
import hashlib
import json
from uuid import UUID

from sqlalchemy import event, select

from app.jobs import import_preflight, retention, retention_validation
from app.jobs.ledger import SQLLedger, run_idempotent_job
from app.models import ArchiveJob, JobRun, SystemSetting, MatchResult, ActiveStatistic
from app.providers.multileague import digest
from app.storage import lifecycle as lc
from app.storage.archive import _verify_object


class RetryBlocked(RuntimeError):
    pass


def flags():
    return {"LIVE_DELETE": "NOT_RUN", "SQL_ARCHIVE_WRITE": "NOT_RUN", "R2_OVERWRITE": "NOT_RUN",
            "R2_DELETE": "NOT_RUN", "AUTO_RETENTION_DELETE_ENABLED": False, "OPERATIONAL_DELETE_ENABLED": False}


def retry_plan(session, store, *, match_id, now, stale_seconds=3600, ignore_job=None):
    match_id = UUID(str(match_id))
    if match_id.int == 0:
        raise RetryBlocked("EXPLICIT_EXISTING_MATCH_REQUIRED")
    options = {"limit": 1, "after": str(UUID(int=match_id.int - 1)), "child_limit": 500, "hours": 48}
    bundles, _, _ = lc.load_bundles(session, **{k:v for k,v in options.items() if k != "hours"})
    if len(bundles) != 1 or bundles[0][0].id != match_id:
        raise RetryBlocked("EXPLICIT_EXISTING_MATCH_REQUIRED")
    match, entities, bundle_errors = bundles[0]
    blockers = list(bundle_errors)
    if lc.unknown_dependencies(session): blockers.append("UNKNOWN_FOREIGN_KEY_DEPENDENCY")
    index = session.get(SystemSetting, lc.source_index_key(match))
    expected_index = {"provider": match.provider, "provider_match_id": match.provider_match_id,
        "match_id": str(match.id), "season": match.season, "league_id": match.league_id}
    if not index or any(index.value.get(k) != v for k,v in expected_index.items()):
        blockers.append("SOURCE_IDENTITY_NOT_VERIFIED")
    running = session.scalars(select(JobRun).where(JobRun.job_type.in_(["archive_data", "cleanup_database", "archive_retry"]),
        JobRun.status == "RUNNING").limit(26)).all()
    if len(running) > 25: blockers.append("RUNNING_JOB_SCAN_LIMIT")
    uncertain = 0
    for job in running:
        if job.idempotency_key == ignore_job: continue
        age = (now - lc.aware(job.started_at)).total_seconds()
        if 0 <= age < stale_seconds: blockers.append("RETENTION_JOB_IN_PROGRESS")
        else: uncertain += 1  # Never infer rollback from a job timeout. Reconcile exact SQL/R2 below.
    records, evidence = [], []
    result = next((e for e in entities if isinstance(e, MatchResult)), None)
    for entity in entities:
        record = lc.make_record(session, match, entity, now=now, hours=48)
        records.append(record)
        blockers += [r for r in record.blockers if r in {"REUSE_PERMISSION_NOT_CONFIRMED", "RESULT_NOT_VERIFIED", "PARTITION_IDENTITY_MISSING"}]
        if isinstance(entity, ActiveStatistic) and (
                not lc.technical_valid(entity, result, now, match) or entity.licence != record.row["licence"]
                or entity.permission_reference != record.row["permission_reference"]):
            blockers.append("TECHNICAL_PERMISSION_OR_VALIDATION_FAILED")
        ledger = session.scalar(select(ArchiveJob).where(ArchiveJob.r2_key == record.key))
        if ledger and (ledger.checksum not in {None, record.checksum} or ledger.row_count not in {None, 1}
                       or ledger.file_size_bytes not in {None, record.size}
                       or ledger.status == "VERIFIED" and (not ledger.verified_at or ledger.checksum != record.checksum
                           or ledger.row_count != 1 or ledger.file_size_bytes != record.size)):
            blockers.append("ARCHIVE_LEDGER_RECEIPT_RECONCILE_REQUIRED")
        info = store.head_object(record.key)
        state = "MISSING"
        if info:
            try:
                rows = _verify_object(store, record.key, expected_size=record.size, expected_rows=1, expected_checksum=record.checksum)
                if rows != [record.row]: raise ValueError()
                state = "EXACT_MATCH"
            except Exception:
                blockers.append("IMMUTABLE_OBJECT_CONFLICT_RECONCILE_REQUIRED")
                state = "CONFLICT"
        if ledger and ledger.status == "DELETED": blockers.append("ARCHIVE_LEDGER_DELETED_RECONCILE_REQUIRED")
        evidence.append({"record": lc.reference(entity), "key": record.key, "sha256": record.checksum,
            "object": state, "ledger": {"status": ledger.status, "checksum": ledger.checksum,
                "rows": ledger.row_count, "size": ledger.file_size_bytes} if ledger else None})
    body = {"version": 1, "match_id": str(match_id), "records": evidence}
    approval = digest(body)
    journal = session.get(SystemSetting, "archive-retry:" + approval)
    if journal and (journal.value.get("match_id") != str(match_id) or journal.value.get("plan_digest") != approval
                    or journal.value.get("scope") != "ARCHIVE_ONLY_NEVER_DELETE"):
        blockers.append("ARCHIVE_RETRY_JOURNAL_CONFLICT_RECONCILE_REQUIRED")
    return {"status": "BLOCKED" if blockers else "READY_FOR_MANUAL_ARCHIVE_RETRY", "as_of": now.isoformat(),
        "approval_digest": approval, **body, "blockers": sorted(set(blockers)),
        "stale_jobs_not_assumed_rolled_back": uncertain, "options": options, **flags()}, records


class AppendOnlyRetryStore:
    def __init__(self, root, records):
        self.root = root
        self.allowed = {r.key: r.checksum for r in records}
    def head_object(self, key): return self.root.head_object(key)
    def get_object(self, key): return self.root.get_object(key)
    def put_object_if_absent(self, key, body, **kwargs):
        if self.allowed.get(key) != hashlib.sha256(body).hexdigest():
            raise RetryBlocked("ARCHIVE_RETRY_SNAPSHOT_CHANGED")
        return self.root.put_object_if_absent(key, body, **kwargs)
    def put_object(self, *args, **kwargs): raise RetryBlocked("R2_OVERWRITE_FORBIDDEN")
    def delete_object(self, *args, **kwargs): raise RetryBlocked("R2_DELETE_FORBIDDEN")


@contextmanager
def no_delete_sql(session):
    def guard(conn, cursor, statement, parameters, context, many):
        sql = statement.lstrip().upper()
        compiled = getattr(context, "compiled", None)
        if getattr(context, "isinsert", False) or getattr(context, "isupdate", False):
            table = getattr(getattr(compiled, "statement", None), "table", None)
            if table is not None and table.name in {"archive_jobs", "job_runs", "system_settings"}:
                return
        elif sql.startswith("SELECT") and ";" not in statement:
            return
        elif conn.dialect.name == "sqlite" and sql.startswith("PRAGMA") and "=" not in sql:
            return  # SQLite reflection in isolated tests only.
        raise RetryBlocked("ARCHIVE_RETRY_DESTRUCTIVE_SQL_FORBIDDEN")
    bind = session.get_bind()
    event.listen(bind, "before_cursor_execute", guard)
    try: yield
    finally: event.remove(bind, "before_cursor_execute", guard)


def retry_in_session(session, store, *, match_id, approval, reason, now, stale_seconds=3600):
    """Production caller MUST own the Stage 5 connection-level global lock across commits."""
    plan, records = retry_plan(session, store, match_id=match_id, now=now, stale_seconds=stale_seconds)
    if plan["blockers"] or approval != plan["approval_digest"]:
        raise RetryBlocked("CURRENT_SAFE_ARCHIVE_PLAN_REQUIRED")
    if not isinstance(reason, str) or not 1 <= len(reason.strip()) <= 240:
        raise RetryBlocked("MANUAL_RETRY_REASON_REQUIRED")
    key = "archive_retry:" + approval
    protected = import_preflight.protected_snapshot(session)
    started = False
    try:
        with no_delete_sql(session):
            def handler():
                nonlocal started
                session.expire_all()
                checked, _ = retry_plan(session, store, match_id=match_id, now=now,
                    stale_seconds=stale_seconds, ignore_job=key)
                if checked["blockers"] or checked["approval_digest"] != approval:
                    raise RetryBlocked("ARCHIVE_RETRY_SNAPSHOT_CHANGED")
                # The ledger claim may already be durable. Never claim a global rollback.
                started = True
                journal_key = "archive-retry:" + approval
                existing = session.get(SystemSetting, journal_key)
                if not existing:
                    session.add(SystemSetting(key=journal_key, value={"match_id": str(match_id),
                        "plan_digest": approval, "reason": reason, "requested_at": now.isoformat(),
                        "scope": "ARCHIVE_ONLY_NEVER_DELETE"}))
                outcome = lc.archive_early(session, AppendOnlyRetryStore(store, records), now=now, **plan["options"])
                session.expire_all()
                readback, _ = retry_plan(session, store, match_id=match_id, now=now,
                    stale_seconds=stale_seconds, ignore_job=key)
                expected = {(r.key, r.checksum) for r in records}
                actual = {(r["key"], r["sha256"]) for r in readback["records"]}
                if not outcome["records_failed"] and (readback["blockers"] or expected != actual or not all(
                        r["object"] == "EXACT_MATCH" and r["ledger"] and r["ledger"]["status"] == "VERIFIED"
                        for r in readback["records"])):
                    raise RetryBlocked("ARCHIVE_RETRY_SQL_R2_READBACK_CHANGED")
                if import_preflight.protected_snapshot(session) != protected:
                    raise RetryBlocked("PROTECTED_VERIFIED_IMPORT_CHANGED")
                return {"status": outcome["status"], "records_seen": outcome["records_seen"],
                    "records_written": outcome["records_written"], "records_failed": outcome["records_failed"],
                    "reason": "MANUAL_ARCHIVE_RETRY_COMPLETED" if not outcome["records_failed"] else "UNKNOWN_RECONCILE_REQUIRED"}
            result = run_idempotent_job(SQLLedger(session), key, "archive_retry", handler)
        uncertain = result["status"] in {"FAILED", "PARTIAL", "RUNNING"}
        return {**flags(), "status": "UNKNOWN_RECONCILE_REQUIRED" if uncertain else result["status"],
            "reason": "UNKNOWN_RECONCILE_REQUIRED" if uncertain else result.get("reason"),
            "records_written": result.get("records_written", 0), "SQL_ARCHIVE_WRITE": "UNKNOWN_RECONCILE_REQUIRED" if uncertain else "VERIFIED",
            "approval_digest": approval}
    except (Exception, KeyboardInterrupt):
        session.rollback()
        if started:
            return {**flags(), "status": "UNKNOWN_RECONCILE_REQUIRED", "SQL_ARCHIVE_WRITE": "UNKNOWN_RECONCILE_REQUIRED"}
        raise


def run(*, match_id, execute=False, approval=None, reason=None, settings=None):
    from app.core.config import get_settings
    cfg = settings or get_settings()
    output = {"status": "NOT_VERIFIED", **flags()}
    # Always new, real read-only preflight before any writable connection.
    checked = retention_validation.run(cfg)
    output["read_only_preflight_status"] = checked["status"]
    if checked["status"] not in {"NO_ELIGIBLE_RECORDS", "ELIGIBLE_RECORDS_FOUND"}: return output
    if checked["capacity"]["status"] == "CRITICAL":
        return {**output, "status": "BLOCKED", "error_code": "CAPACITY_CRITICAL_SAFE_WRITE_PAUSE_REQUIRED"}
    if execute and (not approval or not reason):
        raise RetryBlocked("EXPLICIT_EXECUTE_APPROVAL_AND_REASON_REQUIRED")
    with retention.storage_session(cfg, readonly=not execute) as session:
        store = import_preflight.bounded_store(cfg)
        with closing(store.client):
            now = datetime.now(timezone.utc)
            if not execute:
                return retry_plan(session, store, match_id=match_id, now=now,
                    stale_seconds=cfg.job_run_stale_after_seconds)[0]
            return retry_in_session(session, store, match_id=match_id, approval=approval, reason=reason,
                now=now, stale_seconds=cfg.job_run_stale_after_seconds)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--match-id", required=True, type=UUID)
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--approve-plan")
    parser.add_argument("--reason")
    args = parser.parse_args(argv)
    try:
        output = run(match_id=args.match_id, execute=args.execute, approval=args.approve_plan, reason=args.reason)
    except RetryBlocked as exc:
        output = {"status": "BLOCKED", "error_code": str(exc), **flags()}
    except Exception:
        output = {"status": "UNKNOWN_RECONCILE_REQUIRED" if args.execute else "NOT_VERIFIED",
            "error_code": "ARCHIVE_RETRY_FAILED_SAFE_RECONCILIATION_REQUIRED", **flags()}
        if args.execute: output["SQL_ARCHIVE_WRITE"] = "UNKNOWN_RECONCILE_REQUIRED"
    print(json.dumps(output, indent=2))
    return 0 if output["status"] in {"READY_FOR_MANUAL_ARCHIVE_RETRY", "SUCCESS", "SKIPPED"} else 1


if __name__ == "__main__": raise SystemExit(main())
