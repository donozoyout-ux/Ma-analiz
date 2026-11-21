"""Read-only final retention inspection. No repair, audit write, DELETE or VACUUM."""
import argparse
from contextlib import closing
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path

from sqlalchemy import bindparam, func, select, text

from app.jobs import import_preflight, retention
from app.models import (ActiveMatch, ActiveOdds, ActiveStatistic, Coupon, CouponItem, JobRun,
                        MatchAnalysis, MatchResult, Prediction, SystemSetting)
from app.storage import lifecycle as lc

ANCHORS = {ActiveMatch: ActiveMatch.start_time, MatchResult: MatchResult.verified_at,
    ActiveOdds: ActiveOdds.captured_at, ActiveStatistic: ActiveStatistic.observed_at,
    Prediction: func.coalesce(Prediction.settled_at, Prediction.created_at),
    MatchAnalysis: MatchAnalysis.analysis_timestamp, Coupon: func.coalesce(Coupon.settled_at, Coupon.created_at),
    CouponItem: CouponItem.settled_at}
PREFIXES = ("source-identity:", "review-source:", "review-evidence:", "review-audit:",
            "operator-import:", "controlled-import:", "verified-import:")


def inventory(session, now):
    result = {}
    for model, dataset in lc.DATASETS.items():
        anchor = ANCHORS[model]
        total, old, missing = session.execute(select(func.count(),
            func.count().filter(anchor <= now - timedelta(hours=48)),
            func.count().filter(anchor.is_(None))).select_from(model)).one()
        result[dataset] = {"total": total, "over_48h": old, "missing_age_anchor": missing}
    return result


def inspect_records(session, store, *, now, max_pages=20, batch_size=25, child_limit=500):
    """Bounded read-only traversal. An incomplete scan can never claim global eligibility."""
    counts = inventory(session, now)
    unknown = lc.unknown_dependencies(session)
    details, after, complete = {}, None, False
    pages = []
    for _ in range(max_pages):
        plan, _ = lc.plan(session, store, now=now, hours=48, limit=batch_size, after=after, child_limit=child_limit)
        pages.append({"plan_sha256": plan["approval_digest"], "scope": plan["scope"]})
        for row in plan["records"]:
            item = dict(row)
            if unknown:
                item["eligible"] = False
                item["reasons"] = sorted(set(item["reasons"] + ["UNKNOWN_FOREIGN_KEY_DEPENDENCY"]))
            previous = details.get(item["record"])
            if previous:
                item["eligible"] &= previous["eligible"]
                item["reasons"] = sorted(set(item["reasons"] + previous["reasons"]))
            details[item["record"]] = item
        if not plan["scope"]["truncated"]:
            complete = True
            break
        cursor = plan["scope"]["next_after_match_id"]
        if not cursor or cursor == after:
            break
        after = cursor
    for dataset, info in counts.items():
        rows = [r for r in details.values() if r["dataset"] == dataset]
        info.update(scanned=len(rows), eligible=sum(r["eligible"] for r in rows),
            retained=sum(not r["eligible"] for r in rows), not_scanned=info["total"] - len(rows))
        if info["not_scanned"]:
            complete = False
    for row in details.values():
        archive_errors = {"ARCHIVE_LEDGER_NOT_VERIFIED", "ARCHIVE_RECEIPT_MISMATCH", "ARCHIVE_CONTENT_MISMATCH",
                          "SQL_SNAPSHOT_CHANGED", "ARCHIVE_READBACK_FAILED"}
        row["archive_verification"] = "NOT_VERIFIED" if archive_errors.intersection(row["reasons"]) else "PASS"
        row["source_identity"] = "NOT_VERIFIED" if any(r.startswith("SOURCE_IDENTITY_") for r in row["reasons"]) else "PASS"
    eligible = sum(row["eligible"] for row in details.values())
    return {"status": "INCOMPLETE_SCAN" if not complete else "ELIGIBLE_RECORDS_FOUND" if eligible else "NO_ELIGIBLE_RECORDS",
        "scan_complete": complete, "policy_hours": 48, "as_of": now.isoformat(),
        "over_48h": sum(c["over_48h"] for c in counts.values()), "eligible": eligible,
        "retained": sum(not row["eligible"] for row in details.values()), "deleted": 0,
        "unknown_dependency_tables": len(unknown), "datasets": counts, "records": list(details.values()),
        "pages": pages, "archive_checks_on_pass": ["OBJECT_EXISTS", "SHA256", "SIZE", "ROW_COUNT",
            "METADATA", "FULL_CONTENT", "SQL_SNAPSHOT", "SQL_LEDGER"],
        **import_preflight.flags()}


def protected_counts(session):
    counts = retention.metadata_report(session)
    return {"tables": counts["tables"], "warnings": counts["warnings"],
        "metadata_prefix_counts": {p: session.scalar(select(func.count()).select_from(SystemSetting).where(
            SystemSetting.key.like(p + "%"))) for p in PREFIXES},
        "successful_cleanup_audits": session.scalar(select(func.count()).select_from(JobRun).where(
            JobRun.job_type == "retention_cleanup", JobRun.status == "SUCCESS"))}


def scheduler_audit(session):
    from app.jobs.actions_retention import safe_code
    from app.providers.result_sources import utc
    item = session.get(SystemSetting, "scheduler:github-actions-retention")
    result = {}
    for mode in ("archive", "auto", "dry-run"):
        row = item.value.get(mode, {}) if item else {}
        at = utc(row.get("completed_at"))
        run_id = str(row.get("run_id", ""))
        result[mode] = {"completed_at": at.isoformat() if at else None,
            "run_id": run_id if run_id.isdigit() and len(run_id) <= 20 else None,
            "status": safe_code(row.get("status")), "LIVE_DELETE": safe_code(row.get("LIVE_DELETE")),
            "deleted": row.get("deleted") if type(row.get("deleted")) is int else None}
    return result


def table_sizes(session):
    from app.db.base import Base
    statement = text("""SELECT relname AS table, pg_table_size(relid)::bigint AS table_bytes,
        pg_indexes_size(relid)::bigint AS index_bytes, pg_total_relation_size(relid)::bigint AS total_bytes,
        n_live_tup::bigint AS live_rows_estimate, n_dead_tup::bigint AS dead_rows_estimate
        FROM pg_stat_user_tables WHERE relname IN :tables ORDER BY pg_total_relation_size(relid) DESC""").bindparams(
            bindparam("tables", expanding=True))
    return [dict(row) for row in session.execute(statement, {"tables": list(Base.metadata.tables)}).mappings()]


def run(settings=None):
    from app.core.config import get_settings
    from app.jobs.retention_errors import error_code
    cfg = settings or get_settings()
    output = {"stage": "7D.1", "mode": "READ_ONLY_RETENTION_VALIDATION", "status": "BLOCKED",
              "deleted": 0, **import_preflight.flags()}
    output["storage_preflight"] = import_preflight.run(cfg)
    if output["storage_preflight"]["status"] != "PASS":
        return output
    phase = "SQL_CONNECTION"
    try:
        with retention.storage_session(cfg, readonly=True) as session:
            session.execute(text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ"))
            readonly = session.execute(text("SHOW transaction_read_only")).scalar_one()
            if readonly != "on":
                raise RuntimeError()
            phase = "SQL_QUERY"
            before = protected_counts(session)
            output["capacity"] = retention.capacity(session.execute(text("SELECT pg_database_size(current_database())")).scalar_one())
            output["table_sizes"] = table_sizes(session)
            output["vacuum"] = retention.vacuum_assessment(session)
            output["sql_scheduler_audit"] = scheduler_audit(session)
            phase = "R2_VERIFICATION"
            store = import_preflight.bounded_store(cfg)
            with closing(store.client):
                output["plan"] = inspect_records(session, store, now=datetime.now(timezone.utc))
                output["protected_target"] = retention.target_check(session, store)
            phase = "SQL_QUERY"
            after = protected_counts(session)
            output["protected_metadata"] = {"snapshot_counts": after, "same_snapshot_counts_unchanged": before == after,
                "transaction_read_only": readonly, "isolation": "REPEATABLE_READ", "values_or_user_data": "NOT_EXPORTED",
                "excluded_from_cleanup": list(lc.PROTECTED), "audit_write": "NOT_RUN"}
            output["status"] = output["plan"]["status"]
    except Exception as exc:
        output.update(status="FAILED", error_code=error_code(exc, phase), failure_stage=phase)
    return output


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)
    try:
        report = run()
    except Exception:
        report = {"status": "FAILED", "error_code": "RETENTION_VALIDATION_CONFIGURATION_FAILED", **import_preflight.flags()}
    rendered = json.dumps(report, ensure_ascii=True, indent=2, default=str)
    if args.output:
        args.output.write_text(rendered + "\n", encoding="utf-8")
    print(rendered)
    return 0 if report["status"] in {"NO_ELIGIBLE_RECORDS", "ELIGIBLE_RECORDS_FOUND"} else 1


if __name__ == "__main__":
    raise SystemExit(main())
