"""As-of bookmaker odds projections; live prices are never pre-match closing."""
from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Mapping


_PREMATCH_STATUS = {"ACTIVE", "AVAILABLE", "OPEN"}
_MATCH_STARTED = {"LIVE", "IN_PLAY", "PAUSED", "FINISHED"}


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _get(row: Any, key: str, default: Any = None) -> Any:
    return row.get(key, default) if isinstance(row, Mapping) else getattr(row, key, default)


def _snapshot(row: Any) -> dict[str, Any]:
    price = _get(row, "price")
    if price is None:
        price = _get(row, "odds")
    captured_at = _get(row, "captured_at")
    if captured_at.tzinfo is None or captured_at.utcoffset() is None:
        captured_at = captured_at.replace(tzinfo=timezone.utc)
    else:
        captured_at = captured_at.astimezone(timezone.utc)
    return {"price": price, "captured_at": captured_at, "source": _get(row, "source"),
            "period": _get(row, "period", "UNKNOWN"), "status": _get(row, "status", "AVAILABLE"),
            "snapshot_type": _get(row, "snapshot_type", "OBSERVED")}


def project_odds_snapshots(rows: list[Any], *, kickoff: datetime, match_status: str,
                           as_of: datetime) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Return bookmaker/market opening-latest-closing and separate live prices.

    All fields are selected from observed records. Closing is available only
    after a provider-confirmed started state, and only from an explicitly
    labelled CLOSING PREMATCH snapshot. A latest quote is never inferred to be
    closing, and a live quote can never enter this projection.
    """
    if as_of.tzinfo is None or as_of.utcoffset() is None:
        raise ValueError("Odds projection cutoff must be timezone-aware")
    if kickoff.tzinfo is None or kickoff.utcoffset() is None:
        # SQLite test databases drop the timezone flag from DateTime(timezone=True).
        kickoff = kickoff.replace(tzinfo=timezone.utc)
    else:
        kickoff = kickoff.astimezone(timezone.utc)
    as_of = as_of.astimezone(timezone.utc)
    status = match_status.strip().upper()
    groups: dict[tuple[str, str, str, str], list[Any]] = {}
    live: list[dict[str, Any]] = []
    for row in rows:
        captured_at = _get(row, "captured_at")
        if captured_at.tzinfo is None or captured_at.utcoffset() is None:
            captured_at = captured_at.replace(tzinfo=timezone.utc)
        else:
            captured_at = captured_at.astimezone(timezone.utc)
        if captured_at > as_of:
            continue
        period = str(_get(row, "period", "UNKNOWN")).upper()
        if period == "LIVE":
            live.append({"bookmaker": _get(row, "bookmaker"), "market": _get(row, "market"),
                         "selection": _get(row, "selection"), "line": _get(row, "line"), **_snapshot(row)})
            continue
        if period != "PREMATCH" or str(_get(row, "status", "AVAILABLE")).upper() not in _PREMATCH_STATUS:
            continue
        snapshot_type = str(_get(row, "snapshot_type", "OBSERVED")).upper()
        if captured_at > kickoff and snapshot_type not in {"OPENING", "CLOSING"}:
            continue
        key = (_get(row, "bookmaker"), _get(row, "market"), _get(row, "selection"),
               str(_get(row, "line_key", "") or ""))
        groups.setdefault(key, []).append(row)

    started = status in _MATCH_STARTED
    projected: list[dict[str, Any]] = []
    for (bookmaker, market, selection, line_key), snapshots in sorted(groups.items()):
        snapshots.sort(key=lambda row: (_get(row, "captured_at"), 0 if str(_get(row, "snapshot_type", "OBSERVED")).upper() == "OPENING" else 1))
        opening_rows = [row for row in snapshots if str(_get(row, "snapshot_type", "OBSERVED")).upper() == "OPENING"]
        latest_rows = [row for row in snapshots
                       if str(_get(row, "snapshot_type", "OBSERVED")).upper() not in {"OPENING", "CLOSING"}
                       and _as_utc(_get(row, "captured_at")) <= kickoff]
        opening = opening_rows[0] if opening_rows else snapshots[0]
        source_closings = [row for row in snapshots if str(_get(row, "snapshot_type", "")).upper() == "CLOSING"]
        latest = latest_rows[-1] if latest_rows else source_closings[-1] if started and source_closings else opening
        closing = source_closings[-1] if started and source_closings else None
        projected.append({
            "bookmaker": bookmaker,
            "market": market,
            "selection": selection,
            "line": _get(latest, "line"),
            "line_key": line_key,
            "opening": _snapshot(opening),
            "latest": _snapshot(latest),
            "closing": _snapshot(closing) if closing is not None else None,
        })
    return projected, live


def movement_percent(opening: Decimal | float | None, latest: Decimal | float | None) -> Decimal | None:
    """Calculate display-only price movement; no values are inferred."""
    if opening is None or latest is None:
        return None
    opening, latest = Decimal(str(opening)), Decimal(str(latest))
    if not opening.is_finite() or not latest.is_finite() or opening <= 0:
        return None
    return ((latest - opening) / opening * Decimal("100")).quantize(Decimal("0.01"))
