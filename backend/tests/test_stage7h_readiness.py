"""Stage 7H: pure synthetic readiness policy; no production secrets or writes."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone

import pytest

from app.jobs.retention_readiness import assess

NOW = datetime(2026, 11, 22, 12, tzinfo=timezone.utc)


def evidence():
    flags = {"SQL_WRITE": "NOT_RUN", "R2_WRITE": "NOT_RUN", "LIVE_DELETE": "NOT_RUN",
             "AUTO_RETENTION_DELETE_ENABLED": False, "OPERATIONAL_DELETE_ENABLED": False}
    validation = {
        **flags, "status": "ELIGIBLE_RECORDS_FOUND", "storage_preflight": {"status": "PASS"},
        "protected_target": {"status": "VERIFIED"},
        "protected_metadata": {"transaction_read_only": "on", "same_snapshot_counts_unchanged": True},
        "archive_runtime": {"incomplete_archive_ledgers": 0, "running_jobs": [], "running_jobs_truncated": False},
        "plan": {"as_of": NOW.isoformat(), "scan_complete": True, "unknown_dependency_tables": 0,
                 "records": [{"eligible": True, "archive_verification": "PASS", "source_identity": "PASS"}]},
    }
    checks = lambda: {"sql_audit_matches_scheduled_success": True,
                      "sql_audit_match_kind": "RUN_AND_ATTEMPT", "operation": {"outcome": "SKIPPED"}}
    watchdog = {**{k: flags[k] for k in ("SQL_WRITE", "R2_WRITE", "LIVE_DELETE")},
                "SQL_ARCHIVE_WRITE": "NOT_RUN", "status": "PASS", "state": "HEALTHY",
                "observed_at": NOW.isoformat(),
                "storage_evidence": {"status": "VERIFIED"},
                "checks": {"archive": checks(), "auto": checks()}}
    return validation, watchdog


def test_even_perfect_evidence_never_authorizes_delete():
    validation, watchdog = evidence()
    report = assess(validation, watchdog, now=NOW)
    assert report["technical_ready"] is True
    assert report["status"] == "SAFE_TO_DELETE_NO"
    assert report["blockers"] == ["OPERATOR_APPROVAL_REQUIRED"]
    assert report["LIVE_DELETE"] == "NOT_RUN"


@pytest.mark.parametrize("mutation,expected", [
    (lambda v, w: v["plan"].update(scan_complete=False), "INCOMPLETE_SCAN"),
    (lambda v, w: v["plan"].update(as_of=(NOW-timedelta(minutes=11)).isoformat()), "VALIDATION_STALE"),
    (lambda v, w: v["plan"].update(unknown_dependency_tables=1), "UNKNOWN_DEPENDENCIES"),
    (lambda v, w: v["archive_runtime"].update(incomplete_archive_ledgers=1), "ARCHIVE_LEDGER_OR_JOB_UNRESOLVED"),
    (lambda v, w: v["plan"]["records"][0].update(archive_verification="NOT_VERIFIED"), "ARCHIVE_READBACK_NOT_VERIFIED"),
    (lambda v, w: v["protected_metadata"].update(same_snapshot_counts_unchanged=False), "METADATA_NOT_STABLE"),
    (lambda v, w: w.update(state="ARCHIVE_STALE"), "SCHEDULE_NOT_HEALTHY"),
    (lambda v, w: w["checks"]["auto"].update(sql_audit_match_kind="LEGACY_RUN_ID_ONLY"), "SCHEDULE_AUDIT_NOT_VERIFIED"),
    (lambda v, w: v.update(LIVE_DELETE="UNKNOWN"), "READ_ONLY_FLAGS_NOT_VERIFIED"),
    (lambda v, w: v.update(AUTO_RETENTION_DELETE_ENABLED=True), "DELETE_GATES_NOT_VERIFIED"),
])
def test_missing_or_untrusted_evidence_fails_closed(mutation, expected):
    validation, watchdog = evidence()
    mutation(validation, watchdog)
    report = assess(validation, watchdog, now=NOW)
    assert report["status"] == "SAFE_TO_DELETE_NO" and report["technical_ready"] is False
    assert expected in report["blockers"]


def test_empty_or_unavailable_evidence_fails_closed():
    report = assess({}, {}, now=NOW)
    assert report["status"] == "SAFE_TO_DELETE_NO" and report["technical_ready"] is False


def test_naive_time_is_rejected():
    with pytest.raises(ValueError, match="AWARE_CLOCK_REQUIRED"):
        assess(*evidence(), now=NOW.replace(tzinfo=None))


def test_real_watchdog_schema_without_delete_flags_is_accepted_but_never_approves():
    validation, watchdog = evidence()
    assert "OPERATIONAL_DELETE_ENABLED" not in watchdog
    assert "AUTO_RETENTION_DELETE_ENABLED" not in watchdog
    report = assess(validation, watchdog, now=NOW)
    assert report["technical_ready"] is True
    assert report["status"] == "SAFE_TO_DELETE_NO"
    assert report["blockers"] == ["OPERATOR_APPROVAL_REQUIRED"]


@pytest.mark.parametrize("mutation,expected", [
    (lambda v, w: w.update(SQL_ARCHIVE_WRITE="UNKNOWN"), "READ_ONLY_FLAGS_NOT_VERIFIED"),
    (lambda v, w: w.update(SQL_WRITE="UNVERIFIED"), "READ_ONLY_FLAGS_NOT_VERIFIED"),
    (lambda v, w: v["plan"].pop("unknown_dependency_tables"), "UNKNOWN_DEPENDENCIES"),
    (lambda v, w: v["plan"].update(unknown_dependency_tables=True), "UNKNOWN_DEPENDENCIES"),
    (lambda v, w: w.update(status="FAILED"), "SCHEDULE_NOT_HEALTHY"),
])
def test_actual_report_contract_fail_closed_on_missing_or_bad_evidence(mutation,expected):
    validation,watchdog=evidence()
    mutation(validation,watchdog)
    report=assess(validation,watchdog,now=NOW)
    assert report["technical_ready"] is False
    assert expected in report["blockers"]
    assert report["status"]=="SAFE_TO_DELETE_NO"


@pytest.mark.parametrize("bad_time", [
    None,
    (NOW-timedelta(minutes=11)).isoformat(),
    (NOW+timedelta(seconds=1)).isoformat(),
    NOW.replace(tzinfo=None).isoformat(),
    "invalid",
])
def test_old_future_missing_or_naive_healthy_watchdog_cannot_be_replayed(bad_time):
    validation, watchdog = evidence()
    if bad_time is None:
        watchdog.pop("observed_at")
    else:
        watchdog["observed_at"] = bad_time
    report=assess(validation,watchdog,now=NOW)
    assert report["status"]=="SAFE_TO_DELETE_NO"
    assert report["technical_ready"] is False
    assert "WATCHDOG_EVIDENCE_STALE" in report["blockers"]


def test_watchdog_evidence_exact_ten_minute_boundary_is_allowed_but_delete_not_approved():
    validation,watchdog=evidence()
    watchdog["observed_at"]=(NOW-timedelta(minutes=10)).isoformat()
    report=assess(validation,watchdog,now=NOW)
    assert report["technical_ready"] is True
    assert report["status"]=="SAFE_TO_DELETE_NO"
    assert report["blockers"]==["OPERATOR_APPROVAL_REQUIRED"]
