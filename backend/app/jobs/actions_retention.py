"""One GitHub-hosted retention invocation; no Render worker or secret logging."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
from contextlib import contextmanager

REQUIRED = ("DATABASE_URL", "R2_ENDPOINT", "R2_ACCESS_KEY_ID", "R2_SECRET_ACCESS_KEY", "R2_BUCKET")


@contextmanager
def audit_session(cfg):
    """Bound only Actions audit connections; preserve TLS/search_path URL options."""
    from sqlalchemy import create_engine
    from sqlalchemy.orm import Session
    url = cfg.database_url
    if url.startswith("postgresql://"): url = url.replace("postgresql://", "postgresql+psycopg://", 1)
    engine = create_engine(url, hide_parameters=True, pool_pre_ping=True, pool_timeout=10,
                           connect_args={"connect_timeout": 10})
    try:
        with Session(engine, expire_on_commit=False) as session: yield session
    finally:
        engine.dispose()


def safe_code(value, default="UNAVAILABLE"):
    return value if isinstance(value, str) and re.fullmatch(r"[A-Z][A-Z0-9_]{0,100}", value) else default


def number(value):
    return max(0, value) if type(value) is int else None


def public_summary(report):
    """Whitelist fields; never serialize URLs, exception messages, keys or raw rows."""
    from app.jobs.retention_errors import STAGES
    datasets = report.get("datasets", {})
    totals = {field: sum(number(row.get(field)) or 0 for row in datasets.values())
              for field in ("scanned", "eligible", "retained", "overdue_retained")}
    reasons = {}
    for row in report.get("records", []):
        for reason in row.get("reasons", []):
            code = safe_code(reason)
            reasons[code] = reasons.get(code, 0) + 1
    # Aggregate only fixed-format codes and dataset labels, never UUIDs, object
    # keys, provider response bodies, exception strings or source payloads.
    archive_issue_counts = {}
    archive_entries = report.get("entries") if isinstance(report.get("entries"), list) else (
        report.get("archive_repair", {}).get("entries", []))
    for entry in archive_entries[:1000]:
        if not isinstance(entry, dict) or entry.get("status") not in {"FAILED", "HELD"}:
            continue
        dataset = safe_code(str(entry.get("record", "")).split(":", 1)[0].upper(), "UNKNOWN")
        if entry["status"] == "HELD":
            # The retention engine reports stable blocker codes: expose these
            # rather than an unhelpful generic HELD status. Ignore unknown text.
            allowed_blockers = {
                "RESULT_NOT_VERIFIED", "REUSE_PERMISSION_NOT_CONFIRMED",
                "PARTITION_IDENTITY_MISSING", "TECHNICAL_VALIDATION_FAILED",
                "TECHNICAL_PERMISSION_CHANGED", "ARCHIVE_FAILED_RETRY_NEXT_CYCLE",
            }
            codes = [reason for reason in entry.get("reasons", [])
                     if isinstance(reason, str) and reason in allowed_blockers]
            if not codes:
                codes = ["ARCHIVE_HELD"]
        else:
            codes = [safe_code(entry.get("failure_code"), "ARCHIVE_UNKNOWN_FAILURE")]
        for code in codes[:10]:
            label = f"{dataset}_{code}"
            archive_issue_counts[label] = archive_issue_counts.get(label, 0) + 1
    digest = report.get("approval_digest")
    return {"storage_status": safe_code(report.get("status")), "error_code": safe_code(report.get("error_code"), "NONE"),
            "failure_stage": report.get("failure_stage") if report.get("failure_stage") in STAGES else "NONE",
            "LIVE_DELETE": safe_code(report.get("LIVE_DELETE"), "NOT_RUN"), "deleted": number(report.get("deleted")),
            "policy_hours": number(report.get("policy_hours")), **totals, "retention_reasons": reasons,
            "plan_digest": digest if isinstance(digest, str) and re.fullmatch(r"[0-9a-f]{64}", digest) else None,
            "archive_failures": number(report.get("archive_repair", {}).get("records_failed")) or number(report.get("records_failed")) or 0,
            "archive_failure_codes": archive_issue_counts,
            "capacity_bytes": number(report.get("capacity", {}).get("bytes")),
            "capacity_status": safe_code(report.get("capacity", {}).get("status")),
            "auto_delete_enabled": report.get("auto_delete_enabled") is True}


def normalize(report):
    status = report.get("status")
    if status in {"DRY_RUN", "COMPLETED", "SUCCESS", "SKIPPED"}:
        failures = report.get("records_failed", 0) or report.get("archive_repair", {}).get("records_failed", 0)
        overdue = sum(t.get("overdue_retained", 0) for t in report.get("datasets", {}).values())
        # A valid dry-run describes blockers; it does not claim every record is ready.
        partial = status != "DRY_RUN" and (failures or overdue)
        return {**report, "status": "PARTIAL" if partial else "SUCCESS", "storage_status": status,
                "records_written": report.get("deleted", 0) if status == "COMPLETED" else report.get("records_written", 0),
                "records_seen": report.get("records_seen", sum(t.get("scanned", 0) for t in report.get("datasets", {}).values())),
                "records_failed": failures, "reason": "DRY_RUN_COMPLETED" if status == "DRY_RUN" else "RETENTION_CHECK_COMPLETED"}
    return {**report, "status": "FAILED", "storage_status": status, "records_written": 0,
            "records_failed": max(1, report.get("records_failed", 0)), "reason": "RETENTION_STORAGE_NOT_READY"}


def record_observation(previous, mode, summary, *, run_id, started_at, completed_at):
    """Bounded invocation evidence, separate from the Stage 5 slot ledger.

    Manual invocations must not replace schedule evidence. A duplicate records
    this invocation's no-op, never borrows the previous run's write count.
    """
    raw_attempt = os.environ.get("GITHUB_RUN_ATTEMPT", "1")
    run_attempt = int(raw_attempt) if re.fullmatch(r"[1-9][0-9]{0,5}", raw_attempt) else None
    raw_event = os.environ.get("GITHUB_EVENT_NAME", "local")
    event = raw_event if raw_event in {"schedule", "workflow_dispatch", "push", "local"} else "unknown"
    entry = {"schema_version": 2, "event": event, "run_id": run_id, "run_attempt": run_attempt,
        "started_at": started_at.isoformat(), "completed_at": completed_at.isoformat(),
        **{k: summary.get(k) for k in ("status", "storage_status", "execution_status", "archive_records_written",
            "archive_failures", "LIVE_DELETE", "deleted", "plan_digest", "slot")}}
    value = {**previous, mode: entry}
    if event == "schedule" and mode in {"archive", "auto"}:
        scheduled = dict(previous.get("scheduled_runs", {}))
        rows = [r for r in scheduled.get(mode, []) if (r.get("run_id"), r.get("run_attempt")) != (run_id, run_attempt)]
        # At most 64 entries total. No table or critical history is pruned.
        scheduled[mode] = (rows + [entry])[-32:]
        value["scheduled_runs"] = scheduled
    return value


def run_once(mode, *, now=None, cfg=None, session_factory=None, runner=None, clock=None):
    if mode not in {"dry-run", "archive", "auto"}: raise ValueError("INVALID_ACTIONS_RETENTION_MODE")
    clock = clock or (lambda: datetime.now(timezone.utc))
    now = now or clock()
    from app.core.config import get_settings
    cfg = cfg or get_settings()
    # Even an accidentally enabled repo variable cannot turn a dry-run into DELETE.
    if mode == "dry-run":
        cfg = cfg.model_copy(update={"auto_retention_delete_enabled": False, "operational_delete_enabled": False})
    missing = (["DATABASE_URL"] if cfg.database_url_missing else []) + list(cfg.r2_missing_env)
    invalid = ([] if cfg.database_url_missing or cfg.database_url_valid else ["DATABASE_URL"]) + list(cfg.r2_invalid_env)
    if missing or invalid:
        return {"status": "FAILED", "mode": mode, "LIVE_DELETE": "NOT_RUN", "deleted": 0,
                "failure_stage": "CONFIGURATION", "error_code": "ACTIONS_SECRETS_NOT_CONFIGURED", "missing_env": missing, "invalid_env": invalid}, 1
    from app.jobs.retention import run
    from app.jobs.ledger import SQLLedger, run_idempotent_job
    from app.models import SystemSetting
    from app.jobs.retention_errors import error_code
    from sqlalchemy import text
    runner = runner or run
    if session_factory is None:
        session_factory = lambda: audit_session(cfg)
    if session_factory is None:
        return {"status": "FAILED", "error_code": "DATABASE_NOT_CONFIGURED", "LIVE_DELETE": "NOT_RUN", "deleted": 0}, 1
    interval = 900 if mode == "archive" else 3600
    slot = datetime.fromtimestamp(int(now.timestamp() // interval)*interval, tz=timezone.utc).isoformat()
    job_type = {"archive": "archive_data", "auto": "cleanup_database", "dry-run": "actions_retention_dry_run"}[mode]
    run_id = os.environ.get("GITHUB_RUN_ID", "local")
    run_id = run_id if re.fullmatch(r"[0-9]{1,24}", run_id) else "local"
    # Dry-run attempts are independently audited. Archive/auto share Stage 5 slot keys.
    key = f"{job_type}:{run_id}:{os.environ.get('GITHUB_RUN_ATTEMPT','1')}" if mode == "dry-run" else f"{job_type}:{slot}"
    core = {}
    phase = "SQL_CONNECTION"
    class DiagnosedLedger(SQLLedger):
        def claim(self, *args, **kwargs):
            nonlocal phase
            phase = "LEDGER_CLAIM"
            return super().claim(*args, **kwargs)
        def finish(self, *args, **kwargs):
            nonlocal phase
            phase = "LEDGER_FINISH"
            return super().finish(*args, **kwargs)
    try:
        with session_factory() as session:
            session.execute(text("SELECT 1")).scalar_one()  # separate transport/TLS from ledger access
            session.rollback()
            def handler():
                nonlocal phase
                phase = "RETENTION"
                try:
                    report = runner(mode=mode, settings=cfg)
                except Exception as exc:
                    uncertain = mode == "auto" and cfg.auto_retention_delete_enabled
                    report = {"status": "FAILED", "LIVE_DELETE": "UNKNOWN_RECONCILE_REQUIRED" if uncertain else "NOT_RUN", "deleted": None if uncertain else 0,
                              "failure_stage": phase, "error_code": error_code(exc, phase)}
                core.update(report)
                normalized = normalize(report)
                normalized["reason"] = normalized.get("reason", "RETENTION_RESULT") + "_GHA_" + run_id.upper()
                return normalized
            result = run_idempotent_job(DiagnosedLedger(session), key, job_type, handler)
            summary = public_summary(core or {"status": result.get("status"), "LIVE_DELETE": "NOT_RUN", "deleted": 0})
            summary.update(status=safe_code(result.get("status")), mode=mode,
                           sql_audit="RECORDED", reason=safe_code(result.get("reason"), "NONE"), slot=slot)
            summary["execution_status"] = "EXECUTED" if core else "NOT_EXECUTED"
            summary["archive_records_written"] = (number(core.get("records_written")) if mode == "archive"
                else number(core.get("archive_repair", {}).get("records_written"))) if core else 0
            if not core:
                summary["storage_status"] = "NOT_RUN"
            if result.get("reason") == "IDEMPOTENT_DUPLICATE":
                summary["LIVE_DELETE"], summary["deleted"] = "NOT_RUN", 0
            phase = "SCHEDULER_AUDIT"
            state = session.get(SystemSetting, "scheduler:github-actions-retention")
            previous = state.value if state else {}
            value = record_observation(previous, mode, summary, run_id=run_id, started_at=now, completed_at=clock())
            if state: state.value = value
            else: session.add(SystemSetting(key="scheduler:github-actions-retention", value=value))
            session.commit()
            code = 0 if result.get("status") in {"SUCCESS", "SKIPPED"} else 1
            return summary, code
    except Exception as exc:
        summary = public_summary(core or {"status": "FAILED", "LIVE_DELETE": "NOT_RUN", "deleted": 0})
        return {**summary, "status": "FAILED", "mode": mode, "sql_audit": "UNAVAILABLE",
                "failure_stage": phase, "error_code": error_code(exc, phase)}, 1


def emit(report, code):
    from urllib.parse import quote, quote_plus
    values = [value for name in REQUIRED if (value := os.environ.get(name))]
    patterns = {p for value in values for p in (value, quote(value, safe=""), quote_plus(value))}
    def scrub(value):
        if isinstance(value, str):
            for pattern in sorted(patterns, key=len, reverse=True): value = value.replace(pattern, "[REDACTED]")
            return value
        if isinstance(value, dict): return {k:scrub(v) for k,v in value.items()}
        if isinstance(value, list): return [scrub(v) for v in value]
        return value
    report = scrub(report)
    print(json.dumps(report, ensure_ascii=True, sort_keys=True))
    if path := os.environ.get("RETENTION_REPORT_PATH"):
        try:
            Path(path).write_text(json.dumps(report, indent=2, ensure_ascii=True) + "\n", encoding="utf-8")
        except OSError:
            print("::error::RETENTION_REPORT_WRITE_FAILED")
    if os.environ.get("GITHUB_ACTIONS") == "true":
        if report.get("capacity_status") in {"WARNING", "HIGH", "CRITICAL"}:
            print("::warning::POSTGRES_CAPACITY_" + report["capacity_status"] + "; archive and review blockers, never force-delete unverified data.")
        if report.get("archive_failures", 0) or report.get("failure_stage") == "R2_VERIFICATION":
            print("::error::STORAGE_RECONCILE_REQUIRED; archive verification or repair failed. No forced cleanup.")
        if code:
            print("::error::Retention failed or is partial. Inspect safe summary; no unsafe cleanup fallback is used.")
        elif report.get("retained", 0):
            print("::warning::Some operational records are retained; inspect retention_reasons before any activation.")
        summary_path = os.environ.get("GITHUB_STEP_SUMMARY")
        if summary_path:
            with Path(summary_path).open("a", encoding="utf-8") as stream:
                stream.write("### Retention result\n\n```json\n" + json.dumps(report, indent=2, ensure_ascii=True) + "\n```\n")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=("dry-run", "archive", "auto"), default="dry-run")
    args = parser.parse_args(argv)
    try:
        report, code = run_once(args.mode)
    except Exception:
        # Settings/driver errors can contain secret values. Never emit their repr/traceback.
        report, code = {"status": "FAILED", "error_code": "ACTIONS_RETENTION_CONFIGURATION_OR_RUNTIME_ERROR", "LIVE_DELETE": "NOT_RUN"}, 1
    emit(report, code)
    return code


if __name__ == "__main__": raise SystemExit(main())
