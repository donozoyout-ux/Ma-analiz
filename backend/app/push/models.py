from enum import Enum
from typing import Any
from pydantic import BaseModel, Field, HttpUrl, field_validator


class NotificationType(str, Enum):
    MATCH_START_REMINDER = "match_start_reminder"
    VERIFIED_GOAL = "verified_goal"
    VERIFIED_MATCH_RESULT = "verified_match_result"
    FAVORITE_TEAM_UPDATES = "favorite_team_updates"
    AI_ANALYSIS_COMPLETE = "ai_analysis_complete"


class PushSubscriptionKeys(BaseModel):
    p256dh: str = Field(..., min_length=16, max_length=256)
    auth: str = Field(..., min_length=10, max_length=128)


class PushPreferences(BaseModel):
    enabled: bool = True
    favorite_teams: list[str] = Field(default_factory=list)
    notification_types: dict[str, bool] = Field(
        default_factory=lambda: {
            NotificationType.MATCH_START_REMINDER.value: True,
            NotificationType.VERIFIED_GOAL.value: True,
            NotificationType.VERIFIED_MATCH_RESULT.value: True,
            NotificationType.FAVORITE_TEAM_UPDATES.value: True,
            NotificationType.AI_ANALYSIS_COMPLETE.value: True,
        }
    )

    @field_validator("favorite_teams")
    @classmethod
    def clean_teams(cls, v: list[str]) -> list[str]:
        cleaned = []
        for team in v:
            t = team.strip()
            if t and len(t) <= 100 and t not in cleaned:
                cleaned.append(t)
        return cleaned[:50]  # Cap at 50 favorite teams


class PushSubscriptionCreate(BaseModel):
    endpoint: str = Field(..., min_length=10, max_length=2048)
    keys: PushSubscriptionKeys
    client_token: str = Field(..., min_length=16, max_length=128)
    preferences: PushPreferences = Field(default_factory=PushPreferences)

    @field_validator("endpoint")
    @classmethod
    def validate_endpoint(cls, v: str) -> str:
        v_clean = v.strip()
        if not (v_clean.startswith("https://") or v_clean.startswith("http://localhost")):
            raise ValueError("Endpoint must use HTTPS (or localhost for dev)")
        return v_clean


class PushSubscriptionUpdate(BaseModel):
    client_token: str = Field(..., min_length=16, max_length=128)
    enabled: bool | None = None
    favorite_teams: list[str] | None = None
    notification_types: dict[str, bool] | None = None


class PushUnsubscribeRequest(BaseModel):
    endpoint: str = Field(..., min_length=10, max_length=2048)
    client_token: str = Field(..., min_length=16, max_length=128)


class NotificationEvent(BaseModel):
    event_type: NotificationType
    match_id: str = Field(..., min_length=1, max_length=64)
    home_team: str = Field(..., min_length=1, max_length=128)
    away_team: str = Field(..., min_length=1, max_length=128)
    title: str = Field(..., min_length=1, max_length=128)
    body: str = Field(..., min_length=1, max_length=512)
    url: str = Field(default="/matches", max_length=512)
    verified: bool = Field(..., description="Must be strictly True; unverified events are rejected")
    detail_id: str | None = Field(default=None, max_length=128)
    tag: str | None = None
