"""Pure Stage 7D.2 interpretation. A workflow success alone is never HEALTHY storage."""
from datetime import timedelta

from app.providers.result_sources import utc


def extend(result, *, runs, storage, now):
    states = []
    for mode, stale, delayed in (("archive", 45, 30), ("auto", 120, 90)):
        rows = [r for r in runs if r.get("event") == "schedule" and r.get("display_title") == f"Retention {mode}"]
        latest = max(rows, key=lambda r: r.get("created_at", ""), default=None)
        check = result["checks"][mode]
        age = check["last_success_age_seconds"]
        check["last_success_created_at"] = (now - timedelta(seconds=age)).isoformat() if age is not None else None
        latest_at = utc(latest.get("created_at")) if latest else None
        check["last_scheduled_created_at"] = latest_at.isoformat() if latest_at else None
        if latest and latest.get("conclusion") in {"failure", "cancelled", "timed_out", "action_required", "startup_failure"}:
            states.append(mode.upper() + "_FAILED")
        if age is None or age < 0:
            states.append("INSUFFICIENT_EVIDENCE")
        elif age > stale * 60:
            states.append(mode.upper() + "_STALE")
        elif age > delayed * 60:
            states.append("SCHEDULE_DELAYED")
            result["warnings"].append(mode.upper() + "_SCHEDULE_DELAYED")
        check["last_success_run_id"] = next((r.get("id") for r in sorted(rows,
            key=lambda r: r.get("created_at", ""), reverse=True) if r.get("conclusion") == "success"), None)
    evidence = {"status": "NOT_VERIFIED", "last_real_readback_at": None}
    if storage is None:
        states.append("INSUFFICIENT_EVIDENCE")
    else:
        plan = storage.get("plan", {})
        at = utc(plan.get("as_of"))
        fresh = at is not None and timedelta(0) <= now - at <= timedelta(minutes=10)
        runtime = storage.get("archive_runtime", {})
        valid = (storage.get("status") in {"NO_ELIGIBLE_RECORDS", "ELIGIBLE_RECORDS_FOUND"}
                 and plan.get("scan_complete") is True and fresh
                 and storage.get("storage_preflight", {}).get("status") == "PASS"
                 and storage.get("protected_target", {}).get("status") in {"VERIFIED", "ARCHIVED_ONLY"}
                 and storage.get("protected_metadata", {}).get("transaction_read_only") == "on"
                 and storage.get("protected_metadata", {}).get("same_snapshot_counts_unchanged") is True
                 and storage.get("capacity", {}).get("status") in {"HEALTHY", "WARNING", "HIGH", "CRITICAL"}
                 and all(k in runtime for k in ("incomplete_archive_ledgers", "running_jobs", "running_jobs_truncated"))
                 and all(storage.get(k) == "NOT_RUN" for k in ("SQL_WRITE", "R2_WRITE", "LIVE_DELETE"))
                 and storage.get("AUTO_RETENTION_DELETE_ENABLED") is False
                 and storage.get("OPERATIONAL_DELETE_ENABLED") is False)
        partial = (runtime.get("incomplete_archive_ledgers", 0) > 0 or runtime.get("running_jobs_truncated")
                   or any(j.get("state") == "UNKNOWN_RECONCILE_REQUIRED" for j in runtime.get("running_jobs", [])))
        records = plan.get("records", [])
        archives = all(r.get("archive_verification") == "PASS" and r.get("source_identity") == "PASS" for r in records) and (
            bool(records) or storage.get("protected_target", {}).get("status") == "ARCHIVED_ONLY")
        if not valid or not archives:
            states.append("INSUFFICIENT_EVIDENCE")
            result["errors"].append("STORAGE_EVIDENCE_NOT_VERIFIED")
        if partial or records and not archives:
            states.append("STORAGE_RECONCILE_REQUIRED")
            result["errors"].append("STORAGE_RECONCILE_REQUIRED")
        if storage.get("storage_preflight", {}).get("r2", {}).get("status") == "FAILED":
            states.append("STORAGE_RECONCILE_REQUIRED")
            result["errors"].append("R2_VERIFICATION_FAILED")
        for mode, limit in (("archive", 45), ("auto", 120)):
            audit = storage.get("sql_scheduler_audit", {}).get(mode, {})
            linked = str(audit.get("run_id")) == str(result["checks"][mode]["last_success_run_id"]) and audit.get("run_id") is not None
            result["checks"][mode]["sql_audit_matches_scheduled_success"] = linked
            if not linked:
                states.append("INSUFFICIENT_EVIDENCE")
                result["errors"].append(mode.upper() + "_AUDIT_SCHEDULE_LINK_NOT_VERIFIED")
            at_audit = utc(audit.get("completed_at"))
            if audit.get("status") in {"FAILED", "PARTIAL"}:
                states.append(mode.upper() + "_FAILED")
                result["errors"].append(mode.upper() + "_SQL_AUDIT_FAILED")
            if at_audit is None or at_audit > now:
                states.append("INSUFFICIENT_EVIDENCE")
                result["errors"].append(mode.upper() + "_SQL_AUDIT_MISSING_OR_INVALID")
            elif (now - at_audit).total_seconds() > limit * 60:
                states.append(mode.upper() + "_STALE")
                result["errors"].append(mode.upper() + "_SQL_AUDIT_STALE")
        capacity = storage.get("capacity", {}).get("status")
        if capacity in {"WARNING", "HIGH", "CRITICAL"}:
            result["warnings"].append("POSTGRES_CAPACITY_" + capacity)
        if capacity == "CRITICAL":
            states.append("STORAGE_RECONCILE_REQUIRED")
            result["errors"].append("CAPACITY_CRITICAL_SAFE_WRITE_PAUSE_REVIEW_REQUIRED")
        evidence = {"status": "VERIFIED" if valid and archives and not partial else "NOT_VERIFIED",
            "last_real_readback_at": plan.get("as_of") if valid and archives else None,
            "last_sql_ledger_verified_at": runtime.get("last_sql_ledger_verified_at"),
            "capacity_status": capacity if capacity in {"HEALTHY", "WARNING", "HIGH", "CRITICAL"} else "NOT_VERIFIED",
            "incomplete_archive_ledgers": runtime.get("incomplete_archive_ledgers"),
            "running_jobs": len(runtime.get("running_jobs", [])),
            "retention_status": plan.get("status") if plan.get("status") in {"NO_ELIGIBLE_RECORDS", "ELIGIBLE_RECORDS_FOUND", "INCOMPLETE_SCAN"} else "NOT_VERIFIED"}
    if result["errors"] and not states:
        states.append("INSUFFICIENT_EVIDENCE")
    priority = ["STORAGE_RECONCILE_REQUIRED", "ARCHIVE_FAILED", "AUTO_FAILED", "ARCHIVE_STALE", "AUTO_STALE",
                "INSUFFICIENT_EVIDENCE", "SCHEDULE_DELAYED"]
    result.update(state=next((s for s in priority if s in states), "HEALTHY"),
        states=[s for s in priority if s in states] or ["HEALTHY"], storage_evidence=evidence,
        observed_at=now.isoformat(), LIVE_DELETE="NOT_RUN", SQL_ARCHIVE_WRITE="NOT_RUN")
    result["errors"] = sorted(set(result["errors"]))
    result["warnings"] = sorted(set(result["warnings"]))
    result["status"] = "FAILED" if result["errors"] else "PASS"
    return result
