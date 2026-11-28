"""Explicit historical transfer with immutable objects and an atomic SQL checkpoint."""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json

import httpx
from sqlalchemy import select, text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.orm import Session

from app.models.season_bulk import season_fixtures, season_imports, season_odds, season_teams
from app.providers.season_files import (
    MAX_BYTES, DownloadBudget, Source, download, parse_season, source_for, summarize_source,
)
from app.storage.r2 import ObjectStore, parquet_bytes, parquet_row_count

PARSER_VERSION = "1"
APPROVED_LICENCES = {"CC0-1.0", "ODbL-1.0"}


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _json(data: dict) -> bytes:
    return json.dumps(data, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()


def _verified_bytes(store: ObjectStore, receipt: dict) -> bytes:
    key = receipt["key"]
    head = store.head_object(key)
    if head is None or head.size != receipt["size"] or (head.metadata or {}).get("sha256") != receipt["sha256"]:
        raise RuntimeError("ARCHIVE_RECEIPT_MISMATCH")
    actual = store.get_object(key)
    if len(actual) != receipt["size"] or _sha(actual) != receipt["sha256"]:
        raise RuntimeError("ARCHIVE_CHECKSUM_MISMATCH")
    if "row_count" in receipt and parquet_row_count(actual) != receipt["row_count"]:
        raise RuntimeError("ARCHIVE_ROW_COUNT_MISMATCH")
    return actual


def _archive(store: ObjectStore, key: str, data: bytes, content_type: str, rows: int | None = None) -> dict:
    receipt = {"key": key, "sha256": _sha(data), "size": len(data)}
    if rows is not None:
        receipt["row_count"] = rows
    if store.head_object(key) is None:
        store.put_object_if_absent(key, data, content_type=content_type, metadata={"sha256": receipt["sha256"]})
    _verified_bytes(store, receipt)
    return receipt


def reuse_source(session: Session, store: ObjectStore, source: Source) -> bytes | None:
    """Pinned historical versions may reuse a verified raw object before downloading."""
    if source.revision is None:
        return None
    values = session.scalars(select(season_imports.c.receipt).where(
        season_imports.c.source_url == source.url, season_imports.c.revision == source.revision,
    ).order_by(season_imports.c.completed_at.desc()).limit(1)).first()
    return _verified_bytes(store, values["raw"]) if values else None


def transfer_source(session: Session, store: ObjectStore, source: Source, client: httpx.Client,
                    budget: DownloadBudget, *, approval: str) -> dict:
    if approval != "IMPORT_REVIEWED_SEASON_ONLY":
        raise PermissionError("SEASON_IMPORT_APPROVAL_REQUIRED")
    if source != source_for(source.league, source.season):
        raise PermissionError("BLOCKED_LICENSE_REVIEW")
    raw = reuse_source(session, store, source)
    if raw is None:
        raw = download(source, client, budget)
    return import_season(session, store, source, raw, approval=approval)


def import_season(session: Session, store: ObjectStore, source: Source, raw: bytes,
                  *, approval: str, odds: tuple[dict, ...] = (), odds_permission: str = "") -> dict:
    """Caller owns the transaction. Run in isolated storage until production is approved."""
    if approval != "IMPORT_REVIEWED_SEASON_ONLY":
        raise PermissionError("SEASON_IMPORT_APPROVAL_REQUIRED")
    if source != source_for(source.league, source.season) or source.licence not in APPROVED_LICENCES:
        raise PermissionError("BLOCKED_LICENSE_REVIEW")
    if not 0 < len(raw) <= MAX_BYTES or len(odds) > 10000:
        raise ValueError("SEASON_IMPORT_SIZE_LIMIT")

    rows = parse_season(source, raw)
    fixtures = {row["id"]: row for row in rows}
    observations = {}
    if odds and not odds_permission.strip():
        raise PermissionError("BLOCKED_LICENSE_REVIEW")
    from app.analysis.season_favorites import validate_odds

    for observation in odds:
        normalized = validate_odds(observation, fixtures)
        normalized["permission_reference"] = odds_permission
        natural_key = {k: normalized[k] for k in ("fixture_id", "provider", "bookmaker", "observed_at", "kind")}
        observation_id = _sha(_json(natural_key))
        if observation_id in observations and observations[observation_id] != normalized:
            raise ValueError("CONFLICTING_ODDS_OBSERVATION")
        observations[observation_id] = normalized
    if observations:
        covered = {row["fixture_id"] for row in observations.values()}
        for row in rows:
            if row["id"] in covered:
                row["odds_status"] = "AVAILABLE"

    report = summarize_source(source, raw, rows)
    report["odds"] = len({row["fixture_id"] for row in observations.values()})
    if observations:
        report["odds_status"] = "AVAILABLE"
    odds_rows = [{"id": key, **value} for key, value in sorted(observations.items())]
    batch = _sha(_json({"source": source.url, "sha256": report["sha256"], "parser": PARSER_VERSION,
                        "odds": odds_rows}))
    dialect = session.get_bind().dialect.name
    if dialect == "postgresql":
        lock_key = int.from_bytes(hashlib.sha256(f"{source.league}/{source.season}".encode()).digest()[:8],
                                  "big", signed=True)
        session.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": lock_key})
    elif dialect != "sqlite":
        raise ValueError("UNSUPPORTED_IMPORT_DATABASE")
    insert = pg_insert if dialect == "postgresql" else sqlite_insert

    previous = session.scalar(select(season_imports.c.receipt).where(season_imports.c.id == batch))
    if previous:
        for kind in ("raw", "matches", "odds_archive", "manifest"):
            if kind in previous:
                _verified_bytes(store, previous[kind])
        return {**previous, "reused": True}

    prefix = f"season-bulk/{source.provider}/season={source.season}/league={source.league}"
    extension = "txt" if source.format == "football.txt" else "json"
    raw_receipt = _archive(store, f"{prefix}/raw/{report['sha256']}.{extension}", raw,
                           "text/plain; charset=utf-8" if extension == "txt" else "application/json")
    match_data = parquet_bytes(rows)
    matches = _archive(store, f"{prefix}/matches/{_sha(match_data)}.parquet", match_data,
                       "application/vnd.apache.parquet", len(rows))
    receipt = {**report, "verification": "ARCHIVE_VERIFIED", "raw": raw_receipt, "matches": matches}
    if odds_rows:
        data = parquet_bytes(odds_rows)
        receipt["odds_archive"] = _archive(store, f"{prefix}/odds/{_sha(data)}.parquet", data,
                                           "application/vnd.apache.parquet", len(odds_rows))
    manifest = _json({**receipt, "parser_version": PARSER_VERSION, "batch": batch})
    receipt["manifest"] = _archive(store, f"{prefix}/manifests/{batch}.json", manifest, "application/json")

    now = datetime.now(timezone.utc)
    teams = {row[f"{side}_team_id"]: {"id": row[f"{side}_team_id"], "provider": source.provider,
                                     "source_name": row[f"{side}_team"]}
             for row in rows for side in ("home", "away")}
    session.execute(insert(season_teams).values(list(teams.values())).on_conflict_do_nothing())
    fixture_values = [{"id": row["id"], "league": row["league"], "season": row["season"],
                       "data": row, "updated_at": now} for row in rows]
    existing = dict(session.execute(select(season_fixtures.c.id, season_fixtures.c.data).where(
        season_fixtures.c.league == source.league, season_fixtures.c.season == source.season,
    )).all())
    # Preserve a previously reported score when a refreshed source loses it.
    for item in fixture_values:
        prior = existing.get(item["id"], {})
        if prior.get("regulation") is not None and item["data"]["regulation"] is None:
            item["data"] = {**item["data"], **{key: prior[key] for key in (
                "regulation", "half_time", "extra_time", "penalties", "status", "verification",
            )}}
        if prior.get("odds_status") == "AVAILABLE":
            item["data"] = {**item["data"], "odds_status": "AVAILABLE"}
    statement = insert(season_fixtures).values(fixture_values)
    session.execute(statement.on_conflict_do_update(
        index_elements=[season_fixtures.c.id],
        set_={"data": statement.excluded.data, "updated_at": statement.excluded.updated_at},
        where=season_fixtures.c.verified_at.is_(None),
    ))
    if observations:
        for observation_id, observation in observations.items():
            previous_odds = session.scalar(select(season_odds.c.data).where(season_odds.c.id == observation_id))
            if previous_odds and previous_odds != observation:
                raise ValueError("CONFLICTING_ODDS_OBSERVATION")
        session.execute(insert(season_odds).values([
            {"id": key, "fixture_id": row["fixture_id"], "data": row} for key, row in observations.items()
        ]).on_conflict_do_nothing())
    session.execute(insert(season_imports).values(id=batch, league=source.league, season=source.season,
                    source_url=source.url, revision=source.revision, receipt=receipt, completed_at=now))
    return {**receipt, "reused": False}
