"""Pure Stage 7D.2 interpretation. A workflow success alone is never HEALTHY storage."""
from datetime import timedelta
import hashlib
import json
import re

from app.providers.result_sources import utc


LIMITS = {"archive": (15, 30, 45), "auto": (60, 90, 120)}
FAILED_CONCLUSIONS = {"failure", "cancelled", "timed_out", "action_required", "startup_failure"}


def attempt(row):
    value = row.get("run_attempt", 1)
    return value if type(value) is int and 1 <= value <= 999999 else 0


def activity(row):
    """Reruns keep created_at; attempt start describes execution, not delivery."""
    created = utc(row.get("created_at"))
    started = utc(row.get("run_started_at"))
    return max(t for t in (created, started) if t is not None) if created or started else None


def identified(row):
    return type(row.get("id")) is int and 0 < row["id"] < 10**24 and type(row.get("run_attempt")) is int and attempt(row) > 0


def schedule_checks(workflow, runs, *, now):
    errors, warnings, checks, states = [], [], {}, []
    if workflow.get("state") != "active":
        errors.append("RETENTION_WORKFLOW_NOT_ACTIVE")
    created = utc(workflow.get("created_at"))
    # Repeated API rows/reruns are one run. An older successful attempt cannot
    # hide a newer failed or pending attempt with the same GitHub run ID.
    unique, conflicts = {}, set()
    for index, row in enumerate(runs):
        key = row.get("id", ("unidentified", index))
        previous = unique.get(key)
        if previous and attempt(row) == attempt(previous):
            fields = ("event", "display_title", "created_at", "status", "conclusion")
            if any(row.get(k) != previous.get(k) for k in fields):
                conflicts.add((key, attempt(row)))
        if key not in unique or attempt(row) >= attempt(unique[key]):
            unique[key] = row
    for mode, (interval, delayed, stale) in LIMITS.items():
        rows = [r for r in unique.values() if r.get("event") == "schedule"
                and r.get("display_title") == f"Retention {mode}"]
        conflicting = [r for r in rows if (r.get("id"), attempt(r)) in conflicts]
        if conflicting:
            errors.append(mode.upper() + "_SCHEDULE_OBSERVATION_CONFLICT")
            states.append("INSUFFICIENT_EVIDENCE")
        if any(r.get("conclusion") == "success" and not identified(r) for r in rows):
            errors.append(mode.upper() + "_SCHEDULE_IDENTITY_NOT_VERIFIED")
            states.append("INSUFFICIENT_EVIDENCE")
        ordered = sorted(rows, key=lambda r: utc(r.get("created_at")) or now - timedelta(days=36500), reverse=True)
        latest = max(rows, key=lambda r: activity(r) or now - timedelta(days=36500), default=None)
        delivered = next(iter(ordered), None)
        success = next((r for r in ordered if r.get("conclusion") == "success"
                        and r.get("status", "completed") == "completed" and identified(r)
                        and (r.get("id"), attempt(r)) not in conflicts), None)
        at = utc(success.get("created_at")) if success else None
        age = (now - at).total_seconds() if at else None
        success_started = utc(success.get("run_started_at")) if success else None
        success_updated = utc(success.get("updated_at")) if success else None
        timeline_valid = bool(at and success_started and success_updated and at <= success_started <= success_updated <= now)
        if success and not timeline_valid:
            errors.append(mode.upper() + "_SCHEDULE_TIME_WINDOW_NOT_VERIFIED")
            states.append("INSUFFICIENT_EVIDENCE")
        latest_at = utc(delivered.get("created_at")) if delivered else None
        latest_age = (now - latest_at).total_seconds() if latest_at else None
        # The first attempt's queue wait still consumes the original budget.
        # Only a rerun has a new attempt clock without a new delivery clock.
        pending_at = (utc(latest.get("created_at")) if attempt(latest) == 1 else activity(latest)) if latest else None
        pending_age = (now - pending_at).total_seconds() if pending_at else None
        bootstrap = created and 0 <= (now - created).total_seconds() < stale * 60
        if age is None or age < 0 or age > stale * 60:
            (warnings if bootstrap and not success else errors).append(f"NO_RECENT_SUCCESSFUL_{mode.upper()}_SCHEDULE")
            states.append(mode.upper() + "_STALE" if age is not None and age >= 0 else "INSUFFICIENT_EVIDENCE")
        elif age > delayed * 60:
            states.append("SCHEDULE_DELAYED")
            warnings.append(mode.upper() + "_SCHEDULE_DELAYED")
        if latest and latest.get("conclusion") in FAILED_CONCLUSIONS and (latest.get("id"), attempt(latest)) not in conflicts:
            errors.append(f"LATEST_{mode.upper()}_FAILED_OR_CANCELLED")
            states.append(mode.upper() + "_FAILED")
        pending = bool(latest and latest.get("status") in {"queued", "in_progress", "waiting", "pending", "requested"})
        if pending:
            warnings.append(mode.upper() + "_RUN_PENDING")
            if pending_age is None or pending_age < 0 or (activity(latest) and activity(latest) > now):
                errors.append(mode.upper() + "_PENDING_TIME_WINDOW_NOT_VERIFIED")
                states.append("INSUFFICIENT_EVIDENCE")
            # validate (8m) + retention (12m); queue time cannot buy freshness.
            if pending_age is not None and pending_age > 20 * 60:
                errors.append(mode.upper() + "_RUN_EXCEEDED_BUDGET")
                states.append("INSUFFICIENT_EVIDENCE")
        checks[mode] = {"threshold_minutes": stale, "delay_minutes": delayed, "interval_minutes": interval,
            "last_success_age_seconds": age, "last_success_created_at": at.isoformat() if at else None,
            "last_success_run_id": success.get("id") if success else None,
            "last_success_run_attempt": attempt(success) if success else None,
            "last_success_updated_at": success_updated.isoformat() if success_updated else None,
            "last_success_attempt_started_at": success_started.isoformat() if success_started else None,
            "github_time_window_verified": timeline_valid,
            "latest_run_id": latest.get("id") if latest else None,
            "latest_run_attempt": attempt(latest) if latest else None,
            "last_scheduled_created_at": latest_at.isoformat() if latest_at else None,
            "latest_attempt_started_at": utc(latest.get("run_started_at")).isoformat() if latest and utc(latest.get("run_started_at")) else None,
            "latest_pending_age_seconds": pending_age if pending else None,
            "latest_pending": pending,
            "observed_gap_intervals": max(0, int(latest_age // (interval * 60))) if latest_age is not None else None,
            "delivery_note": "CREATION_TIME_IS_NOT_NOMINAL_CRON_TIME;_MISSING_OR_DELAYED_NOT_PROVEN_DROPPED"}
    return {"errors": errors, "warnings": warnings, "checks": checks, "schedule_states": states}


def operation(audit):
    """Keep invocation/ledger outcomes distinct from readback verification."""
    status, storage = audit.get("status"), audit.get("storage_status")
    if status in {"FAILED", "PARTIAL"} or (audit.get("archive_failures") or 0) > 0:
        outcome = "FAILED"
    elif audit.get("execution_status") == "NOT_EXECUTED":
        outcome = "IDEMPOTENT_DUPLICATE"
    elif storage == "DRY_RUN":
        outcome = "DRY_RUN"
    elif status == "SKIPPED" and storage == "SKIPPED":
        outcome = "SKIPPED"
    elif status == "SUCCESS" and (audit.get("archive_records_written") or 0) > 0:
        outcome = "REPORTED_WRITES"
    elif status == "SUCCESS" and storage == "SUCCESS" and audit.get("archive_records_written") == 0:
        outcome = "SUCCESS"
    else:
        outcome = "NOT_VERIFIED"
    return {"outcome": outcome, "new_r2_data_verified": False,
            "archive_records_written": audit.get("archive_records_written")}


def alert_payload(result):
    """Transport-neutral Slack-ready payload. Never sends messages or includes secrets."""
    def codes(values):
        return sorted({v for v in values if isinstance(v, str) and re.fullmatch(r"[A-Z][A-Z0-9_]{0,100}", v)})
    errors, warnings = codes(result.get("errors", [])), codes(result.get("warnings", []))
    severity = "ERROR" if errors else "WARNING" if warnings or result.get("state") != "HEALTHY" else "INFO"
    identities = {mode: {k: result.get("checks", {}).get(mode, {}).get(k)
                        for k in ("latest_run_id", "latest_run_attempt", "last_success_run_id", "last_success_run_attempt")}
                  for mode in LIMITS}
    body = {"schema_version": 1, "severity": severity, "codes": sorted(set(errors + warnings)),
            "state": result.get("state", "INSUFFICIENT_EVIDENCE"), "runs": identities}
    # No observed_at/elapsed seconds: re-observing the same incident is not a new alert.
    body["dedup_key"] = hashlib.sha256(json.dumps(body, sort_keys=True).encode()).hexdigest()
    return body


def extend(result, *, runs, storage, now):
    states = list(result.pop("schedule_states", []))
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
                 and storage.get("storage_preflight", {}).get("r2", {}).get("status") != "FAILED"
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
            check = result["checks"][mode]
            audits = storage.get("sql_scheduler_audit", {})
            history = audits.get("scheduled_runs", {}).get(mode, [])
            wanted_id, wanted_attempt = check["last_success_run_id"], check["last_success_run_attempt"]
            matches = [a for a in history if str(a.get("run_id")) == str(wanted_id)
                       and a.get("run_attempt") == wanted_attempt]
            legacy = audits.get(mode, {})
            audit = matches[-1] if matches else legacy
            linked = wanted_id is not None and str(audit.get("run_id")) == str(wanted_id)
            version = audit.get("schema_version", 1)
            if type(version) is int and version == 2:
                linked = linked and audit.get("event") == "schedule" and type(audit.get("run_attempt")) is int and audit.get("run_attempt") == wanted_attempt
            elif type(version) is int and version == 1 and linked:
                linked = wanted_attempt == 1
                result["warnings"].append(mode.upper() + "_LEGACY_AUDIT_RUN_ID_ONLY")
            else:
                linked = False
            identity_fields = ("schema_version", "event", "run_id", "run_attempt", "started_at", "completed_at", "status",
                               "storage_status", "execution_status", "archive_records_written", "archive_failures")
            signatures = {json.dumps({k: row.get(k) for k in identity_fields}, sort_keys=True) for row in matches}
            if len(signatures) > 1:
                linked = False
                result["errors"].append(mode.upper() + "_AUDIT_OBSERVATION_CONFLICT")
            check["sql_audit_matches_scheduled_success"] = linked
            check["sql_audit_match_kind"] = "RUN_AND_ATTEMPT" if linked and audit.get("schema_version") == 2 else "LEGACY_RUN_ID_ONLY" if linked else "NOT_VERIFIED"
            check["operation"] = operation(audit) if linked else {"outcome": "NOT_VERIFIED"}
            if linked and audit.get("schema_version") == 2 and operation(audit)["outcome"] == "NOT_VERIFIED":
                states.append("INSUFFICIENT_EVIDENCE")
                result["errors"].append(mode.upper() + "_OPERATION_EVIDENCE_NOT_VERIFIED")
            if not linked:
                states.append("INSUFFICIENT_EVIDENCE")
                result["errors"].append(mode.upper() + "_AUDIT_SCHEDULE_LINK_NOT_VERIFIED")
            at_audit = utc(audit.get("completed_at"))
            if linked and version == 2:
                started = utc(audit.get("started_at"))
                created = utc(check["last_success_created_at"])
                finished = utc(check["last_success_updated_at"])
                attempt_started = utc(check["last_success_attempt_started_at"])
                times_valid = bool(check["github_time_window_verified"] and created and attempt_started and started and at_audit and finished
                                   and created <= attempt_started <= started <= at_audit <= finished <= now)
                check["sql_audit_time_window_verified"] = times_valid
                if not times_valid:
                    states.append("INSUFFICIENT_EVIDENCE")
                    result["errors"].append(mode.upper() + "_AUDIT_TIME_WINDOW_NOT_VERIFIED")
            else:
                check["sql_audit_time_window_verified"] = False
            if linked and (audit.get("status") in {"FAILED", "PARTIAL"} or (audit.get("archive_failures") or 0) > 0):
                states.append(mode.upper() + "_FAILED")
                result["errors"].append(mode.upper() + "_SQL_AUDIT_FAILED")
            if linked and audit.get("status") not in {"SUCCESS", "SKIPPED", "DRY_RUN", "FAILED", "PARTIAL"}:
                states.append("INSUFFICIENT_EVIDENCE")
                result["errors"].append(mode.upper() + "_SQL_AUDIT_STATUS_NOT_VERIFIED")
            if linked and (at_audit is None or at_audit > now):
                states.append("INSUFFICIENT_EVIDENCE")
                result["errors"].append(mode.upper() + "_SQL_AUDIT_MISSING_OR_INVALID")
            elif linked and at_audit and (now - at_audit).total_seconds() > limit * 60:
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
        observed_at=now.isoformat(), LIVE_DELETE="NOT_RUN", SQL_ARCHIVE_WRITE="NOT_RUN",
        SQL_WRITE="NOT_RUN", R2_WRITE="NOT_RUN", SAFE_TO_DELETE="NO")
    result["errors"] = sorted(set(result["errors"]))
    result["warnings"] = sorted(set(result["warnings"]))
    result["status"] = "FAILED" if result["errors"] else "PASS"
    return result
