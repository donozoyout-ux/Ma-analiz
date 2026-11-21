"""48h retention: read-only plan, explicit manual modes and separately gated automation."""
from __future__ import annotations

import argparse
from contextlib import contextmanager
from datetime import datetime, timezone
import json
import hashlib
from uuid import UUID

from sqlalchemy import bindparam, create_engine, func, select, text
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.models import ActiveMatch, ArchiveJob, SystemSetting
from app.providers.identity import REVIEWED_IMPORT_KEY
from app.storage import lifecycle
from app.storage.r2 import R2ObjectStore


def capacity(size):
    from app.storage.monitor import database_capacity
    return database_capacity(size)


def metadata_report(session):
    from app.db.base import Base
    counts = {table: session.scalar(select(func.count()).select_from(Base.metadata.tables[table]))
              for table in lifecycle.PROTECTED}
    return {"tables": counts, "delete": "DISABLED", "growth_control": "COUNTS_AND_TABLE_BYTES_ALERTS",
            "review_above_rows": 100_000,
            "warnings": [f"METADATA_GROWTH:{table}" for table, count in counts.items() if count >= 100_000]}


def vacuum_assessment(session):
    """Read-only diagnostics. Never run VACUUM, FULL, table rewrites or DDL."""
    try:
        with session.begin_nested():
            settings = session.execute(text("SELECT current_setting('autovacuum') AS autovacuum, current_setting('track_counts') AS track_counts")).mappings().one()
            query = text("""SELECT relname AS table, n_live_tup, n_dead_tup, last_vacuum, last_autovacuum,
                            last_analyze, last_autoanalyze
                            FROM pg_stat_user_tables WHERE relname IN :tables ORDER BY relname""").bindparams(bindparam("tables", expanding=True))
            rows = session.execute(query, {"tables": [m.__tablename__ for m in lifecycle.DATASETS]}).mappings().all()
            tables = []
            for row in rows:
                item = dict(row)
                live, dead = int(item["n_live_tup"] or 0), int(item["n_dead_tup"] or 0)
                item["review_needed"] = dead >= 1000 and dead / max(1, live + dead) >= .2
                tables.append(lifecycle.canonical(item))
        return {"status": "ASSESSED", **dict(settings), "tables": tables, "row_counts_are_estimates": True,
                "manual_vacuum": "NOT_RUN", "vacuum_full": "DISABLED",
                "space_reclamation": "DELETE_MAY_NOT_SHRINK_DATABASE_FILE;_AUTOVACUUM_REUSES_SPACE",
                "recommendation": "REVIEW_AUTOVACUUM" if settings["autovacuum"] != "on" or any(t["review_needed"] for t in tables) else "MONITOR_AUTOVACUUM"}
    except Exception:
        return {"status": "UNAVAILABLE", "manual_vacuum": "NOT_RUN", "vacuum_full": "DISABLED"}


def scheduler_report(session, now):
    actions = session.get(SystemSetting, "scheduler:github-actions-retention")
    if actions:
        ages = {}
        for mode in ("archive", "auto"):
            at = lifecycle.utc(actions.value.get(mode, {}).get("completed_at"))
            ages[mode] = (now-at).total_seconds() if at else None
        fresh = all(ages[m] is not None and 0 <= ages[m] <= limit for m, limit in (("archive", 2700), ("auto", 7200)))
        return {"owner": "github-actions", "status": "FRESH" if fresh else "STALE_OR_NOT_INITIALIZED",
                "age_seconds": ages, "last_runs": actions.value}
    heartbeat = session.get(SystemSetting, "scheduler:retention-heartbeat")
    last_run = session.get(SystemSetting, "lifecycle:last-auto-run")
    if not heartbeat:
        return {"status": "NO_HEARTBEAT", "last_auto_run": last_run.value if last_run else None}
    stamp = lifecycle.utc(heartbeat.value.get("last_cycle_at"))
    age = (now-stamp).total_seconds() if stamp else None
    status = "FRESH" if age is not None and 0 <= age <= 900 else "STALE_OR_CLOCK_SKEW"
    return {"status": status, "age_seconds": age, "heartbeat": heartbeat.value,
            "last_auto_run": last_run.value if last_run else None}


def target_check(session, store):
    """Storage evidence only: never substitute the old source/dry-run JSON for live rows."""
    from app.models import MatchResult
    from app.storage.archive import _verify_object
    query = select(ActiveMatch).where(((ActiveMatch.provider == "openligadb") & (ActiveMatch.provider_match_id == "83237")) |
                                    ((ActiveMatch.provider == "goaloo") & (ActiveMatch.provider_match_id == "3018869")))
    matches = session.scalars(query).all()
    journal = session.get(SystemSetting, REVIEWED_IMPORT_KEY)
    if not matches and not journal:
        return {"status": "NOT_FOUND", "postgres": "NOT_FOUND", "r2": "NOT_VERIFIED", "LIVE_DELETE": "NOT_RUN"}
    if matches and (len(matches) != 1 or not journal or journal.value.get("match_id") != str(matches[0].id)):
        return {"status": "BLOCKED", "reason": "TARGET_IDENTITY_OR_JOURNAL_CONFLICT"}
    match = matches[0] if matches else None
    result = None
    if match:
        from app.providers.identity import reviewed_fixture_key
        if not reviewed_fixture_key({"provider": match.provider, "provider_match_id": match.provider_match_id,
                "league_id": match.league_id, "start_time": lifecycle.aware(match.start_time),
                "home_team": match.home_team, "away_team": match.away_team}):
            return {"status": "BLOCKED", "reason": "TARGET_FIXTURE_IDENTITY_CHANGED"}
        result = session.scalar(select(MatchResult).where(MatchResult.match_id == match.id))
        if (not result or result.provider != "openligadb" or result.provider_result_id != "83237"
                or result.status != "FINAL" or (result.home_score, result.away_score) != (1, 1) or not result.verified_at
                or lifecycle.aware(result.verified_at) != lifecycle.utc(journal.value.get("candidate", {}).get("verified_at"))):
            return {"status": "BLOCKED", "reason": "TARGET_SQL_RESULT_NOT_VERIFIED"}
    receipts = journal.value.get("receipts", {})
    verified = {}
    try:
        if set(receipts) != {"matches", "results"}: raise ValueError("INCOMPLETE_RECEIPTS")
        for dataset, receipt in receipts.items():
            if receipt["key"] != f"{dataset}/season=2026-2027/league=de.bl1/date=2026-11-20/part=verified-openligadb-83237.parquet":
                raise ValueError("PARTITION_CONFLICT")
            ledger = session.scalar(select(ArchiveJob).where(ArchiveJob.r2_key == receipt["key"]))
            if (not ledger or ledger.status not in {"VERIFIED", "DELETED"} or not ledger.verified_at
                    or ledger.checksum != receipt["sha256"] or ledger.row_count != 1 or ledger.file_size_bytes != receipt["size_bytes"]):
                raise ValueError("ARCHIVE_LEDGER_CONFLICT")
            rows = _verify_object(store, receipt["key"], expected_size=receipt["size_bytes"],
                                  expected_rows=1, expected_checksum=receipt["sha256"])
            expected = journal.value["archive_rows"][dataset]
            actual = lifecycle.canonical(rows[0])
            if actual != expected: raise ValueError("CONTENT_CONFLICT")
            if dataset == "results" and (actual["home_score"], actual["away_score"]) != (1, 1):
                raise ValueError("SQL_SCORE_CONFLICT")
            verified[dataset] = {"key": receipt["key"], "sha256": receipt["sha256"], "row_count": 1, "readback": "PASS"}
        if match:
            return {"status": "VERIFIED", "postgres": "PASS", "r2": "PASS", "receipts": verified,
                    "match_id": str(match.id), "verified_at": lifecycle.aware(result.verified_at).isoformat(), "LIVE_DELETE": "NOT_RUN"}
        # Cache expiry is not data loss. Require the atomic cleanup audit, rather
        # than interpreting unexplained SQL absence as successful deletion.
        row = journal.value["archive_rows"]["matches"]
        key = "source-identity:" + hashlib.sha256(lifecycle.json_bytes([
            row["operational_provider"], row["operational_provider_match_id"]])).hexdigest()
        index = session.get(SystemSetting, key)
        approval = index.value.get("cleanup_approval_digest") if index else None
        from app.models import JobRun
        audit = session.scalar(select(JobRun).where(JobRun.idempotency_key == f"retention_cleanup:{approval}")) if approval else None
        proved = bool(index and index.value.get("operational_removed_at") and index.value.get("match_id") == journal.value["match_id"]
                      and audit and audit.status == "SUCCESS" and audit.records_written > 0)
        return {"status": "ARCHIVED_ONLY" if proved else "BLOCKED", "postgres": "VERIFIED_CACHE_EXPIRY" if proved else "MISSING_WITHOUT_CLEANUP_PROOF",
                "r2": "PASS", "receipts": verified, "LIVE_DELETE": "NOT_RUN"}
    except Exception:
        return {"status": "BLOCKED", "postgres": "PASS", "r2": "FAILED", "reason": "TARGET_ARCHIVE_NOT_VERIFIED"}


@contextmanager
def storage_session(settings, *, readonly):
    url = settings.database_url
    if url.startswith("postgresql://"): url = url.replace("postgresql://", "postgresql+psycopg://", 1)
    engine = create_engine(url, hide_parameters=True, connect_args={"connect_timeout": 10}, pool_pre_ping=True)
    try:
        with engine.connect() as connection:
            if readonly:
                connection.execute(text("SET TRANSACTION READ ONLY"))
                connection.execute(text("SET LOCAL statement_timeout = '30s'"))
                with Session(bind=connection, expire_on_commit=False) as session:
                    yield session
                connection.rollback()
            else:
                acquired = connection.execute(text("SELECT pg_try_advisory_lock(:key)"), {"key": lifecycle.LOCK_ID}).scalar_one()
                connection.commit()
                if not acquired: raise RuntimeError("LIFECYCLE_ALREADY_RUNNING")
                try:
                    with Session(bind=connection, expire_on_commit=False) as session:
                        yield session
                finally:
                    connection.rollback()
                    connection.execute(text("SELECT pg_advisory_unlock(:key)"), {"key": lifecycle.LOCK_ID})
                    connection.commit()
    finally:
        engine.dispose()


def run(*, mode="dry-run", approval=None, after=None, target=False, settings=None):
    cfg = settings or get_settings()
    report = {"status": "NOT_CONFIGURED", "mode": mode, "LIVE_DELETE": "NOT_RUN", "deleted": 0,
              "postgres": "NOT_RUN", "r2": "NOT_RUN", "target": {"status": "NOT_RUN"},
              "capacity": {"status": "NOT_RUN", "bytes": None, "limit_bytes": 1_000_000_000},
              "metadata": {"status": "NOT_RUN"},
              "scheduler": {"status": "NOT_RUN"},
              "auto_delete_enabled_in_this_process": cfg.auto_retention_delete_enabled,
              "missing_env": (["DATABASE_URL"] if cfg.database_url_missing else []) + list(cfg.r2_missing_env),
              "invalid_env": ([] if cfg.database_url_valid or cfg.database_url_missing else ["DATABASE_URL"]) + list(cfg.r2_invalid_env)}
    if report["missing_env"] or report["invalid_env"]: return report
    if mode == "execute" and (not cfg.operational_delete_enabled or not approval):
        report.update(status="BLOCKED", error_code="EXPLICIT_DELETE_ENABLE_AND_PLAN_APPROVAL_REQUIRED")
        return report
    try:
        auto_enabled = mode == "auto" and cfg.auto_retention_delete_enabled
        readonly = mode == "dry-run" or (mode == "auto" and not auto_enabled)
        with storage_session(cfg, readonly=readonly) as session:
            size = session.execute(text("SELECT pg_database_size(current_database())")).scalar_one()
            report["capacity"] = capacity(int(size))
            report["postgres"] = "READ_ONLY_CONNECTED" if readonly else "CONNECTED"
            report["largest_tables"] = [dict(row) for row in session.execute(text("""
                SELECT relname AS table, pg_total_relation_size(relid)::bigint AS size_bytes,
                       COALESCE(n_live_tup, 0)::bigint AS rows_estimate
                FROM pg_stat_user_tables ORDER BY pg_total_relation_size(relid) DESC LIMIT 20
            """)).mappings()]
            report["target"] = target_check(session, R2ObjectStore())
            # Read revision metadata only. No upgrade or CREATE is ever called.
            from alembic.runtime.migration import MigrationContext
            head = set(MigrationContext.configure(session.connection()).get_current_heads())
            from alembic.config import Config
            from alembic.script import ScriptDirectory
            from app.core.config import PROJECT_ROOT
            alembic = Config(str(PROJECT_ROOT / "backend/alembic.ini"))
            alembic.set_main_option("script_location", str(PROJECT_ROOT / "backend/migrations"))
            if head != set(ScriptDirectory.from_config(alembic).get_heads()):
                report.update(status="SCHEMA_NOT_READY", error_code="PREPARED_STATISTICS_MIGRATION_NOT_APPLIED")
                return report
            report["metadata"] = metadata_report(session)
            report["scheduler"] = scheduler_report(session, datetime.now(timezone.utc))
            report["vacuum"] = vacuum_assessment(session)
            store = R2ObjectStore()
            cursor_key = "lifecycle:auto-retention-cursor" if mode == "auto" else "lifecycle:archive-cursor"
            cursor_record = session.get(SystemSetting, cursor_key) if (mode == "archive" or auto_enabled) and not target else None
            if cursor_record and after is None:
                after = cursor_record.value.get("after_match_id")
            options = {"now": datetime.now(timezone.utc), "hours": cfg.operational_retention_hours,
                       "limit": cfg.lifecycle_batch_size, "child_limit": cfg.lifecycle_child_limit,
                       "after": after, "target": target}
            if mode == "archive":
                outcome = lifecycle.archive_early(session, store, **options)
                if not target:
                    value = {"after_match_id": outcome["scope"]["next_after_match_id"]}
                    if cursor_record: cursor_record.value = value
                    else: session.add(SystemSetting(key=cursor_key, value=value))
                    session.commit()
            elif mode == "auto":
                outcome = lifecycle.automatic(session, store, enabled=auto_enabled, **options)
                if auto_enabled:
                    if not target:
                        value = {"after_match_id": outcome["scope"]["next_after_match_id"]}
                        if cursor_record: cursor_record.value = value
                        else: session.add(SystemSetting(key=cursor_key, value=value))
                    last = session.get(SystemSetting, "lifecycle:last-auto-run")
                    data = {"as_of": options["now"].isoformat(), "policy_hours": 48, "deleted": outcome["deleted"],
                            "plan_digest": outcome["approval_digest"], "LIVE_DELETE": outcome["LIVE_DELETE"],
                            "retained_for_safety": outcome["retained_for_safety"], "datasets": outcome["datasets"],
                            "archive_failures": outcome["archive_repair"]["records_failed"], "capacity_before": report["capacity"]}
                    if last: last.value = data
                    else: session.add(SystemSetting(key="lifecycle:last-auto-run", value=data))
                    session.commit()
            elif mode == "execute":
                outcome = lifecycle.execute(session, store, approval=approval, enabled=cfg.operational_delete_enabled, **options)
            else:
                outcome, _ = lifecycle.plan(session, store, **options)
            report.update(outcome, postgres="PASS", r2="CHECKED")
    except PermissionError as exc:
        allowed = {"OPERATIONAL_DELETE_DISABLED", "APPROVAL_PLAN_CHANGED", "UNKNOWN_FOREIGN_KEY_DEPENDENCY"}
        report.update(status="BLOCKED", error_code=str(exc) if str(exc) in allowed else "DELETE_APPROVAL_REQUIRED")
    except Exception:
        report.update(status="FAILED", error_code="LIFECYCLE_CHECK_OR_OPERATION_FAILED")
        if mode in {"execute", "auto"}:
            report.update(LIVE_DELETE="UNKNOWN_RECONCILE_REQUIRED", deleted=None)
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true")
    mode.add_argument("--archive", action="store_true", help="early append-only R2 writes; never deletes SQL")
    mode.add_argument("--execute", action="store_true", help="operator only; requires env gate and exact reviewed plan digest")
    mode.add_argument("--auto", action="store_true", help="worker path; AUTO_RETENTION_DELETE_ENABLED controls real deletion")
    parser.add_argument("--approve-plan")
    parser.add_argument("--after-match-id", type=UUID)
    parser.add_argument("--target", action="store_true", help="only reviewed Bayern/Cologne fixture")
    args = parser.parse_args(argv)
    if args.approve_plan and not args.execute: parser.error("--approve-plan requires --execute")
    report = run(mode="archive" if args.archive else "execute" if args.execute else "auto" if args.auto else "dry-run",
                 approval=args.approve_plan, after=str(args.after_match_id) if args.after_match_id else None, target=args.target)
    print(json.dumps(report, ensure_ascii=True, indent=2))
    return 0 if report["status"] in {"DRY_RUN", "SUCCESS", "SKIPPED", "COMPLETED"} else 1


if __name__ == "__main__": raise SystemExit(main())
