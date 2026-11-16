"""Provider-to-active-state ingestion and verified R2 historical backfill."""
from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone
from typing import Any, Callable

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.models import ActiveMatch, ActiveOdds, ArchiveJob, MatchResult
from app.providers.base import ProviderError, ProviderFailure, ProviderNotConfigured
from app.providers.normalize import normalize_matches, normalize_odds, normalize_result
from app.storage.archive import ArchiveStatus, SQLArchiveTracker, archive_partition, cleanup_database_rows_after_verified
from app.storage.r2 import ObjectStore


def _expires(start_time: datetime, days: int) -> datetime:
    return max(start_time + timedelta(days=days), datetime.now(timezone.utc) + timedelta(hours=1))


def _safe_status(status: str) -> str:
    allowed = {"SCHEDULED", "LIVE", "IN_PLAY", "PAUSED", "FINISHED", "POSTPONED", "CANCELLED", "SUSPENDED"}
    return status if status in allowed else "UNKNOWN"


def upsert_matches(session: Session, matches: list[dict], *, now: datetime | None = None) -> tuple[int, int]:
    current = now or datetime.now(timezone.utc)
    retention = get_settings().active_state_retention_days
    written = skipped = 0
    for match in matches:
        if match["start_time"] < current - timedelta(days=retention):
            skipped += 1
            continue
        # Provider adapters may attach useful discovery metadata (for example,
        # a provider season ID) which is not part of the active SQL schema.
        # Keep only mapped columns at this persistence boundary.
        columns = {column.name for column in ActiveMatch.__table__.columns}
        values = {key: value for key, value in match.items() if key in columns}
        values.update({"status": _safe_status(match["status"]),
                       "expires_at": _expires(match["start_time"], retention)})
        statement = insert(ActiveMatch).values(**values)
        updates = {key: getattr(statement.excluded, key) for key in
                   ("league_id", "league_name", "season", "home_team", "away_team", "start_time", "status", "venue", "round", "country", "sport", "expires_at")}
        statement = statement.on_conflict_do_update(
            index_elements=[ActiveMatch.provider, ActiveMatch.provider_match_id],
            set_=updates,
        )
        session.execute(statement)
        written += 1
    session.flush()
    return written, skipped


def upsert_odds(session: Session, provider: str, match: ActiveMatch, payloads: list[dict], *, now: datetime | None = None) -> tuple[int, int]:
    current = now or datetime.now(timezone.utc)
    retention = get_settings().odds_retention_days
    written = skipped = 0
    for payload in payloads:
        odds = normalize_odds(payload, match_id=str(match.id), provider=provider)
        if odds["captured_at"] > current + timedelta(minutes=5):
            skipped += 1
            continue
        values = {**odds, "match_id": match.id, "expires_at": max(odds["captured_at"] + timedelta(days=retention), current + timedelta(hours=1))}
        statement = insert(ActiveOdds).values(**values).on_conflict_do_nothing(
            index_elements=[ActiveOdds.match_id, ActiveOdds.bookmaker, ActiveOdds.market, ActiveOdds.selection, ActiveOdds.captured_at]
        )
        result = session.execute(statement)
        written += result.rowcount or 0
        skipped += 0 if result.rowcount else 1
    return written, skipped


def daily_ingest(session: Session, caller: Any, day: date, *, enabled_leagues: tuple[str, ...],
                 now: datetime | None = None) -> dict[str, Any]:
    provider = caller.provider
    if not enabled_leagues:
        raise ProviderNotConfigured("PROVIDER_NOT_CONFIGURED: ENABLED_LEAGUES is empty")
    available = caller.call("get_leagues")
    available_ids = {str(item.get("id", item.get("league_id", ""))) for item in available}
    leagues = [league for league in enabled_leagues if league in available_ids]
    if not leagues:
        return {"status": "SKIPPED", "error_code": "NO_ENABLED_LEAGUES_AVAILABLE", "records_seen": len(available),
                "records_written": 0, "records_skipped": 0, "records_failed": 0}
    raw_matches = caller.call("get_matches", day)
    normalized = [m for m in normalize_matches(raw_matches, provider=provider.name)
                  if m["league_id"] in leagues]
    written, skipped = upsert_matches(session, normalized, now=now)
    odds_written = odds_skipped = failures = 0
    match_start = datetime.combine(day, time.min, tzinfo=timezone.utc)
    match_end = match_start + timedelta(days=1)
    matches = session.scalars(select(ActiveMatch).where(
        ActiveMatch.provider == provider.name,
        ActiveMatch.start_time >= match_start,
        ActiveMatch.start_time < match_end,
        ActiveMatch.league_id.in_(leagues),
    )).all()
    for match in matches:
        try:
            payloads = caller.call("get_odds", match.provider_match_id)
            count, ignored = upsert_odds(session, provider.name, match, payloads, now=now)
            odds_written += count
            odds_skipped += ignored
        except ProviderFailure:
            failures += 1
        except (KeyError, TypeError, ValueError) as exc:
            failures += 1
    session.commit()
    total_written = written + odds_written
    total_skipped = skipped + odds_skipped
    status = "PARTIAL" if failures else "SUCCESS" if total_written else "SKIPPED"
    return {"status": status, "records_seen": len(raw_matches), "records_written": total_written,
            "records_skipped": total_skipped, "records_failed": failures,
            "error_code": "PROVIDER_PARTIAL_FAILURE" if failures else None}


def daily_odds(session: Session, caller: Any, day: date, *, enabled_leagues: tuple[str, ...],
               now: datetime | None = None) -> dict[str, Any]:
    current = now or datetime.now(timezone.utc)
    start = datetime.combine(day, time.min, tzinfo=timezone.utc)
    end = start + timedelta(days=1)
    matches = session.scalars(select(ActiveMatch).where(
        ActiveMatch.provider == caller.provider.name,
        ActiveMatch.start_time >= start, ActiveMatch.start_time < end,
        ActiveMatch.expires_at > current,
        ActiveMatch.league_id.in_(enabled_leagues),
    )).all()
    written = skipped = failed = seen = 0
    for match in matches:
        try:
            payloads = caller.call("get_odds", match.provider_match_id)
            seen += len(payloads)
            try:
                count, ignored = upsert_odds(session, caller.provider.name, match, payloads, now=current)
            except (KeyError, TypeError, ValueError) as exc:
                raise ProviderError("PROVIDER_ERROR: invalid odds response") from exc
            written += count
            skipped += ignored
        except ProviderFailure:
            failed += 1
    session.commit()
    return {"status": "PARTIAL" if failed and written else "FAILED" if failed else "SUCCESS" if written else "SKIPPED",
            "records_seen": seen, "records_written": written, "records_skipped": skipped,
            "records_failed": failed, "error_code": "PROVIDER_PARTIAL_FAILURE" if failed else None}

def archive_rows(store: ObjectStore, tracker: Any, *, dataset: str, season: str, league: str, day: date,
                 rows: list[dict]) -> dict[str, Any]:
    if not rows:
        return {"status": "SKIPPED", "records_written": 0, "reason": "NO_ROWS"}
    receipt = archive_partition(store, dataset=dataset, season=season, league=league, day=day, rows=rows, tracker=tracker)
    return {"status": "SUCCESS", "records_written": receipt.row_count, "key": receipt.key,
            "size_bytes": receipt.size, "checksum": receipt.checksum}


def historical_ingest(session: Session, caller: Any, store: ObjectStore, *, season: str, league: str,
                      start_date: date, end_date: date) -> dict[str, Any]:
    from app.models import HistoricalIngestDay
    if end_date < start_date:
        raise ValueError("end_date must be on or after start_date")
    configured_start = get_settings().historical_start_date
    if configured_start and start_date < configured_start:
        raise ValueError("start_date precedes HISTORICAL_START_DATE")
    if league not in get_settings().enabled_league_ids or season not in get_settings().enabled_season_ids:
        raise ValueError("season and league must be present in ENABLED_SEASONS and ENABLED_LEAGUES")
    provider_leagues = caller.call("get_leagues")
    available = {str(item.get("id", item.get("league_id", ""))) for item in provider_leagues}
    if league not in available:
        return {"status": "SKIPPED", "error_code": "LEAGUE_NOT_AVAILABLE", "records_seen": 0,
                "records_written": 0, "records_skipped": 0, "records_failed": 0, "dates_completed": 0}
    completed = failed = seen = archived = 0
    tracker = SQLArchiveTracker(session)
    day = start_date
    while day <= end_date:
        progress = session.scalar(select(HistoricalIngestDay).where(
            HistoricalIngestDay.provider == caller.provider.name,
            HistoricalIngestDay.season == season,
            HistoricalIngestDay.league_id == league,
            HistoricalIngestDay.ingest_date == day,
        ).with_for_update())
        if progress and progress.status in {"COMPLETED", "EMPTY"}:
            completed += 1
            day += timedelta(days=1)
            continue
        if progress is None:
            progress = HistoricalIngestDay(provider=caller.provider.name, season=season,
                                           league_id=league, ingest_date=day, status="RUNNING")
            session.add(progress)
        else:
            progress.status, progress.error_code = "RUNNING", None
        session.commit()
        try:
            raw = caller.call("get_matches", day)
            normalized = [row for row in normalize_matches(raw, provider=caller.provider.name)
                          if row["league_id"] == league]
            seen += len(raw)
            archive_count = 0
            if normalized:
                for match_row in normalized:
                    match_row["season"] = season
                archive_rows(store, tracker, dataset="matches", season=season, league=league, day=day, rows=normalized)
                archive_count += len(normalized)
            raw_results = caller.call("get_results", day)
            final_results = [normalize_result(item, provider=caller.provider.name) for item in raw_results]
            final_results = [item for item in final_results if item["status"] == "FINAL"
                             and any(match["provider_match_id"] == item["provider_match_id"] for match in normalized)]
            if final_results:
                for item in final_results:
                    item.update({"league_id": league, "season": season})
                archive_rows(store, tracker, dataset="results", season=season, league=league,
                             day=day, rows=final_results)
                archive_count += len(final_results)
            archived += archive_count
            progress.records_seen = len(raw)
            progress.records_archived = archive_count
            progress.status = "COMPLETED" if archive_count else "EMPTY"
            session.commit()
            completed += 1
        except Exception as exc:
            session.rollback()
            progress = session.scalar(select(HistoricalIngestDay).where(
                HistoricalIngestDay.provider == caller.provider.name,
                HistoricalIngestDay.season == season,
                HistoricalIngestDay.league_id == league,
                HistoricalIngestDay.ingest_date == day,
            ))
            if progress:
                progress.status = "FAILED"
                progress.error_code = getattr(exc, "code", "PROVIDER_ERROR" if isinstance(exc, (KeyError, TypeError, ValueError)) else type(exc).__name__.upper())[:80]
                session.commit()
            failed += 1
        day += timedelta(days=1)
    status = "PARTIAL" if failed and completed else "FAILED" if failed else "SUCCESS" if archived else "SKIPPED"
    return {"status": status, "records_seen": seen, "records_written": archived,
            "records_skipped": completed if not archived else max(0, completed - 1),
            "records_failed": failed, "dates_completed": completed, "dates_failed": failed,
            "odds_history": "UNAVAILABLE_UNLESS_PROVIDER_ADAPTER_EXPOSES_HISTORICAL_ODDS"}
def ingest_results(session: Session, caller: Any, day: date) -> dict[str, Any]:
    raw = caller.call("get_results", day)
    try:
        results = [normalize_result(item, provider=caller.provider.name) for item in raw]
    except (KeyError, TypeError, ValueError) as exc:
        raise ProviderError("PROVIDER_ERROR: invalid result response") from exc
    written = skipped = 0
    for value in results:
        match = session.scalar(select(ActiveMatch).where(
            ActiveMatch.provider == value["provider"],
            ActiveMatch.provider_match_id == value["provider_match_id"],
        ))
        if match is None or value["status"] != "FINAL":
            skipped += 1
            continue
        result = session.scalar(select(MatchResult).where(MatchResult.match_id == match.id).with_for_update())
        if result and result.status == "FINAL":
            skipped += 1
            continue
        if result is None:
            result = MatchResult(match_id=match.id, provider=value["provider"],
                                 provider_result_id=value["provider_match_id"],
                                 result_source=value["result_source"])
            session.add(result)
        result.provider = value["provider"]
        result.home_score, result.away_score = value["home_score"], value["away_score"]
        result.status, result.finished_at = "FINAL", value["finished_at"]
        match.status = "FINISHED"
        result.result_source, result.verified_at = value["result_source"], datetime.now(timezone.utc)
        written += 1
    session.commit()
    return {"status": "SUCCESS" if written else "SKIPPED", "records_seen": len(raw),
            "records_written": written, "records_skipped": skipped, "records_failed": 0}


def archive_expired_active_state(session: Session, store: ObjectStore, *, now: datetime | None = None) -> dict[str, int]:
    """Archive expired odds and finalized outcomes in daily, league-scoped Parquet partitions."""
    from collections import defaultdict
    from decimal import Decimal
    from uuid import UUID
    from app.models import Prediction

    current = now or datetime.now(timezone.utc)
    cutoff = current - timedelta(days=1)
    partitions: dict[tuple[str, str, str, date], list[dict]] = defaultdict(list)

    def canonical(row: dict) -> dict:
        return {key: str(value) if isinstance(value, UUID) else float(value) if isinstance(value, Decimal) else value
                for key, value in row.items()}

    odds_rows = session.execute(select(ActiveOdds, ActiveMatch).join(
        ActiveMatch, ActiveMatch.id == ActiveOdds.match_id
    ).where(ActiveOdds.expires_at <= current, ActiveOdds.captured_at < cutoff)).all()
    for odds, match in odds_rows:
        row = {"match_id": str(match.id), "provider": odds.provider, "provider_odds_id": odds.provider_odds_id,
               "provider_market_id": odds.provider_market_id, "provider_bookmaker_id": odds.provider_bookmaker_id,
               "league_id": match.league_id, "league_name": match.league_name, "season": match.season,
               "home_team": match.home_team, "away_team": match.away_team, "start_time": match.start_time,
               "bookmaker": odds.bookmaker, "market": odds.market, "selection": odds.selection,
               "odds": float(odds.odds), "captured_at": odds.captured_at}
        partitions[("odds", match.season or "unknown", match.league_id or "unknown", odds.captured_at.date())].append(row)

    result_rows = session.execute(select(MatchResult, ActiveMatch).join(
        ActiveMatch, ActiveMatch.id == MatchResult.match_id
    ).where(MatchResult.status == "FINAL", MatchResult.verified_at.is_not(None),
            func.coalesce(MatchResult.finished_at, MatchResult.verified_at) < cutoff)).all()
    for result, match in result_rows:
        row = {"match_id": str(match.id), "provider": result.provider, "provider_result_id": result.provider_result_id,
               "league_id": match.league_id, "league_name": match.league_name, "season": match.season,
               "home_team": match.home_team, "away_team": match.away_team, "start_time": match.start_time,
               "home_score": result.home_score, "away_score": result.away_score, "status": result.status,
               "finished_at": result.finished_at, "verified_at": result.verified_at, "result_source": result.result_source}
        partitions[("results", match.season or "unknown", match.league_id or "unknown", (result.finished_at or result.verified_at).date())].append(row)

    prediction_rows = session.execute(select(Prediction, ActiveMatch).join(
        ActiveMatch, ActiveMatch.id == Prediction.match_id
    ).where(Prediction.status.in_(("WON", "LOST")), Prediction.settled_at.is_not(None),
            Prediction.expires_at <= current, Prediction.created_at < cutoff)).all()
    for prediction, match in prediction_rows:
        row = {"prediction_id": str(prediction.id), "match_id": str(match.id), "provider": match.provider,
               "provider_match_id": match.provider_match_id, "league_id": match.league_id,
               "league_name": match.league_name, "season": match.season,
               "home_team": match.home_team, "away_team": match.away_team, "start_time": match.start_time,
               "market": prediction.market, "selection": prediction.selection,
               "probability": float(prediction.probability), "confidence": float(prediction.confidence),
               "decimal_odds": float(prediction.decimal_odds) if prediction.decimal_odds else None,
               "paper_stake": float(prediction.paper_stake), "paper_return": float(prediction.paper_return or 0),
               "status": prediction.status, "created_at": prediction.created_at, "settled_at": prediction.settled_at}
        partitions[("predictions", match.season or "unknown", match.league_id or "unknown",
                    prediction.created_at.date())].append(row)

    counts = {"odds": 0, "results": 0, "predictions": 0}
    tracker = SQLArchiveTracker(session)
    for (dataset, season, league, day), rows in sorted(partitions.items()):
        archive_rows(store, tracker, dataset=dataset, season=season, league=league, day=day,
                     rows=[canonical(row) for row in rows])
        counts[dataset] += len(rows)
    return counts

def cleanup_verified_archives(session: Session, store: ObjectStore, *, today: date | None = None) -> int:
    """Re-read and verify R2 bytes before deleting old source partitions."""
    from sqlalchemy import delete, func
    from app.models import CouponItem, Prediction
    from app.storage.archive import _verify_object

    cutoff = (today or datetime.now(timezone.utc).date()) - timedelta(days=1)
    jobs = session.scalars(select(ArchiveJob).where(
        ArchiveJob.status == ArchiveStatus.VERIFIED.value,
        ArchiveJob.archive_date < cutoff,
        ArchiveJob.checksum.is_not(None),
        ArchiveJob.verified_at.is_not(None),
        ArchiveJob.row_count > 0,
        ArchiveJob.file_size_bytes.is_not(None),
    ).order_by(ArchiveJob.archive_date).with_for_update(skip_locked=True)).all()
    count = 0
    for job in jobs:
        try:
            parts = job.r2_key.split("/")
            date_segment = f"date={job.archive_date.isoformat()}.parquet"
            league_segment = f"league={job.league}" if job.league else None
            if (not parts or parts[0] != job.dataset_type or parts[-1] != date_segment
                    or not any(part.startswith("season=") for part in parts)
                    or (league_segment and league_segment not in parts)):
                continue
            _verify_object(store, job.r2_key, expected_size=job.file_size_bytes,
                           expected_rows=job.row_count, expected_checksum=job.checksum)
        except Exception:
            continue

        day_start = datetime.combine(job.archive_date, time.min, tzinfo=timezone.utc)
        day_end = day_start + timedelta(days=1)
        if job.dataset_type == "odds":
            query = delete(ActiveOdds).where(ActiveOdds.captured_at >= day_start, ActiveOdds.captured_at < day_end)
            if job.league:
                query = query.where(ActiveOdds.match_id.in_(
                    select(ActiveMatch.id).where(ActiveMatch.league_id == job.league)))
            session.execute(query)
        elif job.dataset_type == "results":
            query = delete(MatchResult).where(func.coalesce(MatchResult.finished_at, MatchResult.verified_at) >= day_start, func.coalesce(MatchResult.finished_at, MatchResult.verified_at) < day_end,
                                               MatchResult.status == "FINAL", MatchResult.verified_at.is_not(None))
            session.execute(query)
        elif job.dataset_type == "predictions":
            query = delete(Prediction).where(Prediction.created_at >= day_start, Prediction.created_at < day_end,
                                              Prediction.status.in_(("WON", "LOST")))
            if session.scalar(select(func.count()).select_from(CouponItem).where(CouponItem.prediction_id.in_(
                select(Prediction.id).where(Prediction.created_at >= day_start, Prediction.created_at < day_end)
            ))):
                continue
            session.execute(query)
        elif job.dataset_type == "matches":
            # ActiveMatch has cascading foreign keys. Retain it while any dependent operational
            # record remains, so a match archive can never silently erase unarchived odds/results.
            match_ids = select(ActiveMatch.id).where(ActiveMatch.start_time >= day_start, ActiveMatch.start_time < day_end)
            if job.league:
                match_ids = match_ids.where(ActiveMatch.league_id == job.league)
            dependent = (
                session.scalar(select(func.count()).select_from(ActiveOdds).where(ActiveOdds.match_id.in_(match_ids))) or
                session.scalar(select(func.count()).select_from(MatchResult).where(MatchResult.match_id.in_(match_ids))) or
                session.scalar(select(func.count()).select_from(Prediction).where(Prediction.match_id.in_(match_ids))) or
                session.scalar(select(func.count()).select_from(CouponItem).where(CouponItem.match_id.in_(match_ids)))
            )
            if dependent:
                continue
            session.execute(delete(ActiveMatch).where(ActiveMatch.id.in_(match_ids)))
        else:
            continue

        job.status = ArchiveStatus.DELETED.value
        job.deleted_at = datetime.now(timezone.utc)
        session.commit()
        count += 1
    return count
