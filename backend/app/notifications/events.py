"""Android push notification event contracts.

Strict data contracts for match events:
- MATCH_SCHEDULED: When a fixture is scheduled
- MATCH_STARTED: When kickoff arrives
- MATCH_FINISHED: When the match reaches full-time
- RESULT_VERIFIED: When the final result is cross-verified

No fake goals, scores, odds, or predictions are ever emitted.
Deterministic event IDs guarantee deduplication across Android clients.
"""
from __future__ import annotations

from enum import StrEnum
import hashlib
import json
from typing import Any
from uuid import UUID

from pydantic import BaseModel, Field


class MatchEventType(StrEnum):
    MATCH_SCHEDULED = "MATCH_SCHEDULED"
    MATCH_STARTED = "MATCH_STARTED"
    MATCH_FINISHED = "MATCH_FINISHED"
    RESULT_VERIFIED = "RESULT_VERIFIED"


class ScorePayload(BaseModel):
    home: int = Field(..., ge=0)
    away: int = Field(..., ge=0)


class MatchEventPayload(BaseModel):
    event_id: str = Field(..., description="Deterministic unique identifier for deduplication")
    event_type: MatchEventType
    match_id: str = Field(..., description="Canonical UUID of the match in active_matches")
    provider: str = Field(default="openligadb")
    provider_match_id: str
    league_id: str = Field(default="de.bl1")
    league_name: str = Field(default="Bundesliga")
    season: str = Field(default="2026-2027")
    home_team: str
    away_team: str
    start_time: str = Field(..., description="ISO 8601 UTC start time")
    status: str
    score: ScorePayload | None = None
    half_time_score: ScorePayload | None = None
    attribution: str = Field(default="Data: OpenLigaDB (https://www.openligadb.de), ODbL 1.0")
    occurred_at: str = Field(..., description="ISO 8601 UTC event generation timestamp")


def compute_event_id(
    event_type: str | MatchEventType,
    match_id: str | UUID,
    status: str,
    home_score: int | None = None,
    away_score: int | None = None,
) -> str:
    """Compute a deterministic hash for this event to prevent duplicate delivery."""
    components = [
        str(event_type),
        str(match_id),
        str(status),
        f"{home_score}-{away_score}" if home_score is not None and away_score is not None else "none",
    ]
    raw = ":".join(components)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def create_match_event(
    *,
    event_type: MatchEventType,
    match_id: str | UUID,
    provider_match_id: str,
    home_team: str,
    away_team: str,
    start_time: str,
    status: str,
    occurred_at: str,
    league_id: str = "de.bl1",
    league_name: str = "Bundesliga",
    season: str = "2026-2027",
    home_score: int | None = None,
    away_score: int | None = None,
    half_home_score: int | None = None,
    half_away_score: int | None = None,
    attribution: str = "Data: OpenLigaDB (https://www.openligadb.de), ODbL 1.0",
) -> MatchEventPayload:
    """Construct and validate a push notification event payload."""
    score_payload = None
    if home_score is not None and away_score is not None:
        score_payload = ScorePayload(home=home_score, away=away_score)

    ht_payload = None
    if half_home_score is not None and half_away_score is not None:
        ht_payload = ScorePayload(home=half_home_score, away=half_away_score)

    event_id = compute_event_id(
        event_type=event_type,
        match_id=str(match_id),
        status=status,
        home_score=home_score,
        away_score=away_score,
    )

    return MatchEventPayload(
        event_id=event_id,
        event_type=event_type,
        match_id=str(match_id),
        provider_match_id=str(provider_match_id),
        league_id=league_id,
        league_name=league_name,
        season=season,
        home_team=home_team,
        away_team=away_team,
        start_time=start_time,
        status=status,
        score=score_payload,
        half_time_score=ht_payload,
        attribution=attribution,
        occurred_at=occurred_at,
    )
