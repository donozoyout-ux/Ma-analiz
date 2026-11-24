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
from typing import Any
from uuid import UUID, uuid4
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import Settings, get_settings
from app.models import ActiveMatch, MatchResult
from app.notifications.events import (
    MatchEventPayload,
    MatchEventType,
    create_match_event,
)
from app.providers.identity import deduplicate_scope, fixture_key
from app.providers.openligadb import ATTRIBUTION, LICENCE, LICENCE_URL, OpenLigaDBProvider
from app.storage.archive import SQLArchiveTracker, archive_partition
from app.storage.quota import QuotaExceededError, get_quota_tracker
from app.storage.r2 import ObjectStore

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
        if matchday is None or not isinstance(matchday, int) or isinstance(matchday, bool) or not 1 <= matchday <= 34:
            raise PipelineSafetyError("MATCHDAY_REQUIRED: explicit Bundesliga matchday 1..34 is required")
        if store is None:
            raise PipelineSafetyError("ARCHIVE_STORE_REQUIRED: SQL-only Bundesliga ingest is forbidden")

    if is_dry_run:
        from app.jobs.bundesliga_dryrun import run_bundesliga_dryrun
        return run_bundesliga_dryrun(target_day, matchday if matchday is not None else 10, settings=cfg, session=session, provider=provider)

    # Isolated or Live Execution with Session
    if session is None:
        raise ValueError("Database session required for non-dry-run execution")

    quota = get_quota_tracker(cfg)
    own_provider = provider is None
    p = provider or OpenLigaDBProvider(
        base_url=getattr(cfg, "openligadb_base_url", "https://api.openligadb.de"),
        timeout_seconds=getattr(cfg, "openligadb_timeout_seconds", 10.0),
        requests_per_minute=getattr(cfg, "openligadb_requests_per_minute", 1),
    )

    report: dict[str, Any] = {
        "operation": "BUNDESLIGA_PIPELINE_EXECUTION",
        "dry_run": False,
        "target_day": target_day.isoformat(),
        "target_matchday": matchday,
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

    try:
        quota.check_request_quota(1)
        quota.record_request(1)

        season_year = target_day.year if target_day.month >= 7 else target_day.year - 1
        fetched = p.get_matchdata_by_group("bl1", season_year, matchday)
        unique_matches, _ = deduplicate_scope(fetched)

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

            expires_at = start_dt + timedelta(hours=48)

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
                res_stmt = select(MatchResult).where(MatchResult.match_id == match_id)
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
                        verified_at=now,
                    )
                    session.add(res_row)
                    report["persisted_results"] += 1
                else:
                    res_row.home_score = score[0]
                    res_row.away_score = score[1]
                    res_row.status = "FINAL"
                    res_row.verified_at = now

                # Generate RESULT_VERIFIED notification contract
                event = create_match_event(
                    event_type=MatchEventType.RESULT_VERIFIED,
                    match_id=match_id,
                    provider_match_id=p_id,
                    home_team=m["home_team"],
                    away_team=m["away_team"],
                    start_time=start_dt.isoformat(),
                    status="FINAL",
                    home_score=score[0],
                    away_score=score[1],
                    occurred_at=now.isoformat(),
                )
                report["events_generated"].append(event.model_dump())

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

        session.commit()

        # R2 Archival if store provided
        if store is not None and archive_rows:
            quota.check_r2_write_quota(1, len(archive_rows) * 100)
            receipt = archive_partition(
                store,
                tracker=SQLArchiveTracker(session),
                day=target_day,
                dataset="matches",
                season="2026-2027",
                league="de.bl1",
                rows=archive_rows,
                part=f"openligadb-bundesliga-md{matchday}",
            )
            quota.record_r2_write(1, receipt.size)
            report["archive_receipt"] = {
                "key": receipt.key,
                "size": receipt.size,
                "row_count": receipt.row_count,
                "checksum": receipt.checksum,
            }

    except QuotaExceededError as exc:
        session.rollback()
        report["status"] = "QUOTA_EXCEEDED"
        report["errors"].append(str(exc))
    except Exception as exc:
        session.rollback()
        report["status"] = "FAILED"
        report["errors"].append(str(exc))
        raise
    finally:
        report["quota_summary"] = quota.get_summary()
        if own_provider:
            p.close()

    return report
