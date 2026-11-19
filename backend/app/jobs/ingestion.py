"""Provider-to-active-state ingestion and verified R2 historical backfill."""
from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone
from typing import Any, Callable
from uuid import uuid4

from sqlalchemy import func, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.models import ActiveMatch, ActiveOdds, ArchiveJob, Bookmaker, HistoricalIngestDay, MatchResult
from app.providers.base import ProviderError, ProviderFailure, ProviderNotConfigured
from app.providers.normalize import normalize_match, normalize_matches, normalize_odds, normalize_result
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
    bookmaker_cache: dict[tuple[str, str], Bookmaker] = {}
    for payload in payloads:
        odds = normalize_odds(payload, match_id=str(match.id), provider=provider)
        if odds["captured_at"] > current + timedelta(minutes=5):
            skipped += 1
            continue
        bookmaker_key = (odds["provider"], odds["bookmaker"])
        bookmaker = bookmaker_cache.get(bookmaker_key)
        if bookmaker is None:
            bookmaker = session.scalar(select(Bookmaker).where(
                Bookmaker.provider == odds["provider"], Bookmaker.name == odds["bookmaker"]))
        if bookmaker is None:
            bookmaker = Bookmaker(provider=odds["provider"],
                                  provider_bookmaker_id=odds.get("provider_bookmaker_id"),
                                  name=odds["bookmaker"], source=odds["source"],
                                  country=odds.get("bookmaker_country"), active=True)
            session.add(bookmaker)
            session.flush()
        else:
            if odds.get("provider_bookmaker_id") and not bookmaker.provider_bookmaker_id:
                bookmaker.provider_bookmaker_id = odds["provider_bookmaker_id"]
            if odds.get("bookmaker_country") and not bookmaker.country:
                bookmaker.country = odds["bookmaker_country"]
        bookmaker_cache[bookmaker_key] = bookmaker
        columns = {column.name for column in ActiveOdds.__table__.columns}
        values = {key: value for key, value in odds.items() if key in columns and key != "price"}
        values.update({"match_id": match.id, "bookmaker_entity_id": bookmaker.id,
                       "expires_at": max(odds["captured_at"] + timedelta(days=retention), current + timedelta(hours=1))})
        duplicate = session.scalar(select(ActiveOdds).where(
            ActiveOdds.match_id == match.id, ActiveOdds.bookmaker == odds["bookmaker"],
            ActiveOdds.market == odds["market"], ActiveOdds.selection == odds["selection"],
            ActiveOdds.line_key == odds["line_key"], ActiveOdds.snapshot_type == odds["snapshot_type"],
        ).order_by(ActiveOdds.captured_at.desc()).limit(1))
        if duplicate is not None and duplicate.odds == odds["odds"] and duplicate.period == odds["period"]:
            skipped += 1
            continue
        statement = insert(ActiveOdds).values(**values).on_conflict_do_nothing(
            index_elements=[ActiveOdds.match_id, ActiveOdds.bookmaker, ActiveOdds.market,
                            ActiveOdds.selection, ActiveOdds.line_key, ActiveOdds.snapshot_type, ActiveOdds.captured_at]
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
    session.commit()
    return {"status": "SUCCESS" if written else "SKIPPED", "records_seen": len(raw_matches),
            "records_written": written, "records_skipped": skipped, "records_failed": 0}


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


def _claim_historical_day(session: Session, *, provider: str, season: str, league: str,
                          day: date) -> tuple[HistoricalIngestDay | None, str | None]:
    now = datetime.now(timezone.utc)
    lease_seconds = max(60, get_settings().historical_ingest_lease_seconds)
    lease_expiry = now + timedelta(seconds=lease_seconds)
    token = uuid4().hex
    row = session.scalar(select(HistoricalIngestDay).where(
        HistoricalIngestDay.provider == provider,
        HistoricalIngestDay.season == season,
        HistoricalIngestDay.league_id == league,
        HistoricalIngestDay.ingest_date == day,
    ).with_for_update())
    if row is None:
        row = HistoricalIngestDay(provider=provider, season=season, league_id=league, ingest_date=day,
                                  status="RUNNING", lease_token=token, lease_expires_at=lease_expiry,
                                  attempt_count=1, last_attempt_at=now)
        session.add(row)
        try:
            session.commit()
        except IntegrityError:
            session.rollback()
            row = session.scalar(select(HistoricalIngestDay).where(
                HistoricalIngestDay.provider == provider,
                HistoricalIngestDay.season == season,
                HistoricalIngestDay.league_id == league,
                HistoricalIngestDay.ingest_date == day,
            ).with_for_update())
            if row is None:
                raise
        else:
            return row, token

    if row.status in {"COMPLETED", "EMPTY"}:
        session.rollback()
        return row, None

    active_until = row.lease_expires_at
    if active_until is not None and active_until.tzinfo is None:
        active_until = active_until.replace(tzinfo=timezone.utc)
    updated_at = row.updated_at
    if updated_at is not None and updated_at.tzinfo is None:
        updated_at = updated_at.replace(tzinfo=timezone.utc)
    if row.status == "RUNNING" and active_until is None and updated_at is not None:
        active_until = updated_at + timedelta(seconds=lease_seconds)
    if row.status == "RUNNING" and active_until is not None and active_until > now:
        session.rollback()
        return row, None

    row.status = "RUNNING"
    row.lease_token = token
    row.lease_expires_at = lease_expiry
    row.error_code = None
    row.failed_match_ids = []
    row.records_failed = 0
    row.attempt_count = (row.attempt_count or 0) + 1
    row.last_attempt_at = now
    session.commit()
    return row, token


def _renew_historical_lease(session: Session, *, progress_id: Any, token: str) -> None:
    now = datetime.now(timezone.utc)
    expiry = now + timedelta(seconds=max(60, get_settings().historical_ingest_lease_seconds))
    result = session.execute(update(HistoricalIngestDay).where(
        HistoricalIngestDay.id == progress_id,
        HistoricalIngestDay.status == "RUNNING",
        HistoricalIngestDay.lease_token == token,
    ).values(lease_expires_at=expiry, updated_at=now))
    session.commit()
    if result.rowcount != 1:
        raise RuntimeError("HISTORICAL_INGEST_LEASE_LOST")


def _finish_historical_day(session: Session, *, progress_id: Any, token: str,
                           values: dict[str, Any]) -> None:
    result = session.execute(update(HistoricalIngestDay).where(
        HistoricalIngestDay.id == progress_id,
        HistoricalIngestDay.status == "RUNNING",
        HistoricalIngestDay.lease_token == token,
    ).values(**values, lease_token=None, lease_expires_at=None, updated_at=datetime.now(timezone.utc)))
    session.commit()
    if result.rowcount != 1:
        raise RuntimeError("HISTORICAL_INGEST_LEASE_LOST")


def historical_ingest(session: Session, caller: Any, store: ObjectStore, *, season: str, league: str,
                      start_date: date, end_date: date) -> dict[str, Any]:
    if end_date < start_date:
        raise ValueError("end_date must be on or after start_date")
    configured_start = get_settings().historical_start_date
    if configured_start and start_date < configured_start:
        raise ValueError("start_date precedes HISTORICAL_START_DATE")
    if league not in get_settings().enabled_league_ids or season not in get_settings().enabled_season_ids:
        raise ValueError("season and league must be present in ENABLED_SEASONS and ENABLED_LEAGUES")
    from app.storage.monitor import StorageMonitor
    storage_status = StorageMonitor(session.get_bind(), get_settings()).historical_ingestion_status()
    if storage_status == "critical":
        return {"status": "BLOCKED", "error_code": "STORAGE_CRITICAL", "storage_status": storage_status,
                "records_seen": 0, "records_written": 0, "records_skipped": 0, "records_failed": 0,
                "dates_completed": 0, "dates_partial": 0, "dates_failed": 0, "dates_skipped": 0,
                "matches_written": 0, "results_written": 0, "odds_written": 0, "duplicates": 0,
                "invalid_rows": 0, "r2_objects": 0, "r2_bytes": 0,
                "postgres_rows_written": 0, "postgres_rows_deleted": 0}
    if storage_status == "DATABASE_STORAGE_CHECK_FAILED":
        return {"status": "BLOCKED", "error_code": storage_status, "storage_status": storage_status,
                "records_seen": 0, "records_written": 0, "records_skipped": 0, "records_failed": 0,
                "dates_completed": 0, "dates_partial": 0, "dates_failed": 0, "dates_skipped": 0,
                "matches_written": 0, "results_written": 0, "odds_written": 0, "duplicates": 0,
                "invalid_rows": 0, "r2_objects": 0, "r2_bytes": 0,
                "postgres_rows_written": 0, "postgres_rows_deleted": 0}
    provider_leagues = caller.call("get_leagues")
    available = {str(item.get("id", item.get("league_id", ""))) for item in provider_leagues}
    if league not in available:
        return {"status": "SKIPPED", "error_code": "LEAGUE_NOT_AVAILABLE", "records_seen": 0,
                "records_written": 0, "records_skipped": 0, "records_failed": 0, "dates_completed": 0}
    season_discovery = getattr(caller.provider, "get_seasons", None)
    if callable(season_discovery):
        available_seasons = {str(item.get("id", item.get("season_id", "")))
                             for item in caller.call("get_seasons", league)}
        if season not in available_seasons:
            return {"status": "SKIPPED", "error_code": "SEASON_NOT_AVAILABLE", "records_seen": 0,
                    "records_written": 0, "records_skipped": 0, "records_failed": 0, "dates_completed": 0}
    completed = failed = partial = seen = archived = records_failed = dates_skipped = 0
    matches_written = results_written = odds_written = odds_skipped = duplicates = r2_objects = r2_bytes = 0
    provider_failures = invalid_rows = checksum_failures = 0
    odds_fetch_supported = callable(getattr(caller.provider, "get_odds", None))
    tracker = SQLArchiveTracker(session)
    day = start_date
    while day <= end_date:
        progress, lease_token = _claim_historical_day(
            session, provider=caller.provider.name, season=season, league=league, day=day)
        if progress is not None and progress.status in {"COMPLETED", "EMPTY"}:
            completed += 1
            day += timedelta(days=1)
            continue
        if lease_token is None:
            dates_skipped += 1
            day += timedelta(days=1)
            continue
        try:
            _renew_historical_lease(session, progress_id=progress.id, token=lease_token)
            raw = caller.call("get_matches", day)
            normalized_by_id: dict[str, dict] = {}
            failed_ids = []
            for payload in raw:
                try:
                    row = normalize_match(payload, provider=caller.provider.name)
                except (KeyError, TypeError, ValueError):
                    failed_ids.append(str(payload.get("id", "unknown")) if isinstance(payload, dict) else "unknown")
                    invalid_rows += 1
                    continue
                # Multi-season providers (including Goaloo) can return fixtures
                # from every configured season for a date. Never relabel a row
                # into the requested archive season when the source supplied
                # its own season identity.
                source_season = payload.get("season", payload.get("season_id"))
                if row["league_id"] == league and (source_season is None or str(source_season) == season):
                    match_id = str(row["provider_match_id"])
                    previous = normalized_by_id.get(match_id)
                    if previous is None:
                        normalized_by_id[match_id] = row
                    elif previous == row:
                        duplicates += 1
                    else:
                        failed_ids.append(match_id)
                        invalid_rows += 1
            normalized = list(normalized_by_id.values())
            seen += len(raw)
            archive_count = 0
            if normalized:
                for match_row in normalized:
                    match_row["season"] = season
                _renew_historical_lease(session, progress_id=progress.id, token=lease_token)
                receipt = archive_rows(store, tracker, dataset="matches", season=season, league=league, day=day, rows=normalized)
                archive_count += len(normalized)
                matches_written += len(normalized)
                r2_objects += 1
                r2_bytes += int(receipt.get("size_bytes", 0))
            historical_odds: dict[str, dict] = {}
            if odds_fetch_supported:
                retrieved_at = datetime.now(timezone.utc)
                for match in normalized:
                    if match["status"] != "FINISHED":
                        continue
                    try:
                        quotes = caller.call("get_odds", match["provider_match_id"])
                    except ProviderFailure:
                        provider_failures += 1
                        failed_ids.append(f"odds:{match['provider_match_id']}")
                        continue
                    for quote in quotes:
                        # Goaloo exposes opening (`f`) and latest (`l`) snapshots.
                        # Ignore live (`r`) and never infer a closing snapshot.
                        if quote.get("period") != "PREMATCH" or quote.get("snapshot_type") not in {"OPENING", "LATEST"}:
                            continue
                        odds_id = str(quote.get("provider_odds_id", ""))
                        if not odds_id:
                            failed_ids.append(f"odds:{match['provider_match_id']}:missing-id")
                            invalid_rows += 1
                            continue
                        row = {"provider": quote.get("provider", caller.provider.name),
                               "provider_match_id": match["provider_match_id"],
                               "league_id": league, "league_name": match.get("league_name"),
                               "season": season, "home_team": match["home_team"],
                               "away_team": match["away_team"], "start_time": match["start_time"],
                               "fixture_date": day, "provider_odds_id": odds_id,
                               "provider_market_id": quote.get("provider_market_id"),
                               "provider_bookmaker_id": quote.get("provider_bookmaker_id"),
                               "bookmaker": quote.get("bookmaker"), "market": quote.get("market"),
                               "selection": quote.get("selection"),
                               "line": float(quote["line"]) if quote.get("line") is not None else None,
                               "raw_line": quote.get("raw_line"), "odds": float(quote["odds"]),
                               "snapshot_type": quote["snapshot_type"], "period": "PREMATCH",
                               "source": quote.get("source", "goaloo"), "retrieved_at": retrieved_at,
                               "source_captured_at": quote.get("source_captured_at"),
                               "raw_value": quote.get("raw_value")}
                        previous = historical_odds.get(odds_id)
                        if previous is None:
                            historical_odds[odds_id] = row
                        elif previous == row:
                            duplicates += 1
                        else:
                            failed_ids.append(f"odds:{odds_id}:conflicting-duplicate")
                            invalid_rows += 1
            odds_rows = list(historical_odds.values())
            if odds_rows:
                object_key = f"odds/season={season}/league={league}/date={day.isoformat()}.parquet"
                previous_status = tracker.get_status(object_key)
                object_exists = store.object_exists(object_key)
                # A date/league odds partition might already contain captured
                # operational quotes. Never replace it with a different source
                # snapshot. FAILED + missing object is safe to retry.
                if not object_exists and previous_status in {None, ArchiveStatus.FAILED}:
                    _renew_historical_lease(session, progress_id=progress.id, token=lease_token)
                    receipt = archive_rows(store, tracker, dataset="odds", season=season, league=league,
                                           day=day, rows=odds_rows)
                    odds_written += len(odds_rows)
                    archive_count += len(odds_rows)
                    r2_objects += 1
                    r2_bytes += int(receipt.get("size_bytes", 0))
                else:
                    odds_skipped += len(odds_rows)
            _renew_historical_lease(session, progress_id=progress.id, token=lease_token)
            raw_results = caller.call("get_results", day)
            results_by_id: dict[str, dict] = {}
            match_ids = {match["provider_match_id"] for match in normalized}
            for payload in raw_results:
                try:
                    result = normalize_result(payload, provider=caller.provider.name)
                except (KeyError, TypeError, ValueError):
                    failed_ids.append(str(payload.get("provider_match_id", payload.get("id", "unknown")))
                                      if isinstance(payload, dict) else "unknown")
                    invalid_rows += 1
                    continue
                if result["status"] == "FINAL" and result["provider_match_id"] in match_ids:
                    if result["provider_match_id"] in results_by_id:
                        duplicates += 1
                    else:
                        results_by_id[result["provider_match_id"]] = result
            final_results = list(results_by_id.values())
            if final_results:
                verified_at = datetime.now(timezone.utc)
                for item in final_results:
                    item.update({"league_id": league, "season": season, "verified_at": verified_at})
                _renew_historical_lease(session, progress_id=progress.id, token=lease_token)
                receipt = archive_rows(store, tracker, dataset="results", season=season, league=league,
                                       day=day, rows=final_results)
                archive_count += len(final_results)
                results_written += len(final_results)
                r2_objects += 1
                r2_bytes += int(receipt.get("size_bytes", 0))
            archived += archive_count
            status = "PARTIAL" if failed_ids else "COMPLETED" if archive_count else "EMPTY"
            _finish_historical_day(session, progress_id=progress.id, token=lease_token, values={
                "records_seen": len(raw), "records_archived": archive_count,
                "records_failed": len(failed_ids), "failed_match_ids": sorted(set(failed_ids)),
                "status": status,
                "error_code": "PROVIDER_FAILURE" if provider_failures else "INVALID_PROVIDER_RECORDS" if failed_ids else None,
            })
            records_failed += len(failed_ids)
            if failed_ids:
                partial += 1
            else:
                completed += 1
        except KeyboardInterrupt:
            session.rollback()
            _finish_historical_day(session, progress_id=progress.id, token=lease_token, values={
                "status": "PARTIAL", "records_failed": 0, "error_code": "JOB_INTERRUPTED",
            })
            raise
        except Exception as exc:
            session.rollback()
            error_texts = []
            cause = exc
            while cause is not None:
                error_texts.append(str(cause).lower())
                cause = cause.__cause__
            if any(marker in " ".join(error_texts) for marker in
                   ("checksum", "row count", "unexpected size", "archive object is missing")):
                checksum_failures += 1
            error_code = getattr(exc, "code", None) or (
                "PROVIDER_ERROR" if isinstance(exc, (KeyError, TypeError, ValueError)) else type(exc).__name__.upper())
            _finish_historical_day(session, progress_id=progress.id, token=lease_token, values={
                "status": "FAILED", "records_failed": 1, "error_code": error_code[:80],
            })
            failed += 1
            records_failed += 1
        day += timedelta(days=1)
    status = "PARTIAL" if (failed or partial) and (completed or partial or archived) else "FAILED" if failed else "PARTIAL" if partial else "SUCCESS" if archived else "SKIPPED"
    return {"status": status, "records_seen": seen, "records_written": archived,
            "records_skipped": dates_skipped + (completed if not archived else max(0, completed - 1)),
            "records_failed": records_failed, "dates_completed": completed, "dates_partial": partial, "dates_failed": failed,
            "dates_skipped": dates_skipped,
            "matches_written": matches_written, "results_written": results_written,
            "odds_written": odds_written, "odds_skipped_conflict": odds_skipped,
            "provider_failures": provider_failures,
            "odds_history": "OPENING_LATEST_ONLY_NO_CLOSING" if odds_fetch_supported else "NOT_SUPPORTED",
            "duplicates": duplicates, "invalid_rows": invalid_rows, "r2_objects": r2_objects,
            "r2_bytes": r2_bytes, "checksum_failures": checksum_failures,
            "postgres_rows_written": 0, "postgres_rows_deleted": 0}
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
        row = {"match_id": str(match.id), "provider_match_id": match.provider_match_id,
               "provider": odds.provider, "provider_odds_id": odds.provider_odds_id,
               "provider_market_id": odds.provider_market_id, "provider_bookmaker_id": odds.provider_bookmaker_id,
               "league_id": match.league_id, "league_name": match.league_name, "season": match.season,
               "home_team": match.home_team, "away_team": match.away_team, "start_time": match.start_time,
               "bookmaker": odds.bookmaker, "bookmaker_entity_id": str(odds.bookmaker_entity_id) if odds.bookmaker_entity_id else None,
               "market": odds.market, "selection": odds.selection, "line": float(odds.line) if odds.line is not None else None,
               "raw_line": odds.raw_line, "line_key": odds.line_key, "odds": float(odds.odds),
               "price": float(odds.odds), "source": odds.source, "period": odds.period, "status": odds.status,
               "captured_at": odds.captured_at, "snapshot_type": odds.snapshot_type,
               "raw_value": odds.raw_value}
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
        ArchiveJob.dataset_type.in_(("odds", "results", "matches")),
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
        season_segment = next((part for part in parts if part.startswith("season=")), None)
        if season_segment is None:
            continue
        season_id = season_segment.removeprefix("season=")
        if job.dataset_type == "odds":
            match_scope = select(ActiveMatch.id).where(
                ActiveMatch.league_id == job.league, ActiveMatch.season == season_id)
            query = delete(ActiveOdds).where(
                ActiveOdds.captured_at >= day_start, ActiveOdds.captured_at < day_end,
                ActiveOdds.match_id.in_(match_scope))
            session.execute(query)
        elif job.dataset_type == "results":
            match_scope = select(ActiveMatch.id).where(
                ActiveMatch.league_id == job.league, ActiveMatch.season == season_id)
            query = delete(MatchResult).where(
                func.coalesce(MatchResult.finished_at, MatchResult.verified_at) >= day_start,
                func.coalesce(MatchResult.finished_at, MatchResult.verified_at) < day_end,
                MatchResult.status == "FINAL", MatchResult.verified_at.is_not(None),
                MatchResult.match_id.in_(match_scope))
            session.execute(query)
        elif job.dataset_type == "matches":
            # Historical R2 fixtures are the source of truth. Re-read their IDs
            # and retain product records before deleting the expiring active row.
            from app.storage.r2 import parquet_rows
            archived_ids = {str(row.get("provider_match_id")) for row in
                            parquet_rows(store.get_object(job.r2_key))
                            if row.get("provider_match_id") is not None}
            eligible = session.scalars(select(ActiveMatch).where(
                ActiveMatch.start_time >= day_start, ActiveMatch.start_time < day_end,
                ActiveMatch.expires_at <= datetime.now(timezone.utc),
                ActiveMatch.season == season_id,
                ActiveMatch.league_id == job.league,
            ).with_for_update()).all()
            if any(match.provider_match_id not in archived_ids for match in eligible):
                continue
            match_ids = [match.id for match in eligible]
            if not match_ids:
                job.status = ArchiveStatus.DELETED.value
                job.deleted_at = datetime.now(timezone.utc)
                session.commit()
                count += 1
                continue
            if job.league:
                match_ids = [match.id for match in eligible if match.league_id == job.league]
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
