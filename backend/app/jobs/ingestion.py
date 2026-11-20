"""Provider-to-active-state ingestion and verified R2 historical backfill."""
from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone
from typing import Any
from uuid import uuid4

from sqlalchemy import func, select, update, or_
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.models import ActiveMatch, ActiveOdds, ArchiveJob, Bookmaker, HistoricalIngestDay, MatchResult
from app.providers.identity import fixture_key, lock_id
from app.providers.base import ProviderError, ProviderFailure, ProviderNotConfigured
from app.providers.normalize import normalize_match, normalize_matches, normalize_odds, normalize_result, utc_datetime
from app.storage.archive import ArchiveStatus, SQLArchiveTracker
from app.storage.r2 import ObjectStore
from app.storage.incremental import archive_incremental, row_identity


def _expires(start_time: datetime, days: int) -> datetime:
    return max(start_time + timedelta(days=days), datetime.now(timezone.utc) + timedelta(hours=1))


def _safe_status(status: str) -> str:
    allowed = {"SCHEDULED", "LIVE", "IN_PLAY", "PAUSED", "FINISHED", "POSTPONED", "CANCELLED", "SUSPENDED"}
    return status if status in allowed else "UNKNOWN"


def upsert_matches(session: Session, matches: list[dict], *, now: datetime | None = None) -> tuple[int, int]:
    current = now or datetime.now(timezone.utc)
    retention = get_settings().active_state_retention_days
    written = skipped = 0
    if matches and session.get_bind().dialect.name == "postgresql":
        # Acquire a deterministic order across a batch to avoid reciprocal lock waits.
        keys = {lock_id(key) for match in matches if (key := fixture_key(match)) is not None}
        for key in sorted(keys):
            session.execute(select(func.pg_advisory_xact_lock(key)))
    for match in matches:
        if match["start_time"] < current - timedelta(days=retention):
            skipped += 1
            continue
        identity = fixture_key(match)
        if identity:
            # Cooperating PostgreSQL writers hold the ordered batch locks above.
            candidates = session.scalars(select(ActiveMatch).where(
                ActiveMatch.start_time == match["start_time"],
                or_(ActiveMatch.provider != match["provider"],
                    ActiveMatch.provider_match_id != match["provider_match_id"]),
            )).all()
            duplicate = False
            for candidate in candidates:
                kickoff = candidate.start_time
                if kickoff.tzinfo is None:  # SQL timestamps are UTC; SQLite test adapter drops tzinfo.
                    kickoff = kickoff.replace(tzinfo=timezone.utc)
                other = {"provider": candidate.provider, "provider_match_id": candidate.provider_match_id, "league_id": candidate.league_id,
                         "home_team": candidate.home_team, "away_team": candidate.away_team, "start_time": kickoff}
                if fixture_key(other) == identity:
                    duplicate = True
                    break
            if duplicate:
                # Do not silently attach another source's odds/results to an unpersisted alias.
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
    return {"status": "SUCCESS", **archive_incremental(store, tracker, dataset=dataset, season=season,
                                                       league=league, day=day, rows=rows)}


def _claim_historical_day(session: Session, *, provider: str, season: str, league: str,
                          day: date, include_odds: bool = True) -> tuple[HistoricalIngestDay | None, str | None]:
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

    coverage = row.coverage or {}
    scope_complete = not include_odds or coverage.get("odds_requested", True)
    if row.status in {"COMPLETED", "EMPTY"} and coverage.get("version") == 1 and scope_complete:
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
                      start_date: date, end_date: date, include_odds: bool | None = None) -> dict[str, Any]:
    if include_odds is None:
        include_odds = get_settings().historical_collect_odds
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
    provider_failures = invalid_rows = checksum_failures = statistics_written = 0
    coverage_report = []
    odds_fetch_supported = callable(getattr(caller.provider, "get_odds", None))
    tracker = SQLArchiveTracker(session)
    day = start_date
    while day <= end_date:
        progress, lease_token = _claim_historical_day(
            session, provider=caller.provider.name, season=season, league=league, day=day, include_odds=include_odds)
        if progress is not None and progress.status in {"COMPLETED", "EMPTY"}:
            coverage_report.append({"date": str(day), "status": progress.status,
                                    "fields": progress.coverage, "failed_match_ids": progress.failed_match_ids})
            completed += 1
            day += timedelta(days=1)
            continue
        if lease_token is None:
            dates_skipped += 1
            day += timedelta(days=1)
            continue
        try:
            coverage = {"version": 1, "odds_requested": include_odds,
                        "odds_scope": "NOT_REQUESTED" if not include_odds else "FROM_2025_26" if day >= date(2025, 7, 1) else "OUTSIDE_REQUESTED_SCOPE"}
            failed_ids = []
            archive_count = 0
            day_provider_failures = 0
            _renew_historical_lease(session, progress_id=progress.id, token=lease_token)
            raw = (caller.call("get_matches_for_scope", day, league, season)
                   if callable(getattr(caller.provider, "get_matches_for_scope", None)) else caller.call("get_matches", day))
            if not isinstance(raw, list):
                raise ProviderError("PROVIDER_ERROR: matches response is not a list")
            normalized_by_id: dict[str, dict] = {}
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
                    if row["start_time"].date() != day:
                        failed_ids.append(f"match:{match_id}:outside-date-scope")
                        invalid_rows += 1
                        continue
                    previous = normalized_by_id.get(match_id)
                    if previous is None:
                        normalized_by_id[match_id] = row
                    elif previous == row:
                        duplicates += 1
                    else:
                        failed_ids.append(match_id)
                        invalid_rows += 1
            normalized = list(normalized_by_id.values())
            for match in normalized:
                if match["status"] not in {"FINISHED", "CANCELLED"}:
                    failed_ids.append(f"match:{match['provider_match_id']}:awaiting-final-status")
                    coverage[f"match:{match['provider_match_id']}"] = "AWAITING_FINAL_STATUS"
            # Active state owns mutable fixtures. Do not freeze an unfinished
            # fixture into an immutable historical object or completed checkpoint.
            normalized = [match for match in normalized if match["status"] in {"FINISHED", "CANCELLED"}]
            seen += len(raw)
            archive_count = 0
            if normalized:
                for match_row in normalized:
                    match_row["season"] = season
                _renew_historical_lease(session, progress_id=progress.id, token=lease_token)
                receipt = archive_rows(store, tracker, dataset="matches", season=season, league=league, day=day, rows=normalized)
                archive_count += receipt["records_written"]
                matches_written += receipt["records_written"]
                r2_objects += bool(receipt["records_written"])
                r2_bytes += int(receipt.get("size_bytes", 0))
            historical_odds: dict[str, dict] = {}
            conflicting_odds = set()
            if include_odds and odds_fetch_supported and day >= date(2025, 7, 1):
                retrieved_at = datetime.now(timezone.utc)
                for match in normalized:
                    if match["status"] != "FINISHED":
                        continue
                    try:
                        _renew_historical_lease(session, progress_id=progress.id, token=lease_token)
                        quotes = caller.call("get_odds", match["provider_match_id"])
                    except ProviderFailure as exc:
                        provider_failures += 1
                        day_provider_failures += 1
                        failed_ids.append(f"odds:{match['provider_match_id']}")
                        coverage[f"odds:{match['provider_match_id']}"] = "FAILED"
                        coverage[f"odds_error:{match['provider_match_id']}"] = exc.code
                        continue
                    coverage[f"odds:{match['provider_match_id']}"] = "NO_VERIFIED_PREMATCH_QUOTES"
                    for quote in quotes:
                        # Goaloo exposes opening (`f`) and latest (`l`) snapshots.
                        # Ignore live (`r`) and never infer a closing snapshot.
                        kind = quote.get("snapshot_type", "OBSERVED")
                        if quote.get("period") != "PREMATCH" or kind not in {"OPENING", "LATEST", "CLOSING", "OBSERVED"}:
                            continue
                        if caller.provider.name == "goaloo" and kind != "OPENING":
                            # `l` is latest, with no verified pre-kickoff timestamp.
                            # Preserve it in raw provider probes, not prematch history.
                            continue
                        source_timestamp = quote.get("source_captured_at")
                        if caller.provider.name != "goaloo":
                            source_timestamp = source_timestamp or quote.get("captured_at")
                        source_timestamp = utc_datetime(source_timestamp) if source_timestamp is not None else None
                        if source_timestamp is not None and source_timestamp > match["start_time"]:
                            continue
                        if kind == "OBSERVED" and source_timestamp is None:
                            continue
                        odds_id = str(quote.get("provider_odds_id", ""))
                        if not odds_id:
                            failed_ids.append(f"odds:{match['provider_match_id']}:missing-id")
                            invalid_rows += 1
                            continue
                        normalized_quote = normalize_odds({**quote, "captured_at": quote.get("captured_at") or retrieved_at},
                                                         match_id=match["provider_match_id"], provider=caller.provider.name)
                        row = {"provider": quote.get("provider", caller.provider.name),
                               "provider_match_id": match["provider_match_id"],
                               "league_id": league, "league_name": match.get("league_name"),
                               "season": season, "home_team": match["home_team"],
                               "away_team": match["away_team"], "start_time": match["start_time"],
                               "fixture_date": day, "provider_odds_id": odds_id,
                               "provider_market_id": quote.get("provider_market_id"),
                               "provider_bookmaker_id": quote.get("provider_bookmaker_id"),
                               "bookmaker": normalized_quote["bookmaker"], "market": normalized_quote["market"],
                               "selection": normalized_quote["selection"],
                               "line": float(normalized_quote["line"]) if normalized_quote.get("line") is not None else None,
                               "line_key": normalized_quote["line_key"],
                               "raw_line": quote.get("raw_line"), "odds": float(quote["odds"]),
                               "snapshot_type": kind, "period": "PREMATCH",
                               "source": quote.get("source", "goaloo"), "retrieved_at": retrieved_at,
                               "source_url": quote.get("source_url"),
                               "source_captured_at": source_timestamp,
                               "raw_value": quote.get("raw_value")}
                        coverage[f"odds:{match['provider_match_id']}"] = "AVAILABLE_SOURCE_TIMESTAMP_MISSING" if source_timestamp is None else "AVAILABLE"
                        identity = row_identity("odds", row)
                        if identity in conflicting_odds:
                            continue
                        previous = historical_odds.get(identity)
                        if previous is None:
                            historical_odds[identity] = row
                        elif previous == row:
                            duplicates += 1
                        else:
                            failed_ids.append(f"odds:{odds_id}:conflicting-duplicate")
                            invalid_rows += 1
                            historical_odds.pop(identity)
                            conflicting_odds.add(identity)
            odds_rows = list(historical_odds.values())
            if odds_rows:
                _renew_historical_lease(session, progress_id=progress.id, token=lease_token)
                receipt = archive_rows(store, tracker, dataset="odds", season=season, league=league,
                                       day=day, rows=odds_rows)
                written = receipt["records_written"]
                odds_written += written
                odds_skipped += len(odds_rows) - written
                archive_count += written
                r2_objects += bool(written)
                r2_bytes += int(receipt.get("size_bytes", 0))
            for match in normalized:
                if match["status"] != "FINISHED":
                    continue
                match_id = match["provider_match_id"]
                if getattr(caller.provider, "statistics_supported", None) is False or not callable(getattr(caller.provider, "get_statistics", None)):
                    coverage[f"statistics:{match_id}"] = "NOT_SUPPORTED"
                    continue
                try:
                    _renew_historical_lease(session, progress_id=progress.id, token=lease_token)
                    stats = caller.call("get_statistics", match_id)
                    if not isinstance(stats, list) or any(not isinstance(item, dict) for item in stats):
                        raise ValueError("Invalid statistics response")
                    import json
                    import hashlib
                    stat_rows = []
                    for item in stats:
                        raw_stat = json.dumps(item, sort_keys=True, default=str, ensure_ascii=False)
                        stat_rows.append({"provider": caller.provider.name, "provider_match_id": match_id,
                                          "season": season, "league_id": league, "start_time": match["start_time"],
                                          "statistic_id": str(item.get("id") or hashlib.sha256(raw_stat.encode()).hexdigest()),
                                          "name": item.get("name"), "period": item.get("period"),
                                          "home_value": item.get("home_value"), "away_value": item.get("away_value"),
                                          "unit": item.get("unit"), "source_url": item.get("source_url"),
                                          "raw_value": raw_stat, "retrieved_at": datetime.now(timezone.utc)})
                    receipt = archive_rows(store, tracker, dataset="statistics", season=season, league=league,
                                           day=day, rows=stat_rows)
                    written = receipt["records_written"]
                    statistics_written += written
                    archive_count += written
                    r2_objects += bool(written)
                    r2_bytes += receipt.get("size_bytes", 0)
                    coverage[f"statistics:{match_id}"] = "AVAILABLE" if stats else "SOURCE_EMPTY"
                except (ProviderFailure, ValueError, TypeError):
                    coverage[f"statistics:{match_id}"] = "FAILED"
                    failed_ids.append(f"statistics:{match_id}")
                    provider_failures += 1
                    day_provider_failures += 1
            _renew_historical_lease(session, progress_id=progress.id, token=lease_token)
            raw_results = (caller.call("get_results_for_scope", day, league, season)
                           if callable(getattr(caller.provider, "get_results_for_scope", None)) else caller.call("get_results", day))
            if not isinstance(raw_results, list):
                raise ProviderError("PROVIDER_ERROR: results response is not a list")
            results_by_id: dict[str, dict] = {}
            conflicting_results = set()
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
                    if result["provider_match_id"] in conflicting_results:
                        continue
                    if result["provider_match_id"] in results_by_id:
                        if results_by_id[result["provider_match_id"]] == result:
                            duplicates += 1
                        else:
                            failed_ids.append(f"result:{result['provider_match_id']}:conflicting-duplicate")
                            invalid_rows += 1
                            results_by_id.pop(result["provider_match_id"])
                            conflicting_results.add(result["provider_match_id"])
                    else:
                        results_by_id[result["provider_match_id"]] = result
            final_results = list(results_by_id.values())
            for match in normalized:
                if match["status"] == "FINISHED" and match["provider_match_id"] not in results_by_id:
                    failed_ids.append(f"result:{match['provider_match_id']}")
                    coverage[f"result:{match['provider_match_id']}"] = "MISSING"
            if final_results:
                verified_at = datetime.now(timezone.utc)
                for item in final_results:
                    item.update({"league_id": league, "season": season, "verified_at": verified_at})
                _renew_historical_lease(session, progress_id=progress.id, token=lease_token)
                receipt = archive_rows(store, tracker, dataset="results", season=season, league=league,
                                       day=day, rows=final_results)
                archive_count += receipt["records_written"]
                results_written += receipt["records_written"]
                r2_objects += bool(receipt["records_written"])
                r2_bytes += int(receipt.get("size_bytes", 0))
            archived += archive_count
            status = "PARTIAL" if failed_ids else "COMPLETED" if normalized else "EMPTY"
            _finish_historical_day(session, progress_id=progress.id, token=lease_token, values={
                "records_seen": len(raw), "records_archived": (progress.records_archived or 0) + archive_count,
                "records_failed": len(failed_ids), "failed_match_ids": sorted(set(failed_ids)),
                "status": status,
                "coverage": coverage,
                "error_code": "PROVIDER_FAILURE" if day_provider_failures else "INVALID_PROVIDER_RECORDS" if failed_ids else None,
            })
            records_failed += len(failed_ids)
            coverage_report.append({"date": str(day), "status": status, "fields": coverage,
                                    "failed_match_ids": sorted(set(failed_ids))})
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
                "status": "FAILED", "records_failed": max(1, len(failed_ids)),
                "records_archived": (progress.records_archived or 0) + archive_count,
                "failed_match_ids": sorted(set(failed_ids)),
                "coverage": coverage, "error_code": error_code[:80],
            })
            coverage_report.append({"date": str(day), "status": "FAILED", "fields": coverage,
                                    "error_code": error_code[:80]})
            failed += 1
            records_failed += 1
        day += timedelta(days=1)
    archived = matches_written + results_written + odds_written + statistics_written
    status = "PARTIAL" if (failed or partial) and (completed or partial or archived) else "FAILED" if failed else "PARTIAL" if partial else "SUCCESS" if archived else "SKIPPED"
    return {"status": status, "records_seen": seen, "records_written": archived,
            "statistics_written": statistics_written, "coverage": coverage_report,
            "records_skipped": dates_skipped + (completed if not archived else max(0, completed - 1)),
            "records_failed": records_failed, "dates_completed": completed, "dates_partial": partial, "dates_failed": failed,
            "dates_skipped": dates_skipped,
            "matches_written": matches_written, "results_written": results_written,
            "odds_written": odds_written, "odds_skipped_conflict": odds_skipped,
            "provider_failures": provider_failures,
            "odds_requested": include_odds,
            "odds_history": "NOT_REQUESTED" if not include_odds else "OPENING_ONLY_LATEST_AND_CLOSING_UNVERIFIED" if caller.provider.name == "goaloo" else "PROVIDER_PREMATCH_ONLY" if odds_fetch_supported else "NOT_SUPPORTED",
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

    def controlled_import(match):
        # The one reviewed import owns its licensed snapshot and partition date.
        # A generic snapshot of the operational alias (or verification-day partition)
        # would create a second historical copy and could lose the ODbL attribution.
        # Pending controlled imports must be resumed with fresh verification, never
        # silently completed by this background job.
        from app.models import SystemSetting
        from app.providers.identity import REVIEWED_IMPORT_KEY, reviewed_fixture_key
        row = {"provider": match.provider, "provider_match_id": match.provider_match_id,
               "league_id": match.league_id, "home_team": match.home_team,
               "away_team": match.away_team, "start_time": match.start_time}
        if isinstance(row["start_time"], datetime) and row["start_time"].tzinfo is None:
            row["start_time"] = row["start_time"].replace(tzinfo=timezone.utc)
        if reviewed_fixture_key(row) is None:
            return False
        journal = session.get(SystemSetting, REVIEWED_IMPORT_KEY)
        return bool(journal and journal.value.get("match_id") == str(match.id))

    def canonical(row: dict) -> dict:
        return {key: str(value) if isinstance(value, UUID) else float(value) if isinstance(value, Decimal) else value
                for key, value in row.items()}

    expired_matches = session.scalars(select(ActiveMatch).where(
        ActiveMatch.expires_at <= current, ActiveMatch.start_time < cutoff,
        ActiveMatch.status.in_(("FINISHED", "CANCELLED")),
    )).all()
    for match in expired_matches:
        if controlled_import(match):
            continue
        row = {column.name: getattr(match, column.name) for column in ActiveMatch.__table__.columns}
        partitions[("matches", match.season or "unknown", match.league_id or "unknown",
                    match.start_time.date())].append(row)

    odds_rows = session.execute(select(ActiveOdds, ActiveMatch).join(
        ActiveMatch, ActiveMatch.id == ActiveOdds.match_id
    ).where(ActiveOdds.expires_at <= current, ActiveOdds.captured_at < cutoff)).all()
    for odds, match in odds_rows:
        row = {"odds_id": str(odds.id), "match_id": str(match.id), "provider_match_id": match.provider_match_id,
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
        if controlled_import(match):
            continue
        row = {"result_id": str(result.id), "match_id": str(match.id), "provider_match_id": match.provider_match_id,
               "provider": result.provider, "provider_result_id": result.provider_result_id,
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

    counts = {"matches": 0, "odds": 0, "results": 0, "predictions": 0}
    tracker = SQLArchiveTracker(session)
    for (dataset, season, league, day), rows in sorted(partitions.items()):
        receipt = archive_rows(store, tracker, dataset=dataset, season=season, league=league, day=day,
                               rows=[canonical(row) for row in rows])
        counts[dataset] += receipt["records_written"]
    return counts

def cleanup_verified_archives(session: Session, store: ObjectStore, *, today: date | None = None) -> int:
    """Re-read and verify R2 bytes before deleting old source partitions."""
    from sqlalchemy import delete, func
    from app.models import CouponItem, MatchAnalysis, Prediction
    from app.storage.archive import _verify_object

    def matches_snapshot(entity, row):
        from decimal import Decimal
        from uuid import UUID
        required = {
            "active_matches": {"provider", "provider_match_id", "league_id", "season", "home_team", "away_team", "start_time", "status"},
            "active_odds": {"provider", "match_id", "bookmaker", "market", "selection", "line", "odds", "captured_at", "snapshot_type", "period", "source"},
            "match_results": {"provider", "match_id", "home_score", "away_score", "status", "result_source", "verified_at"},
        }
        if not required[entity.__tablename__].issubset(row):
            return False
        def value(item):
            if isinstance(item, datetime):
                return item.replace(tzinfo=timezone.utc) if item.tzinfo is None else item.astimezone(timezone.utc)
            if isinstance(item, UUID):
                return str(item)
            if isinstance(item, Decimal):
                return float(item)
            return item
        return all(value(getattr(entity, column.name)) == value(row[column.name])
                   for column in entity.__table__.columns if column.name in row)

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
        session.refresh(job, with_for_update=True)
        if job.status != ArchiveStatus.VERIFIED.value:
            continue
        remaining_covered = False
        try:
            parts = job.r2_key.split("/")
            date_base = f"date={job.archive_date.isoformat()}"
            league_segment = f"league={job.league}" if job.league else None
            if (not parts or parts[0] != job.dataset_type or not (
                    parts[-1] == date_base + ".parquet" or
                    (len(parts) == 5 and parts[-2] == date_base and parts[-1].startswith("part=") and parts[-1].endswith(".parquet")))
                    or not any(part.startswith("season=") for part in parts)
                    or (league_segment and league_segment not in parts)):
                continue
            archived_rows = _verify_object(store, job.r2_key, expected_size=job.file_size_bytes,
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
            candidates = session.scalars(select(ActiveOdds).where(
                ActiveOdds.captured_at >= day_start, ActiveOdds.captured_at < day_end,
                ActiveOdds.expires_at <= datetime.now(timezone.utc),
                ActiveOdds.match_id.in_(match_scope)).with_for_update()).all()
            by_id = {row["odds_id"]: row for row in archived_rows if row.get("odds_id")}
            eligible = [row.id for row in candidates if str(row.id) in by_id and matches_snapshot(row, by_id[str(row.id)])]
            if not eligible:
                continue
            session.execute(delete(ActiveOdds).where(ActiveOdds.id.in_(eligible)))
            remaining_covered = any(str(row.id) in by_id for row in session.scalars(
                select(ActiveOdds).where(ActiveOdds.match_id.in_(match_scope))).all())
        elif job.dataset_type == "results":
            match_scope = select(ActiveMatch.id).where(
                ActiveMatch.league_id == job.league, ActiveMatch.season == season_id)
            candidates = session.scalars(select(MatchResult).where(
                func.coalesce(MatchResult.finished_at, MatchResult.verified_at) >= day_start,
                func.coalesce(MatchResult.finished_at, MatchResult.verified_at) < day_end,
                MatchResult.status == "FINAL", MatchResult.verified_at.is_not(None),
                MatchResult.match_id.in_(match_scope)).with_for_update()).all()
            by_id = {row["result_id"]: row for row in archived_rows if row.get("result_id")}
            eligible = [row.id for row in candidates if str(row.id) in by_id and matches_snapshot(row, by_id[str(row.id)])
                        and (row.finished_at or row.verified_at).replace(tzinfo=timezone.utc) <
                        datetime.now(timezone.utc) - timedelta(days=get_settings().active_state_retention_days)]
            if not eligible:
                continue
            session.execute(delete(MatchResult).where(MatchResult.id.in_(eligible)))
            remaining_covered = any(str(row.id) in by_id for row in session.scalars(
                select(MatchResult).where(MatchResult.match_id.in_(match_scope))).all())
        elif job.dataset_type == "matches":
            # Historical R2 fixtures are the source of truth. Re-read their IDs
            # and retain product records before deleting the expiring active row.
            archived_ids = {(row.get("provider"), str(row.get("provider_match_id"))): row
                            for row in archived_rows if row.get("provider") and row.get("provider_match_id")}
            eligible = session.scalars(select(ActiveMatch).where(
                ActiveMatch.start_time >= day_start, ActiveMatch.start_time < day_end,
                ActiveMatch.expires_at <= datetime.now(timezone.utc),
                ActiveMatch.season == season_id,
                ActiveMatch.league_id == job.league,
            ).with_for_update()).all()
            eligible = [match for match in eligible if (match.provider, match.provider_match_id) in archived_ids
                        and matches_snapshot(match, archived_ids[(match.provider, match.provider_match_id)])]
            match_ids = [match.id for match in eligible]
            if not match_ids:
                continue
            if job.league:
                match_ids = [match.id for match in eligible if match.league_id == job.league]
            protected = set()
            for model in (ActiveOdds, MatchResult, Prediction, MatchAnalysis, CouponItem):
                protected.update(session.scalars(select(model.match_id).where(model.match_id.in_(match_ids))).all())
            match_ids = [identity for identity in match_ids if identity not in protected]
            if not match_ids:
                continue
            session.execute(delete(ActiveMatch).where(ActiveMatch.id.in_(match_ids)))
            remaining_covered = any((match.provider, match.provider_match_id) in archived_ids for match in
                                    session.scalars(select(ActiveMatch).where(
                                        ActiveMatch.league_id == job.league, ActiveMatch.season == season_id,
                                        ActiveMatch.start_time >= day_start, ActiveMatch.start_time < day_end)).all())
        else:
            continue

        if not remaining_covered:
            job.status = ArchiveStatus.DELETED.value
            job.deleted_at = datetime.now(timezone.utc)
        session.commit()
        count += 1
    return count
