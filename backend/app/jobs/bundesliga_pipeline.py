"""Phase D: Safe Bundesliga match ingestion pipeline and automation controls.

Safety Constraints:
- HARD GATE: Refuses to execute if auto_retention_delete_enabled or operational_delete_enabled is True.
- Gated by bundesliga_ingest_enabled (default: False).
- Defaults to dry_run=True.
- Quota and cost enforcement on requests, matches, and R2 operations.
- Preserves UUIDs on updates; never changes primary keys.
- Strictly ODbL 1.0 license attribution.
- Emits push notification event contracts for downstream delivery.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
import hashlib
import json
from typing import Any
from uuid import uuid4
from zoneinfo import ZoneInfo

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.core.config import Settings, get_settings
from app.models import ActiveMatch, MatchResult, SystemSetting, ArchiveJob
from app.notifications.events import (
    MatchEventPayload,
    MatchEventType,
    create_match_event,
)
from app.providers.identity import deduplicate_scope
from app.providers.openligadb import ATTRIBUTION, LICENCE, LICENCE_URL, OpenLigaDBProvider
from app.storage.archive import SQLArchiveTracker, archive_partition
from app.storage.quota import QuotaExceededError, get_quota_tracker, DurableQuotaTracker
from app.storage.r2 import ObjectStore, parquet_bytes

ISTANBUL = ZoneInfo("Europe/Istanbul")


class PipelineSafetyError(RuntimeError):
    """Raised when safety constraints or gates are violated."""
    code: str = "SAFETY_VIOLATION"


def check_pipeline_safety_gates(settings: Settings) -> None:
    """Enforce absolute safety constraints."""
    if getattr(settings, "auto_retention_delete_enabled", False):
        raise PipelineSafetyError("SAFETY_VIOLATION: auto_retention_delete_enabled must be False")
    if getattr(settings, "operational_delete_enabled", False):
        raise PipelineSafetyError("SAFETY_VIOLATION: operational_delete_enabled must be False")


def run_bundesliga_ingest_pipeline(
    target_day: date,
    matchday: int | None = None,
    *,
    dry_run: bool | None = None,
    settings: Settings | None = None,
    session: Session | None = None,
    store: ObjectStore | None = None,
    provider: OpenLigaDBProvider | None = None,
) -> dict[str, Any]:
    cfg = settings or get_settings()
    check_pipeline_safety_gates(cfg)

    # Determine dry run mode: must be explicitly disabled and config must allow
    is_dry_run = dry_run if dry_run is not None else getattr(cfg, "bundesliga_ingest_dry_run", True)
    is_enabled = getattr(cfg, "bundesliga_ingest_enabled", False)

    if not is_dry_run and not is_enabled:
        raise PipelineSafetyError("PIPELINE_DISABLED: bundesliga_ingest_enabled must be True for live execution")

    # No production-shaped write may silently use the historical demo matchday
    # or omit archival storage. Keep these checks before any provider/SQL I/O.
    if not is_dry_run:
        if matchday is not None and (not isinstance(matchday, int) or isinstance(matchday, bool) or not 1 <= matchday <= 34):
            raise PipelineSafetyError("INVALID_MATCHDAY: Bundesliga matchday must be 1..34")
        # Automatic day scope is restricted to current or past dates, never to arbitrary future guesses.
        if matchday is None and not (date(2026, 7, 1) <= target_day <= min(date(2027, 6, 30), datetime.now(ISTANBUL).date())):
            raise PipelineSafetyError("INVALID_DAY_SCOPE: automatic daily ingest must be in the pilot season and not future-dated")
        if store is None:
            raise PipelineSafetyError("ARCHIVE_STORE_REQUIRED: SQL-only Bundesliga ingest is forbidden")

    if is_dry_run:
        from app.jobs.bundesliga_dryrun import run_bundesliga_dryrun
        return run_bundesliga_dryrun(target_day, matchday if matchday is not None else 10, settings=cfg, session=session, provider=provider)

    # Isolated or Live Execution with Session
    if session is None:
        raise ValueError("Database session required for non-dry-run execution")

    # Production runs must use one durable PostgreSQL quota ledger shared across
    # restarts and workers. SQLite in-memory remains isolated test-only.
    db_engine = session.get_bind()
    quota = (DurableQuotaTracker(cfg, db_engine)
             if db_engine.dialect.name == "postgresql" else get_quota_tracker(cfg))
    def reserve_request() -> None:
        if isinstance(quota, DurableQuotaTracker):
            quota.reserve_request()
        else:
            quota.check_request_quota(1)
            quota.record_request(1)

    def reserve_upload(size: int) -> None:
        if isinstance(quota, DurableQuotaTracker):
            quota.reserve_r2_write(1, size)
        else:
            quota.check_r2_write_quota(1, size)
            quota.record_r2_write(1, size)

    own_provider = provider is None
    p = provider or OpenLigaDBProvider(
        base_url=cfg.openligadb_base_url,
        timeout_seconds=cfg.openligadb_timeout_seconds,
        requests_per_minute=cfg.openligadb_requests_per_minute,
    )
    previous_callback = getattr(p, "before_request", None)

    report: dict[str, Any] = {
        "operation": "BUNDESLIGA_PIPELINE_EXECUTION",
        "dry_run": False,
        "target_day": target_day.isoformat(),
        "target_matchday": matchday,
        "scope_mode": "DAILY" if matchday is None else "MATCHDAY",
        "league": "de.bl1",
        "season": "2026-2027",
        "attribution": ATTRIBUTION,
        "inserted_matches": 0,
        "updated_matches": 0,
        "persisted_results": 0,
        "events_generated": [],
        "archive_receipt": None,
        "status": "COMPLETED",
        "errors": [],
    }

    lock = None
    checkpoint = None
    try:
        # Serialize live pipeline runs across runners while allowing checkpoint
        # and quota commits to survive a crash. Closing this connection releases the lock.
        if isinstance(quota, DurableQuotaTracker):
            lock = db_engine.connect()
            lock.execute(text("SELECT pg_advisory_xact_lock(:lock)"), {"lock": 57179413026025})

        if matchday is None:
            key = f"bundesliga:daily:2026-2027:{target_day.isoformat()}"
            checkpoint = session.get(SystemSetting, key)
            if checkpoint is not None and "completed_at" in checkpoint.value:
                completed = datetime.fromisoformat(checkpoint.value["completed_at"])
                if datetime.now(timezone.utc) - completed < timedelta(hours=2):
                    receipt = checkpoint.value.get("archive_receipt")
                    rows_json = checkpoint.value.get("archive_rows")
                    if receipt is None and rows_json == "[]":
                        report["reused_checkpoint"] = True
                        return report
                    # Do not trust a completed marker alone. A verified SQL
                    # receipt and an extant R2 object must both still exist.
                    if receipt and rows_json:
                        ledger = session.scalar(
                            select(ArchiveJob).where(ArchiveJob.r2_key == receipt.get("key"))
                        )
                        if (ledger is not None and ledger.status == "VERIFIED"
                                and ledger.verified_at is not None
                                and ledger.checksum == receipt.get("checksum")
                                and ledger.row_count == receipt.get("row_count")
                                and store.head_object(receipt["key"]) is not None):
                            report["archive_receipt"] = receipt
                            report["reused_checkpoint"] = True
                            return report
                    if not rows_json:
                        # Older incomplete checkpoint cannot be safely resumed
                        # without re-reading provider; never claim completion.
                        raise PipelineSafetyError("INCOMPLETE_CHECKPOINT_REVIEW_REQUIRED")
                    # Retain durable source rows for archive repair, not an
                    # unverified completed receipt.
                    checkpoint.value = {"archive_rows": rows_json}
                    session.commit()

        state = checkpoint.value if checkpoint is not None else {}
        if "archive_rows" in state:
            # JSONB canonicalizes object keys. Preserve the original column order
            # so a resumed archive produces the same Parquet bytes and key.
            archive_rows = json.loads(state["archive_rows"])
        else:
            if "matches" in state:
                unique_matches = state["matches"]
            else:
                # Reserve per HTTP attempt, including transport retries.
                if isinstance(p, OpenLigaDBProvider):
                    p.before_request = reserve_request
                else:
                    reserve_request()
                if matchday is None:
                    fetched = p.get_matches(target_day)
                else:
                    season_year = target_day.year if target_day.month >= 7 else target_day.year - 1
                    fetched = p.get_matchdata_by_group("bl1", season_year, matchday)
                unique_matches, _ = deduplicate_scope(fetched)
                if matchday is None:
                    # Store only fields needed to replay SQL, in JSON-compatible form.
                    unique_matches = [
                        {
                            "provider_match_id": str(m["provider_match_id"]),
                            "start_time": str(m["start_time"]),
                            "home_team": m["home_team"],
                            "away_team": m["away_team"],
                            "status": m["status"],
                            "round": m.get("round"),
                            "score": m.get("score"),
                        }
                        for m in unique_matches
                    ]
                    if checkpoint is None:
                        checkpoint = SystemSetting(key=key)
                        session.add(checkpoint)
                    checkpoint.value = {"matches": unique_matches}
                    session.commit()

            if unique_matches:
                if isinstance(quota, DurableQuotaTracker):
                    quota.reserve_matches(len(unique_matches))
                else:
                    quota.check_matches_quota(len(unique_matches))
                    quota.record_matches(len(unique_matches))

            now = datetime.now(timezone.utc)
            archive_rows: list[dict[str, Any]] = []
            for m in unique_matches:
                p_id = str(m["provider_match_id"])
                stmt = select(ActiveMatch).where(
                    (ActiveMatch.provider == "openligadb") &
                    (ActiveMatch.provider_match_id == p_id)
                )
                match_row = session.scalars(stmt).one_or_none()
                is_new = match_row is None

                start_dt = m["start_time"] if isinstance(m["start_time"], datetime) else datetime.fromisoformat(str(m["start_time"]))
                if start_dt.tzinfo is None:
                    start_dt = start_dt.replace(tzinfo=timezone.utc)

                expires_at = start_dt + timedelta(hours=cfg.operational_retention_hours)

                if is_new:
                    match_id = uuid4()
                    match_row = ActiveMatch(
                        id=match_id,
                        provider="openligadb",
                        provider_match_id=p_id,
                        league_id="de.bl1",
                        league_name="Bundesliga",
                        season="2026-2027",
                        home_team=m["home_team"],
                        away_team=m["away_team"],
                        start_time=start_dt,
                        status=m["status"],
                        round=m.get("round"),
                        country="Germany",
                        sport="football",
                        expires_at=expires_at,
                    )
                    session.add(match_row)
                    report["inserted_matches"] += 1

                    # Generate MATCH_SCHEDULED notification contract
                    event = create_match_event(
                        event_type=MatchEventType.MATCH_SCHEDULED,
                        match_id=match_id,
                        provider_match_id=p_id,
                        home_team=m["home_team"],
                        away_team=m["away_team"],
                        start_time=start_dt.isoformat(),
                        status="SCHEDULED",
                        occurred_at=now.isoformat(),
                    )
                    report["events_generated"].append(event.model_dump())
                else:
                    # Update existing match: preserve UUID!
                    match_id = match_row.id
                    match_row.status = m["status"]
                    match_row.start_time = start_dt
                    match_row.home_team = m["home_team"]
                    match_row.away_team = m["away_team"]
                    match_row.round = m.get("round")
                    match_row.expires_at = expires_at
                    report["updated_matches"] += 1

                # Check if match is finished and has verified score
                if m["status"] == "FINISHED" and m.get("score") is not None:
                    score = m["score"]
                    res_stmt = select(MatchResult).where(MatchResult.match_id == match_id).with_for_update()
                    res_row = session.scalars(res_stmt).one_or_none()

                    if res_row is None:
                        res_row = MatchResult(
                            id=uuid4(),
                            match_id=match_id,
                            provider="openligadb",
                            provider_result_id=f"openligadb-{p_id}",
                            home_score=score[0],
                            away_score=score[1],
                            status="FINAL",
                            finished_at=start_dt + timedelta(minutes=105),
                            result_source=f"{ATTRIBUTION}; {LICENCE_URL}",
                            verified_at=None,  # Single-source final observation is not independently verified.
                        )
                        session.add(res_row)
                        report["persisted_results"] += 1
                    else:
                        # Never silently replace a score that another verification
                        # workflow has already certified.
                        if (res_row.verified_at is not None and
                                (res_row.home_score, res_row.away_score) != tuple(score)):
                            raise PipelineSafetyError("VERIFIED_RESULT_CONFLICT: manual review required")
                        if res_row.verified_at is None:
                            res_row.home_score = score[0]
                            res_row.away_score = score[1]
                            res_row.status = "FINAL"
                    # No RESULT_VERIFIED event: this source is only one observation.
                    # Verification is performed by the separate corroboration workflow.

                # Row for Parquet archival
                archive_rows.append({
                    "match_id": str(match_id),
                    "provider": "openligadb",
                    "provider_match_id": p_id,
                    "league_id": "de.bl1",
                    "season": "2026-2027",
                    "home_team": m["home_team"],
                    "away_team": m["away_team"],
                    "start_time": start_dt.isoformat(),
                    "status": m["status"],
                    "home_score": (m.get("score") or [None, None])[0],
                    "away_score": (m.get("score") or [None, None])[1],
                    "attribution": ATTRIBUTION,
                    "licence": LICENCE,
                })

            if checkpoint is not None:
                checkpoint.value = {"archive_rows": json.dumps(archive_rows)}
            session.commit()

        # SQL remains available while the archive is deferred or repaired.
        if archive_rows:
            archive_revision = hashlib.sha256(parquet_bytes(archive_rows)).hexdigest()[:16]
            receipt = archive_partition(
                store,
                tracker=SQLArchiveTracker(session),
                day=target_day,
                dataset="matches",
                season="2026-2027",
                league="de.bl1",
                rows=archive_rows,
                before_upload=reserve_upload,
                part=((f"openligadb-bundesliga-day{target_day:%Y%m%d}" if matchday is None
                       else f"openligadb-bundesliga-md{matchday}")
                      + f"-sha{archive_revision}"),
            )
            report["archive_receipt"] = {
                "key": receipt.key,
                "size": receipt.size,
                "row_count": receipt.row_count,
                "checksum": receipt.checksum,
            }

        if checkpoint is not None:
            checkpoint.value = {
                "completed_at": datetime.now(timezone.utc).isoformat(),
                "archive_receipt": report["archive_receipt"],
                "archive_rows": json.dumps(archive_rows),
            }
            session.commit()

    except QuotaExceededError as exc:
        session.rollback()
        report["status"] = "QUOTA_DEFERRED"
        report["resume_date"] = target_day.isoformat()
        report["retry_after_utc"] = (
            datetime.now(timezone.utc).date() + timedelta(days=1)
        ).isoformat() + "T00:00:00Z"
        report["errors"].append(str(exc))
    except Exception as exc:
        session.rollback()
        report["status"] = "FAILED"
        report["errors"].append(str(exc))
        raise
    finally:
        if lock is not None:
            lock.close()
        if isinstance(p, OpenLigaDBProvider):
            p.before_request = previous_callback
        if own_provider:
            p.close()
        report["quota_summary"] = quota.get_summary()

    return report
