"""Offline validation primitives for a future publication ledger.

This module performs no persistence, publication, ingestion or migration.
Caller-supplied timestamps alone are not proof that a publication occurred.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
import re


def utc(value: datetime) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("TIMEZONE_REQUIRED")
    return value.astimezone(timezone.utc)


@dataclass(frozen=True)
class PublicationCandidate:
    match_id: str
    kickoff_at: datetime
    published_at: datetime
    market: str
    selection: str
    probability: Decimal
    model_version: str
    feature_sha256: str
    odds: Decimal | None = None
    odds_captured_at: datetime | None = None
    odds_source_ref: str | None = None


def validate_candidate(value: PublicationCandidate, *, trusted_now: datetime) -> None:
    """Validate a candidate, not the authenticity of its source or timestamp."""
    now, kickoff, published = utc(trusted_now), utc(value.kickoff_at), utc(value.published_at)
    if published > now or published >= kickoff:
        raise ValueError("PUBLICATION_NOT_BEFORE_KICKOFF_OR_IN_FUTURE")
    if any(not isinstance(item, str) or not item.strip() for item in
           (value.match_id, value.market, value.selection, value.model_version)):
        raise ValueError("MISSING_IDENTITY")
    if not isinstance(value.feature_sha256, str) or not re.fullmatch(r"[0-9a-f]{64}", value.feature_sha256):
        raise ValueError("INVALID_FEATURE_HASH")
    if not isinstance(value.probability, Decimal) or not value.probability.is_finite() or not Decimal(0) <= value.probability <= Decimal(1):
        raise ValueError("INVALID_PROBABILITY")
    if value.odds is None:
        if value.odds_captured_at is not None or value.odds_source_ref is not None:
            raise ValueError("INCOMPLETE_ODDS")
    else:
        if not isinstance(value.odds, Decimal) or not value.odds.is_finite() or value.odds <= 1:
            raise ValueError("INVALID_ODDS")
        if value.odds_captured_at is None or not isinstance(value.odds_source_ref, str) or not value.odds_source_ref.strip():
            raise ValueError("INCOMPLETE_ODDS")
        if utc(value.odds_captured_at) > published or utc(value.odds_captured_at) >= kickoff:
            raise ValueError("LATE_ODDS")


def outcome_1x2(*, market: str, selection: str, result_status: str,
                home: int | None, away: int | None, verified_at: datetime | None,
                result_source: str | None, kickoff_at: datetime,
                observed_now: datetime) -> str:
    """Resolve only explicit final 90-minute scores with provenance and timing."""
    if result_status != "FINAL" or verified_at is None or not isinstance(result_source, str) or not result_source.strip():
        return "UNSCORED"
    verified, kickoff, now = utc(verified_at), utc(kickoff_at), utc(observed_now)
    if verified < kickoff or verified > now:
        return "UNSCORED"
    if type(home) is not int or type(away) is not int or home < 0 or away < 0:
        return "UNSCORED"
    if market not in {"1X2", "FULL_TIME_RESULT", "MATCH_WINNER"}:
        return "UNSCORED"
    choices = {"1": "HOME", "X": "DRAW", "2": "AWAY", "HOME": "HOME", "DRAW": "DRAW", "AWAY": "AWAY"}
    chosen = choices.get(selection)
    if chosen is None:
        return "UNSCORED"
    actual = "HOME" if home > away else "AWAY" if away > home else "DRAW"
    return "WON" if chosen == actual else "LOST"


def hit_rate(outcomes: list[str]) -> tuple[int, int, Decimal | None]:
    wins, losses = outcomes.count("WON"), outcomes.count("LOST")
    return wins, losses, Decimal(wins) / Decimal(wins + losses) if wins + losses else None
