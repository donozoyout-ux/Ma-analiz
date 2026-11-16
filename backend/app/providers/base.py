"""Provider-independent contract and bounded failure taxonomy."""
from __future__ import annotations

from datetime import date
from typing import Protocol


class MatchProvider(Protocol):
    name: str
    def get_leagues(self) -> list[dict]: ...
    def get_seasons(self, league_id: str) -> list[dict]: ...
    def get_matches(self, day: date) -> list[dict]: ...
    def get_match(self, match_id: str) -> dict | None: ...
    def get_odds(self, match_id: str) -> list[dict]: ...
    def get_results(self, day: date) -> list[dict]: ...


class ProviderFailure(RuntimeError):
    code = "PROVIDER_ERROR"


class ProviderNotConfigured(ProviderFailure):
    code = "PROVIDER_NOT_CONFIGURED"


class ProviderUnavailable(ProviderFailure):
    code = "PROVIDER_UNAVAILABLE"


class ProviderAuthError(ProviderFailure):
    code = "PROVIDER_AUTH_ERROR"


class ProviderRateLimited(ProviderFailure):
    code = "PROVIDER_RATE_LIMITED"

    def __init__(self, message: str = "Provider rate limit exceeded", retry_after: float | None = None):
        super().__init__(message)
        self.retry_after = retry_after


class ProviderError(ProviderFailure):
    code = "PROVIDER_ERROR"


class ProviderInvalidResponse(ProviderFailure):
    code = "PROVIDER_INVALID_RESPONSE"


class ProviderNotFound(ProviderFailure):
    code = "PROVIDER_NOT_FOUND"
