"""48-hour operational cache and immutable, lossless R2 snapshots.

No source fetching, archive overwrite, R2 deletion, or metadata pruning occurs here.
Deletion is a separate, plan-bound action; the scheduled path only plans it.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import hashlib
import json
from uuid import UUID

from sqlalchemy import func, inspect, select, tuple_

from app.models import (ActiveMatch, ActiveOdds, ActiveStatistic, ArchiveJob, Coupon,
                        CouponItem, JobRun, MatchAnalysis, MatchResult, Prediction, SystemSetting)
from app.providers.identity import REVIEWED_IMPORT_KEY
from app.providers.result_sources import utc
from app.storage.archive import SQLArchiveTracker, _verify_object, archive_partition
from app.storage.r2 import parquet_bytes

FORMAT = "lifecycle-v1"
LOCK_ID = 480000000001
DATASETS = {ActiveMatch: "matches", MatchResult: "results", ActiveOdds: "odds",
            ActiveStatistic: "statistics", Prediction: "predictions", MatchAnalysis: "analyses",
            Coupon: "coupons", CouponItem: "coupon_items"}
DELETE_ORDER = (CouponItem, Prediction, MatchAnalysis, ActiveStatistic, ActiveOdds, MatchResult, Coupon, ActiveMatch)
PROTECTED = ("users", "system_settings", "bookmakers", "job_runs", "archive_jobs", "historical_ingest_days",
             "daily_metrics", "lessons", "notification_deliveries")


def aware(value):
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


def canonical(value):
    if isinstance(value, datetime): return aware(value).isoformat()
    if isinstance(value, (UUID, Decimal)): return str(value)
    if isinstance(value, dict): return {k: canonical(v) for k, v in value.items()}
    if isinstance(value, (tuple, list)): return [canonical(v) for v in value]
    return value


def json_bytes(value):
    return json.dumps(canonical(value), sort_keys=True, ensure_ascii=True, separators=(",", ":")).encode()


def sql_snapshot(entity):
    return {c.name: canonical(getattr(entity, c.name)) for c in entity.__table__.columns}


def identity(entity):
    return ":".join(str(getattr(entity, c.name)) for c in entity.__table__.primary_key)


def reference(entity):
    return f"{entity.__tablename__}:{identity(entity)}"


def source_index_key(match):
    return "source-identity:" + hashlib.sha256(json_bytes([match.provider, match.provider_match_id])).hexdigest()


def policy_for(session, match, dataset, entity=None):
    """Permissions are data-source-specific, never inferred from robots or credentials."""
    source = getattr(entity, "provider", None) or match.provider
    if dataset in {"predictions", "analyses", "coupons", "coupon_items"}:
        source = match.provider
    if source == "openligadb" and dataset != "odds" and dataset != "statistics":
        return {"licence": "ODbL-1.0", "attribution": "Data: OpenLigaDB (https://www.openligadb.de), ODbL 1.0",
                "reference": "https://www.openligadb.de/lizenz"}
    if (match.provider, match.provider_match_id) == ("goaloo", "3018869"):
        from app.providers.identity import reviewed_fixture_key
        reviewed = session.get(SystemSetting, REVIEWED_IMPORT_KEY)
        if (reviewed and reviewed.value.get("match_id") == str(match.id)
                and reviewed_fixture_key({"provider": match.provider, "provider_match_id": match.provider_match_id,
                    "league_id": match.league_id, "home_team": match.home_team, "away_team": match.away_team,
                    "start_time": aware(match.start_time)})
                and dataset not in {"odds", "statistics"}):
            candidate = reviewed.value.get("candidate", {})
            if candidate.get("licence") == "ODbL-1.0":
                return {"licence": candidate["licence"], "attribution": candidate["attribution"],
                        "reference": candidate["licence_url"]}
    record = session.get(SystemSetting, f"source-reuse-policy:{source}")
    policy = record.value if record else {}
    if (policy.get("approved") is True and policy.get("provider") == source
            and dataset in policy.get("datasets", []) and policy.get("licence")
            and str(policy.get("reference", "")).startswith("https://") and policy.get("attribution")):
        return {k: policy[k] for k in ("licence", "attribution", "reference")}
    return None


def technical_valid(entity, result, now, match=None):
    from math import isfinite
    if (not result or result.status != "FINAL" or not result.verified_at
            or (match is not None and (entity.provider != match.provider or entity.provider_match_id != match.provider_match_id))
            or entity.name not in {"Shots", "Shots On Goal", "Corner Kicks", "Possession", "Fouls", "Yellow Cards", "Red Cards", "Offsides"}
            or entity.period != "ftstat" or entity.unit not in {"count", "percent", "minutes"}
            or not entity.licence or not entity.permission_reference.startswith("https://")
            or not entity.source_url.startswith("https://")
            or not (aware(result.verified_at) <= aware(entity.observed_at) <= aware(entity.verified_at) <= now)):
        return False
    for value in (entity.home_value, entity.away_value):
        if value is None or not isfinite(value) or value < 0: return False
        if entity.unit == "percent" and value > 100: return False
        if entity.unit == "count" and value != int(value): return False
    return True


def time_anchor(entity):
    if isinstance(entity, ActiveMatch): return entity.start_time
    if isinstance(entity, ActiveOdds): return entity.captured_at
    if isinstance(entity, ActiveStatistic): return entity.observed_at
    if isinstance(entity, MatchResult): return entity.verified_at
    if isinstance(entity, Prediction): return entity.settled_at or entity.created_at
    if isinstance(entity, MatchAnalysis): return entity.analysis_timestamp
    if isinstance(entity, Coupon): return entity.settled_at or entity.created_at
    return entity.settled_at


@dataclass
class Record:
    entity: object
    dataset: str
    row: dict
    season: str
    league: str
    day: object
    part: str
    key: str
    checksum: str
    size: int
    blockers: list[str]


def make_record(session, match, entity, *, now, hours):
    dataset = DATASETS[type(entity)]
    snapshot = sql_snapshot(entity)
    policy = policy_for(session, match, dataset, entity)
    reasons = [] if policy else ["REUSE_PERMISSION_NOT_CONFIRMED"]
    anchor = time_anchor(entity)
    if anchor is None: reasons.append("RETENTION_CLOCK_MISSING")
    elif aware(anchor) > now - timedelta(hours=hours): reasons.append("WITHIN_RETENTION_WINDOW")
    if match.status not in {"FINISHED", "CANCELLED"}: reasons.append("MATCH_NOT_TERMINAL")
    if isinstance(entity, MatchResult) and (entity.status != "FINAL" or not entity.verified_at
            or aware(entity.verified_at) > now or aware(match.start_time) >= now
            or any(type(s) is not int or s < 0 for s in (entity.home_score, entity.away_score))):
        reasons.append("RESULT_NOT_VERIFIED")
    if isinstance(entity, Prediction) and (entity.status not in {"WON", "LOST", "VOID", "CANCELLED"} or not entity.settled_at):
        reasons.append("PREDICTION_UNSETTLED")
    if isinstance(entity, Coupon) and (entity.status not in {"PAPER_WON", "PAPER_LOST", "PAPER_VOID"} or not entity.settled_at):
        reasons.append("COUPON_UNSETTLED")
    if isinstance(entity, CouponItem) and (entity.status not in {"WON", "LOST", "VOID", "CANCELLED"} or not entity.settled_at):
        reasons.append("COUPON_ITEM_UNSETTLED")
    if isinstance(entity, MatchAnalysis) and entity.analysis_status in {"RUNNING", "PENDING"}:
        reasons.append("ANALYSIS_IN_PROGRESS")
    season, league = match.season or "unknown", match.league_id or "unknown"
    export_provider, export_id = match.provider, match.provider_match_id
    home, away = match.home_team, match.away_team
    review = session.get(SystemSetting, REVIEWED_IMPORT_KEY) if match.provider_match_id in {"83237", "3018869"} else None
    if review and review.value.get("match_id") == str(match.id):
        event = review.value["candidate"]["event"]
        season, league, export_provider, export_id = event["season"], event["league_id"], event["provider"], event["provider_match_id"]
        home, away = event["home_team"], event["away_team"]
    if season == "unknown" or league == "unknown": reasons.append("PARTITION_IDENTITY_MISSING")
    # All SQL columns are preserved losslessly for equality checks before deletion.
    # The familiar flattened fields keep the existing historical reader compatible.
    row = {**snapshot, "archive_format": FORMAT, "sql_table": entity.__tablename__,
           "sql_identity": identity(entity), "sql_snapshot_json": json_bytes(snapshot).decode(),
           "provider": export_provider, "provider_match_id": export_id, "league_id": league,
           "league_name": match.league_name, "season": season, "home_team": home, "away_team": away,
           "start_time": aware(match.start_time), "operational_provider": match.provider,
           "operational_provider_match_id": match.provider_match_id,
           "licence": (policy or {}).get("licence"), "attribution": (policy or {}).get("attribution"),
           "permission_reference": (policy or {}).get("reference")}
    if review and review.value.get("match_id") == str(match.id):
        row["identity_review"] = REVIEWED_IMPORT_KEY
    if isinstance(entity, (ActiveStatistic, ActiveOdds, MatchResult)):
        row["source_provider"] = entity.provider
    for name in ("captured_at", "verified_at", "settled_at", "analysis_timestamp", "created_at", "finished_at"):
        if name in snapshot and snapshot[name] is not None: row[name] = utc(snapshot[name])
    if isinstance(entity, MatchResult):
        row.update(result_id=str(entity.id), result_provider=entity.provider)
    if isinstance(entity, ActiveOdds): row.update(odds_id=str(entity.id), odds=float(entity.odds))
    if isinstance(entity, Prediction): row["prediction_id"] = str(entity.id)
    if isinstance(entity, ActiveStatistic):
        row.update(statistic_id=entity.provider_statistic_id, home_value=float(entity.home_value), away_value=float(entity.away_value))
    if isinstance(entity, ActiveMatch):
        row["verified_at"] = None  # the separate verified result provides the final availability
    stamps = [getattr(entity, name, None) for name in ("updated_at", "last_updated_at", "verified_at", "observed_at",
                                                       "settled_at", "captured_at", "created_at", "analysis_timestamp")]
    row["available_at"] = max((aware(t) for t in stamps if t), default=aware(match.start_time))
    payload = parquet_bytes([row])
    checksum = hashlib.sha256(payload).hexdigest()
    part = "lifecycle-" + hashlib.sha256(reference(entity).encode()).hexdigest()[:20] + "-" + checksum
    day = aware(match.start_time).date()
    from app.storage.archive import _safe_partition
    for value in (season, league): _safe_partition(value)
    key = f"{dataset}/season={season}/league={league}/date={day}/part={part}.parquet"
    return Record(entity, dataset, row, season, league, day, part, key, checksum, len(payload), reasons)


def load_bundles(session, *, limit=25, after=None, child_limit=500, lock=False, target=False):
    query = select(ActiveMatch).order_by(ActiveMatch.id)
    if after: query = query.where(ActiveMatch.id > UUID(after))
    if target: query = query.where(((ActiveMatch.provider == "openligadb") & (ActiveMatch.provider_match_id == "83237")) |
                                  ((ActiveMatch.provider == "goaloo") & (ActiveMatch.provider_match_id == "3018869")))
    if lock: query = query.with_for_update()
    matches = session.scalars(query.limit(limit + 1)).all()
    more = len(matches) > limit
    bundles = []
    for match in matches[:limit]:
        entities, reasons = [match], []
        for model in (MatchResult, ActiveOdds, ActiveStatistic, Prediction, MatchAnalysis, CouponItem):
            statement = select(model).where(model.match_id == match.id)
            if lock: statement = statement.with_for_update()
            rows = session.scalars(statement.order_by(*model.__table__.primary_key).limit(child_limit + 1)).all()
            if len(rows) > child_limit: reasons.append("DEPENDENCY_SCAN_LIMIT")
            entities.extend(rows[:child_limit])
        coupon_ids = {row.coupon_id for row in entities if isinstance(row, CouponItem)}
        prediction_ids = {row.id for row in entities if isinstance(row, Prediction)}
        if prediction_ids and session.scalar(select(CouponItem.match_id).where(
                CouponItem.prediction_id.in_(prediction_ids), CouponItem.match_id != match.id).limit(1)) is not None:
            reasons.append("EXTERNAL_PREDICTION_DEPENDENCY")
        if coupon_ids:
            statement = select(Coupon).where(Coupon.id.in_(coupon_ids))
            if lock: statement = statement.with_for_update()
            entities.extend(session.scalars(statement).all())
            # An external match in a coupon requires a later multi-match workflow.
            # Never rely on cascading deletes/SET NULL to resolve that dependency.
            external = session.scalar(select(CouponItem.match_id).where(CouponItem.coupon_id.in_(coupon_ids),
                                     CouponItem.match_id != match.id).limit(1))
            if external is not None: reasons.append("EXTERNAL_COUPON_DEPENDENCY")
        bundles.append((match, entities, reasons))
    return bundles, more, str(matches[min(limit, len(matches)) - 1].id) if matches else None


def verify_record(session, store, record):
    job = session.scalar(select(ArchiveJob).where(ArchiveJob.r2_key == record.key))
    if not job or job.status not in {"VERIFIED", "DELETED"}:
        return "ARCHIVE_LEDGER_NOT_VERIFIED"
    if (job.checksum != record.checksum or job.row_count != 1 or job.file_size_bytes != record.size or not job.verified_at):
        return "ARCHIVE_RECEIPT_MISMATCH"
    try:
        rows = _verify_object(store, record.key, expected_size=record.size, expected_rows=1, expected_checksum=record.checksum)
        if rows != [record.row]: return "ARCHIVE_CONTENT_MISMATCH"
        if json.loads(rows[0]["sql_snapshot_json"]) != sql_snapshot(record.entity): return "SQL_SNAPSHOT_CHANGED"
    except Exception:
        return "ARCHIVE_READBACK_FAILED"
    return None


def plan(session, store, *, now, hours=48, limit=25, after=None, child_limit=500, lock=False, target=False):
    if not 1 <= hours <= 48: raise ValueError("RETENTION_MUST_BE_WITHIN_48_HOURS")
    bundles, more, cursor = load_bundles(session, limit=limit, after=after, child_limit=child_limit, lock=lock, target=target)
    actions, details, totals = [], [], {name: {"scanned": 0, "eligible": 0, "retained": 0, "overdue_retained": 0, "deleted": 0} for name in DATASETS.values()}
    for match, entities, bundle_reasons in bundles:
        index = session.get(SystemSetting, source_index_key(match))
        if not index:
            bundle_reasons.append("SOURCE_IDENTITY_INDEX_MISSING")
        elif index.value.get("match_id") != str(match.id):
            bundle_reasons.append("SOURCE_IDENTITY_INDEX_CONFLICT")
        records = [make_record(session, match, entity, now=now, hours=hours) for entity in entities]
        results = [e for e in entities if isinstance(e, MatchResult)]
        if match.status == "FINISHED" and (len(results) != 1 or results[0].status != "FINAL" or not results[0].verified_at):
            bundle_reasons.append("FINAL_RESULT_MISSING_OR_UNVERIFIED")
        for record in records:
            if isinstance(record.entity, ActiveStatistic) and not technical_valid(record.entity, results[0] if len(results) == 1 else None, now, match):
                record.blockers.append("TECHNICAL_VALIDATION_FAILED")
            if isinstance(record.entity, ActiveStatistic) and (
                    record.entity.licence != record.row["licence"] or record.entity.permission_reference != record.row["permission_reference"]):
                record.blockers.append("TECHNICAL_PERMISSION_CHANGED")
            if isinstance(record.entity, (Prediction, MatchAnalysis)) and (
                    len(results) != 1 or results[0].status != "FINAL" or not results[0].verified_at):
                record.blockers.append("RESULT_EVALUATION_DEPENDENCY")
            error = verify_record(session, store, record)
            if error: record.blockers.append(error)
        blocked = bool(bundle_reasons or any(r.blockers for r in records))
        if not blocked: actions.extend(records)
        for record in records:
            reasons = sorted(set(record.blockers + bundle_reasons + (["MATCH_BUNDLE_DEPENDENCY"] if blocked and not record.blockers else [])))
            stats = totals[record.dataset]
            stats["scanned"] += 1
            stats["retained" if blocked else "eligible"] += 1
            stamp = time_anchor(record.entity)
            age = (now - aware(stamp)).total_seconds() / 3600 if stamp else None
            if blocked and age is not None and age >= hours: stats["overdue_retained"] += 1
            details.append({"record": reference(record.entity), "dataset": record.dataset,
                            "eligible": not blocked, "reasons": reasons, "archive_key": record.key,
                            "age_hours": round(age, 4) if age is not None else None,
                            "snapshot_sha256": record.checksum})
    # Shared coupons can appear in more than one held bundle. Report physical
    # SQL rows once, rather than multiplying the protected-record count.
    unique_details = {}
    for detail in details:
        previous = unique_details.get(detail["record"])
        if previous:
            previous["reasons"] = sorted(set(previous["reasons"] + detail["reasons"]))
            previous["eligible"] = previous["eligible"] and detail["eligible"]
        else:
            unique_details[detail["record"]] = detail
    details = list(unique_details.values())
    for stats in totals.values():
        for field in stats: stats[field] = 0
    for detail in details:
        stats = totals[detail["dataset"]]
        stats["scanned"] += 1
        stats["eligible" if detail["eligible"] else "retained"] += 1
        if not detail["eligible"] and detail["age_hours"] is not None and detail["age_hours"] >= hours:
            stats["overdue_retained"] += 1
    digest = hashlib.sha256(json_bytes({"version": FORMAT, "hours": hours,
        "actions": sorted((reference(r.entity), r.checksum) for r in actions), "target": target,
        "after": after, "limit": limit, "child_limit": child_limit})).hexdigest()
    report = {"status": "DRY_RUN", "policy_hours": hours, "as_of": now.isoformat(), "approval_digest": digest,
              "LIVE_DELETE": "NOT_RUN", "deleted": 0, "datasets": totals, "records": details,
              "scope": {"matches_scanned": len(bundles), "truncated": more, "next_after_match_id": cursor if more else None},
              "protected_metadata": list(PROTECTED),
              "pending_approval": sum(t["eligible"] for t in totals.values()),
              "retained_for_safety": sum(t["retained"] for t in totals.values())}
    return report, actions


def unknown_dependencies(session):
    schema = inspect(session.get_bind())
    expected = {model.__tablename__ for model in DATASETS}
    known_fks = {(model.__tablename__, fk.parent.name, fk.column.table.name, fk.column.name)
                 for model in DATASETS for fk in model.__table__.foreign_keys}
    unexpected = []
    for table in schema.get_table_names():
        for fk in schema.get_foreign_keys(table):
            if fk.get("referred_table") in expected:
                for col, ref in zip(fk.get("constrained_columns", []), fk.get("referred_columns", [])):
                    if (table, col, fk["referred_table"], ref) not in known_fks:
                        unexpected.append(table)
    return sorted(set(unexpected))


def execute(session, store, *, approval, enabled, now, **options):
    """Operator-only, fresh locked plan. Never called by the stage-4 scheduler."""
    if not enabled: raise PermissionError("OPERATIONAL_DELETE_DISABLED")
    try:
        report, actions = plan(session, store, now=now, lock=True, **options)
        if approval != report["approval_digest"]: raise PermissionError("APPROVAL_PLAN_CHANGED")
        if unknown_dependencies(session): raise PermissionError("UNKNOWN_FOREIGN_KEY_DEPENDENCY")
        if not actions:
            session.rollback()
            report["status"] = "COMPLETED"
            return report
        # Every archive is downloaded again while parent/child SQL locks are held.
        for record in actions:
            if verify_record(session, store, record): raise RuntimeError("PRE_DELETE_REVERIFICATION_FAILED")
        for model in DELETE_ORDER:
            for record in actions:
                if type(record.entity) is model:
                    session.delete(record.entity)
                    report["datasets"][record.dataset]["deleted"] += 1
            session.flush()  # explicit child-first deletes; never invoke FK cascades intentionally
        # Confirm exact PK removal inside the same transaction before committing
        # counts/audit. A trigger or unexpected row-count mismatch must roll back.
        for model in DELETE_ORDER:
            keys = [tuple(getattr(r.entity, c.name) for c in model.__table__.primary_key)
                    for r in actions if type(r.entity) is model]
            for offset in range(0, len(keys), 1000):
                remaining = session.scalar(select(func.count()).select_from(model).where(
                    tuple_(*model.__table__.primary_key).in_(keys[offset:offset+1000])))
                if remaining: raise RuntimeError("DELETE_NOT_APPLIED_ROLLBACK_REQUIRED")
        for record in actions:
            if isinstance(record.entity, ActiveMatch):
                index = session.get(SystemSetting, source_index_key(record.entity))
                index.value = {**index.value, "operational_removed_at": now.isoformat(), "cleanup_approval_digest": approval}
        count = sum(t["deleted"] for t in report["datasets"].values())
        session.add(JobRun(idempotency_key=f"retention_cleanup:{approval}", job_type="retention_cleanup",
                          status="SUCCESS", started_at=now, finished_at=now, records_seen=len(actions),
                          records_written=count, records_processed=count, records_skipped=sum(t["retained"] for t in report["datasets"].values()),
                          records_failed=0))
        session.commit()
        report.update(status="COMPLETED", deleted=sum(t["deleted"] for t in report["datasets"].values()),
                      pending_approval=0, LIVE_DELETE="EXECUTED")
        return report
    except Exception:
        session.rollback()
        raise


def archive_early(session, store, *, now, hours=48, limit=25, after=None, child_limit=500, target=False):
    bundles, more, cursor = load_bundles(session, limit=limit, after=after, child_limit=child_limit, target=target)
    tracker, written, failures, entries = SQLArchiveTracker(session), 0, 0, []
    # Durable archive_jobs transitions make upload/acknowledgement failures retryable.
    blocked_bundles = []
    for match, entities, reasons in bundles:
        if reasons:
            failures += 1
            blocked_bundles.append({"match_id": str(match.id), "reasons": reasons})
        result = next((e for e in entities if isinstance(e, MatchResult)), None)
        for entity in entities:
            record = make_record(session, match, entity, now=now, hours=hours)
            forbidden = [r for r in record.blockers if r in {"REUSE_PERMISSION_NOT_CONFIRMED", "RESULT_NOT_VERIFIED", "PARTITION_IDENTITY_MISSING"}]
            if isinstance(entity, ActiveStatistic) and not technical_valid(entity, result, now, match):
                forbidden.append("TECHNICAL_VALIDATION_FAILED")
            if isinstance(entity, ActiveStatistic) and (entity.licence != record.row["licence"] or entity.permission_reference != record.row["permission_reference"]):
                forbidden.append("TECHNICAL_PERMISSION_CHANGED")
            if forbidden:
                failures += 1
                entries.append({"record": reference(entity), "status": "HELD", "reasons": forbidden})
                continue
            try:
                previous = tracker.get_status(record.key)
                archive_partition(store, tracker=tracker, dataset=record.dataset, season=record.season, league=record.league,
                                  day=record.day, rows=[record.row], part=record.part, retries=1)
                error = verify_record(session, store, record)
                if error: raise RuntimeError(error)
                written += int(previous != "VERIFIED")
                entries.append({"record": reference(entity), "status": "VERIFIED", "key": record.key,
                                "sha256": record.checksum, "row_count": 1, "content": "PASS"})
            except Exception:
                session.rollback()
                failures += 1
                entries.append({"record": reference(entity), "status": "FAILED", "reasons": ["ARCHIVE_FAILED_RETRY_NEXT_CYCLE"]})
        fixture_entries = [e for e in entries if e["record"] == reference(match) and e["status"] == "VERIFIED"]
        if fixture_entries:
            index = session.get(SystemSetting, source_index_key(match))
            if index and index.value.get("match_id") != str(match.id):
                failures += 1
                blocked_bundles.append({"match_id": str(match.id), "reasons": ["SOURCE_IDENTITY_INDEX_CONFLICT"]})
            elif not index:
                # One compact, permanent identity link per source fixture. Never
                # prune it with operational data or silently reassign its UUID.
                session.add(SystemSetting(key=source_index_key(match), value={
                    "provider": match.provider, "provider_match_id": match.provider_match_id,
                    "match_id": str(match.id), "season": match.season, "league_id": match.league_id,
                    "first_verified_fixture_key": fixture_entries[0]["key"]}))
                session.commit()
    return {"status": "PARTIAL" if failures else "SUCCESS" if written else "SKIPPED", "records_written": written,
            "records_failed": failures, "records_seen": len(entries), "entries": entries, "LIVE_DELETE": "NOT_RUN",
            "blocked_bundles": blocked_bundles,
            "scope": {"truncated": more, "next_after_match_id": cursor if more else None}}
