"""Pure, fail-closed readiness assessment. No database, R2 or deletion access.

This is an advisory report, never a deletion authorization or executor.
A technically clean report cannot consent to a production DELETE.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any


def _utc(value: Any) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return parsed.astimezone(timezone.utc) if parsed.utcoffset() is not None else None
    except ValueError:
        return None


def assess(validation: dict, watchdog: dict, *, now: datetime) -> dict:
    """Fail closed on incomplete, stale or unlinked storage/schedule evidence.

    The output never approves deletion. Callers must not interpret
    technical_ready as permission to enable operational cleanup.
    """
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("AWARE_CLOCK_REQUIRED")
    now = now.astimezone(timezone.utc)
    blockers: set[str] = set()

    if validation.get("status") not in {"NO_ELIGIBLE_RECORDS", "ELIGIBLE_RECORDS_FOUND"}:
        blockers.add("VALIDATION_NOT_COMPLETE")
    plan = validation.get("plan") if isinstance(validation.get("plan"), dict) else {}
    as_of = _utc(plan.get("as_of"))
    if not plan.get("scan_complete") is True:
        blockers.add("INCOMPLETE_SCAN")
    if not as_of or not timedelta(0) <= now - as_of <= timedelta(minutes=10):
        blockers.add("VALIDATION_STALE")
    if validation.get("unknown_dependency_tables") != 0:
        # The inventory exposes this field in plan, not at the top level.
        if plan.get("unknown_dependency_tables") != 0:
            blockers.add("UNKNOWN_DEPENDENCIES")
    if validation.get("storage_preflight", {}).get("status") != "PASS":
        blockers.add("STORAGE_PREFLIGHT_NOT_VERIFIED")
    if validation.get("protected_target", {}).get("status") not in {"VERIFIED", "ARCHIVED_ONLY"}:
        blockers.add("R2_TARGET_NOT_VERIFIED")
    if validation.get("protected_metadata", {}).get("transaction_read_only") != "on":
        blockers.add("READ_ONLY_NOT_VERIFIED")
    if validation.get("protected_metadata", {}).get("same_snapshot_counts_unchanged") is not True:
        blockers.add("METADATA_NOT_STABLE")

    runtime = validation.get("archive_runtime", {})
    if (runtime.get("incomplete_archive_ledgers") != 0 or runtime.get("running_jobs") != []
            or runtime.get("running_jobs_truncated") is not False):
        blockers.add("ARCHIVE_LEDGER_OR_JOB_UNRESOLVED")
    records = plan.get("records")
    if not isinstance(records, list):
        blockers.add("RECORD_EVIDENCE_MISSING")
    elif any(row.get("eligible") is True and
             (row.get("archive_verification") != "PASS" or row.get("source_identity") != "PASS")
             for row in records if isinstance(row, dict)):
        blockers.add("ARCHIVE_READBACK_NOT_VERIFIED")
    if isinstance(records, list) and any(not isinstance(row, dict) for row in records):
        blockers.add("RECORD_EVIDENCE_MISSING")

    if watchdog.get("status") != "PASS" or watchdog.get("state") != "HEALTHY":
        blockers.add("SCHEDULE_NOT_HEALTHY")
    if watchdog.get("storage_evidence", {}).get("status") != "VERIFIED":
        blockers.add("WATCHDOG_STORAGE_NOT_VERIFIED")
    for mode in ("archive", "auto"):
        check = watchdog.get("checks", {}).get(mode, {})
        if (check.get("sql_audit_matches_scheduled_success") is not True
                or check.get("sql_audit_match_kind") != "RUN_AND_ATTEMPT"):
            blockers.add("SCHEDULE_AUDIT_NOT_VERIFIED")
        if check.get("operation", {}).get("outcome") in {None, "FAILED", "NOT_VERIFIED"}:
            blockers.add("SCHEDULE_OPERATION_NOT_VERIFIED")

    for source in (validation, watchdog):
        if any(source.get(flag) != "NOT_RUN" for flag in ("SQL_WRITE", "R2_WRITE", "LIVE_DELETE")):
            blockers.add("READ_ONLY_FLAGS_NOT_VERIFIED")
        if (source.get("AUTO_RETENTION_DELETE_ENABLED") is not False or
                source.get("OPERATIONAL_DELETE_ENABLED") is not False):
            blockers.add("DELETE_GATES_NOT_VERIFIED")

    blockers.add("OPERATOR_APPROVAL_REQUIRED")
    return {
        "status": "SAFE_TO_DELETE_NO",
        "technical_ready": len(blockers) == 1,
        "blockers": sorted(blockers),
        "LIVE_DELETE": "NOT_RUN",
        "note": "Advisory only; never dispatches cleanup or authorizes DELETE.",
    }
