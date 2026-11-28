"""Strict *candidate-only* reconciliation between reviewed source identities.

Never imports, updates canonical identities, scores predictions, or infers a
Cornerflick slug. A matching candidate still requires independent human/source
review before linking two providers. This is deliberately schema-agnostic:
do not call with inferred JSON fields from an uninspected raw payload.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import re
import unicodedata
from typing import Iterable

from app.providers.cornerflick_open_data import MATCH_SLUG

_SEASON = re.compile(r"^(20[0-9]{2})-(20[0-9]{2})$")
_SPACE = re.compile(r"\s+")


@dataclass(frozen=True)
class SourceIdentity:
    provider: str
    provider_match_id: str
    league_id: str
    season: str
    home_team: str
    away_team: str
    kickoff_at: datetime


@dataclass(frozen=True)
class ReconciliationReview:
    status: str
    cornerflick_id: str
    openligadb_id: str | None = None
    explanation: str = ""


def _validate(identity: SourceIdentity) -> None:
    if identity.provider not in {"cornerflick", "openligadb"}:
        raise ValueError("UNSUPPORTED_SOURCE_IDENTITY")
    if identity.league_id != "de.bl1":
        raise ValueError("PILOT_BUNDESLIGA_ONLY")
    match = _SEASON.fullmatch(identity.season) if isinstance(identity.season, str) else None
    if match is None or int(match.group(2)) != int(match.group(1)) + 1:
        raise ValueError("INVALID_SEASON")
    if identity.kickoff_at.tzinfo is None or identity.kickoff_at.utcoffset() is None:
        raise ValueError("TIMEZONE_AWARE_KICKOFF_REQUIRED")
    for field in (identity.provider_match_id, identity.home_team, identity.away_team):
        if not isinstance(field, str) or not field.strip() or len(field) > 200:
            raise ValueError("INVALID_IDENTITY_FIELD")
    if identity.provider == "cornerflick":
        prefix = "cornerflick:match:"
        if not identity.provider_match_id.startswith(prefix):
            raise ValueError("CORNERFLICK_PERMANENT_ID_REQUIRED")
        slug = identity.provider_match_id.removeprefix(prefix)
        if not MATCH_SLUG.fullmatch(slug) or len(slug) > 150:
            raise ValueError("INVALID_CORNERFLICK_MATCH_SLUG")


def _name(value: str) -> str:
    # Conservative matching only. Never remove FC/1./SV or guess aliases.
    return _SPACE.sub(" ", unicodedata.normalize("NFKC", value).strip()).casefold()


def propose_reconciliation(
    cornerflick: SourceIdentity, openligadb_candidates: Iterable[SourceIdentity],
    *, tolerance_seconds: int = 60,
) -> ReconciliationReview:
    """Return a review proposal, never a verified cross-provider identity.

    SourceIdentity values must come from separately reviewed typed source
    records. An unverified match-score assertion is never included here.
    """
    _validate(cornerflick)
    if cornerflick.provider != "cornerflick":
        raise ValueError("CORNERFLICK_IDENTITY_REQUIRED")
    if type(tolerance_seconds) is not int or not 0 <= tolerance_seconds <= 60:
        raise ValueError("INVALID_TOLERANCE")

    matches: list[SourceIdentity] = []
    by_id: dict[str, SourceIdentity] = {}
    for candidate in openligadb_candidates:
        _validate(candidate)
        if candidate.provider != "openligadb":
            raise ValueError("OPENLIGADB_IDENTITY_REQUIRED")
        old = by_id.get(candidate.provider_match_id)
        if old is not None:
            if old != candidate:
                return ReconciliationReview("SOURCE_ID_CONFLICT", cornerflick.provider_match_id,
                    explanation="One OpenLigaDB provider ID maps to conflicting identities")
            return ReconciliationReview("AMBIGUOUS", cornerflick.provider_match_id,
                explanation="Duplicate candidates must be resolved")
        by_id[candidate.provider_match_id] = candidate
        if candidate.league_id != cornerflick.league_id or candidate.season != cornerflick.season:
            continue
        seconds = abs((candidate.kickoff_at.astimezone(timezone.utc) -
                       cornerflick.kickoff_at.astimezone(timezone.utc)).total_seconds())
        if seconds > tolerance_seconds:
            continue
        if _name(candidate.home_team) != _name(cornerflick.home_team):
            continue
        if _name(candidate.away_team) != _name(cornerflick.away_team):
            continue
        matches.append(candidate)

    if not matches:
        return ReconciliationReview("NO_MATCH", cornerflick.provider_match_id,
            explanation="No strict same-league, season, kickoff and team-order match")
    if len(matches) > 1:
        return ReconciliationReview("AMBIGUOUS", cornerflick.provider_match_id,
            explanation="Multiple OpenLigaDB IDs match; do not auto-link")
    return ReconciliationReview("REVIEW_REQUIRED", cornerflick.provider_match_id,
        openligadb_id=matches[0].provider_match_id,
        explanation="Identity candidate only; human/source and licence review needed")
