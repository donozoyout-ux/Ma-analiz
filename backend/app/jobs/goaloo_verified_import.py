"""Matched-only Goaloo odds importer. No network or top-level side effects.

Only Goaloo observations with an unambiguous, reviewed Bundesliga
cross-source identity and an existing OpenLigaDB ActiveMatch may be written.
A captured_at timestamp must come from the observation, not a reconstructed
historical quote. Offline dry-run is the default; live writes require BOTH
explicit flags and an injected archive store.
"""
from __future__ import annotations

from collections import Counter
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from hashlib import sha256
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import ActiveMatch, ActiveOdds
from app.providers.goaloo_match_identity import MappingDecision
from app.storage.archive import archive_partition
from app.storage.r2 import ObjectStore


class ImportSafetyError(ValueError):
    pass


def _aware(value: Any) -> datetime:
    if isinstance(value, str):
        try:
            value = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError as exc:
            raise ImportSafetyError("INVALID_CAPTURE_TIMESTAMP") from exc
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ImportSafetyError("CAPTURE_TIMESTAMP_REQUIRED")
    return value.astimezone(timezone.utc)


def _price(value: Any) -> Decimal:
    try:
        price = Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError) as exc:
        raise ImportSafetyError("INVALID_DECIMAL_ODDS") from exc
    if not price.is_finite() or not Decimal("1") < price <= Decimal("10000"):
        raise ImportSafetyError("OUT_OF_RANGE_ODDS")
    return price.quantize(Decimal("0.0001"))


def _canonical_quote(row: dict[str, Any]) -> dict:
    if row.get("provider") != "goaloo" or row.get("source") != "goaloo":
        raise ImportSafetyError("PROVIDER_MISMATCH")
    market = str(row.get("market", ""))
    selection = str(row.get("selection", ""))
    allowed = {"1X2": {"HOME", "DRAW", "AWAY"}, "AH": {"HOME", "AWAY"},
               "OU": {"OVER", "UNDER"}}
    if selection not in allowed.get(market, set()):
        raise ImportSafetyError("INVALID_MARKET_SELECTION")
    snapshot = str(row.get("snapshot_type", ""))
    if snapshot not in {"OPENING", "LATEST", "LIVE"}:
        raise ImportSafetyError("INVALID_SNAPSHOT")
    name = str(row.get("bookmaker", "")).strip()
    if not name or len(name) > 120:
        raise ImportSafetyError("INVALID_BOOKMAKER")
    provider_bookmaker_id = str(row.get("provider_bookmaker_id", "")).strip()
    if not provider_bookmaker_id:
        raise ImportSafetyError("BOOKMAKER_ID_REQUIRED")
    capture = _aware(row.get("captured_at"))
    line = None
    raw_line = row.get("raw_line")
    if market != "1X2":
        if row.get("line") is None or str(row.get("line")).strip() == "":
            raise ImportSafetyError("MARKET_LINE_REQUIRED")
        try:
            line = Decimal(str(row["line"]))
        except (InvalidOperation, ValueError) as exc:
            raise ImportSafetyError("INVALID_MARKET_LINE") from exc
        if not line.is_finite() or abs(line) > 100:
            raise ImportSafetyError("INVALID_MARKET_LINE")
    price = _price(row.get("odds"))
    key = f"{line:.3f}" if line is not None else ""
    provider_odds_id = str(row.get("provider_odds_id") or "")
    if not provider_odds_id:
        raise ImportSafetyError("PROVIDER_ODDS_ID_REQUIRED")
    return {
        "provider": "goaloo", "provider_odds_id": provider_odds_id[:160],
        "provider_market_id": market, "provider_bookmaker_id": provider_bookmaker_id[:160],
        "bookmaker": name, "market": market, "selection": selection,
        "line": line, "line_key": key, "raw_line": str(raw_line) if raw_line is not None else None,
        "odds": price, "captured_at": capture, "source": "goaloo",
        "snapshot_type": snapshot, "period": str(row.get("period") or "UNKNOWN"),
        "raw_value": row.get("raw_value"), "status": "AVAILABLE",
    }


def import_verified_odds(
    session: Session,
    decisions: list[MappingDecision],
    odds_by_goaloo: dict[str, list[dict]],
    *,
    store: ObjectStore | None = None,
    dry_run: bool = True,
    live_approved: bool = False,
) -> dict:
    """Run a bounded, archive-first, matched-only odds import.

    A second invocation with identical observation timestamps leaves SQL rows
    unchanged. A genuinely new observation timestamp is a new odds snapshot.
    R2 is content-addressed and re-read/verified before SQL is committed.
    """
    if not dry_run and (not live_approved or store is None):
        raise ImportSafetyError("LIVE_WRITES_REQUIRE_OPERATOR_APPROVAL_AND_ARCHIVE")
    if len(decisions) > 600 or sum(map(len, odds_by_goaloo.values())) > 50000:
        raise ImportSafetyError("IMPORT_SCOPE_EXCEEDED")
    stats = Counter()
    candidates = []
    seen_identity: set[tuple] = set()
    matched = [d for d in decisions if d.status == "MATCHED" and d.openligadb_id]
    if len({d.goaloo_id for d in matched}) != len(matched) or len({d.openligadb_id for d in matched}) != len(matched):
        raise ImportSafetyError("AMBIGUOUS_SOURCE_MAPPING")
    known_ids = {d.goaloo_id for d in decisions}
    if set(odds_by_goaloo) - known_ids:
        raise ImportSafetyError("UNMAPPED_SOURCE_ID")
    for decision in decisions:
        quotes = odds_by_goaloo.get(decision.goaloo_id, [])
        if decision.status != "MATCHED" or not decision.openligadb_id:
            stats["quarantined_quotes"] += len(quotes)
            continue
        if (decision.evidence.get("league_id") != "de.bl1"
                or decision.evidence.get("season") != "2026-2027"
                or decision.reason != "EXACT_TEAMS_AND_TIME"):
            stats["quarantined_quotes"] += len(quotes)
            continue
        match = session.scalars(select(ActiveMatch).where(
            ActiveMatch.provider == "openligadb",
            ActiveMatch.provider_match_id == str(decision.openligadb_id),
            ActiveMatch.league_id == "de.bl1",
            ActiveMatch.season == "2026-2027",
        )).one_or_none()
        if match is None:
            stats["missing_sql_match"] += 1
            stats["quarantined_quotes"] += len(quotes)
            continue
        for row in quotes:
            try:
                normalized = _canonical_quote(row)
            except ImportSafetyError:
                stats["invalid_quotes"] += 1
                continue
            identity = (str(match.id), normalized["bookmaker"], normalized["market"],
                        normalized["selection"], normalized["line_key"], normalized["snapshot_type"],
                        normalized["captured_at"])
            if identity in seen_identity:
                stats["duplicates_in_batch"] += 1
                continue
            seen_identity.add(identity)
            candidates.append((match, normalized, identity, decision.goaloo_id))
    stats["eligible_quotes"] = len(candidates)
    if dry_run or not candidates:
        return {"status": "DRY_RUN" if dry_run else "NO_ELIGIBLE_QUOTES", "dry_run": dry_run,
                "counts": dict(stats), "archive_receipt": None, "sql_inserted": 0}

    # First archive all eligible observations immutably, re-reading their bytes.
    # Archive data is independently auditable even if SQL later fails.
    records = []
    for match, row, _, source_id in candidates:
        records.append({
            "match_uuid": str(match.id), "openligadb_match_id": match.provider_match_id,
            "goaloo_match_id": source_id, "league_id": "de.bl1", "season": "2026-2027",
            "bookmaker": row["bookmaker"], "bookmaker_id": row["provider_bookmaker_id"],
            "market": row["market"], "selection": row["selection"], "line_key": row["line_key"],
            "odds": str(row["odds"]), "snapshot_type": row["snapshot_type"],
            "captured_at": row["captured_at"].isoformat(), "provider": "goaloo",
        })
    records.sort(key=lambda r: (r["match_uuid"], r["bookmaker"], r["market"],
                                 r["selection"], r["line_key"], r["snapshot_type"], r["captured_at"]))
    import json
    revision = sha256(json.dumps(records, sort_keys=True, separators=(",", ":")).encode()).hexdigest()[:24]
    archived_on = max(row["captured_at"] for _, row, _, _ in candidates).date()
    receipt = archive_partition(
        store, dataset="odds", league="de.bl1", season="2026-2027",
        day=archived_on, rows=records, retries=1, part=f"goaloo-sha{revision}")
    inserted = 0
    try:
        for match, row, identity, _ in candidates:
            existing = session.scalars(select(ActiveOdds.id).where(
                ActiveOdds.match_id == match.id,
                ActiveOdds.bookmaker == row["bookmaker"],
                ActiveOdds.market == row["market"],
                ActiveOdds.selection == row["selection"],
                ActiveOdds.line_key == row["line_key"],
                ActiveOdds.snapshot_type == row["snapshot_type"],
                ActiveOdds.captured_at == row["captured_at"],
            )).first()
            if existing:
                stats["already_in_sql"] += 1
                continue
            session.add(ActiveOdds(match_id=match.id,
                expires_at=max(row["captured_at"] + timedelta(days=2),
                               _aware(match.expires_at.replace(tzinfo=timezone.utc)
                                      if match.expires_at.tzinfo is None else match.expires_at)),
                **row))
            inserted += 1
        session.commit()
    except Exception:
        session.rollback()
        raise
    return {"status": "COMPLETED", "dry_run": False, "counts": dict(stats),
            "sql_inserted": inserted,
            "archive_receipt": {"key": receipt.key, "checksum": receipt.checksum,
                                "row_count": receipt.row_count}}
