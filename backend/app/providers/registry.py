"""Provider registry; no vendor endpoints or payload formats are assumed."""
from __future__ import annotations

import time
from collections.abc import Callable
from threading import Lock
from typing import Any

from app.core.config import Settings, get_settings
from app.providers.base import MatchProvider, ProviderFailure, ProviderNotConfigured, ProviderUnavailable


ProviderFactory = Callable[[Settings], MatchProvider]
_FACTORIES: dict[str, ProviderFactory] = {}


def _register_builtins() -> None:
    if "goaloo" not in _FACTORIES:
        from app.providers.goaloo import GoalooProvider
        register_provider("goaloo", lambda cfg: GoalooProvider(
            base_url=cfg.provider_api_base_url or cfg.goaloo_base_url,
            odds_base_url=cfg.goaloo_odds_base_url,
            league_ids=cfg.enabled_league_ids, season_ids=cfg.enabled_season_ids,
            timeout_seconds=cfg.provider_timeout_seconds,
            requests_per_minute=min(6, cfg.provider_requests_per_minute),
        ))


def register_provider(name: str, factory: ProviderFactory) -> None:
    key = name.strip().lower()
    if not key:
        raise ValueError("Provider name is required")
    _FACTORIES[key] = factory


def create_provider(settings: Settings | None = None) -> MatchProvider:
    cfg = settings or get_settings()
    _register_builtins()
    name = cfg.provider_id
    if (name or "").lower() == "sportmonks":
        raise ProviderNotConfigured("PAID_PROVIDER_DISABLED: Sportmonks is disabled; use a reviewed web source")
    if (name or "").lower() == "goaloo" and (len(cfg.enabled_league_ids) > 1 or len(cfg.enabled_season_ids) > 1):
        raise ProviderNotConfigured("SCRAPING_SCOPE_LIMIT: first rollout requires one league and one season; no automatic narrowing")
    credentials_required = (name or "").lower() != "goaloo"
    if not name or not cfg.provider_api_base_url or (credentials_required and not cfg.provider_api_token):
        raise ProviderNotConfigured("PROVIDER_NOT_CONFIGURED: set provider name, API base URL, and required credentials")
    if name.lower() == "mock":
        raise ProviderNotConfigured("MOCK_PROVIDER_TEST_ONLY")
    factory = _FACTORIES.get(name.lower())
    if factory is None:
        raise ProviderUnavailable(f"PROVIDER_UNAVAILABLE: no adapter registered for {name}")
    try:
        return factory(cfg)
    except ProviderNotConfigured:
        raise
    except Exception as exc:
        raise ProviderUnavailable("PROVIDER_UNAVAILABLE: adapter initialization failed") from exc


def provider_health(settings: Settings | None = None) -> dict[str, Any]:
    cfg = settings or get_settings()
    _register_builtins()
    provider_name = cfg.provider_id
    coverage = {"leagues": list(cfg.enabled_league_ids), "seasons": list(cfg.enabled_season_ids)}
    if (provider_name or "").lower() == "sportmonks":
        return {"provider": "SPORTMONKS", "status": "PAID_PROVIDER_DISABLED",
                "display_status": "DISABLED", "latency_ms": None, "error_code": "PAID_PROVIDER_DISABLED",
                "last_successful_request": None, "coverage": coverage}
    credentials_required = (provider_name or "").lower() != "goaloo"
    if not (provider_name and cfg.provider_api_base_url and (not credentials_required or cfg.provider_api_token)):
        return {"provider": (provider_name or "UNKNOWN").upper(), "status": "PROVIDER_NOT_CONFIGURED",
                "display_status": "NOT CONFIGURED", "latency_ms": None,
                "error_code": "PROVIDER_NOT_CONFIGURED", "last_successful_request": None, "coverage": coverage}
    if provider_name.lower() == "mock":
        return {"provider": "MOCK", "status": "PROVIDER_NOT_CONFIGURED", "display_status": "NOT CONFIGURED",
                "latency_ms": None, "error_code": "MOCK_PROVIDER_TEST_ONLY", "last_successful_request": None,
                "coverage": coverage}
    if provider_name.lower() not in _FACTORIES:
        return {"provider": provider_name.upper(), "status": "PROVIDER_UNAVAILABLE", "display_status": "OFFLINE",
                "latency_ms": None, "error_code": "PROVIDER_UNAVAILABLE", "last_successful_request": None,
                "coverage": coverage}
    provider = None
    try:
        provider = create_provider(cfg)
        probe = getattr(provider, "health_check", None)
        if probe is None:
            raise ProviderUnavailable("PROVIDER_UNAVAILABLE: adapter has no health check")
        ProviderCaller(provider, requests_per_minute=cfg.provider_requests_per_minute,
                       max_retries=cfg.provider_retry_count).call("health_check")
        return {"provider": provider_name.upper(), "status": "PROVIDER_OK", "display_status": "ONLINE",
                "latency_ms": getattr(provider, "last_latency_ms", None), "error_code": None,
                "last_successful_request": getattr(provider, "last_successful_request", None), "coverage": coverage}
    except ProviderFailure as exc:
        display = "DEGRADED" if exc.code == "PROVIDER_RATE_LIMITED" else "OFFLINE"
        return {"provider": provider_name.upper(), "status": exc.code, "display_status": display,
                "latency_ms": getattr(provider, "last_latency_ms", None), "error_code": exc.code,
                "last_successful_request": getattr(provider, "last_successful_request", None), "coverage": coverage}
    except Exception:
        return {"provider": provider_name.upper(), "status": "PROVIDER_ERROR", "display_status": "OFFLINE",
                "latency_ms": getattr(provider, "last_latency_ms", None), "error_code": "PROVIDER_ERROR",
                "last_successful_request": getattr(provider, "last_successful_request", None), "coverage": coverage}
    finally:
        close = getattr(provider, "close", None)
        if callable(close):
            close()


def provider_status(settings: Settings | None = None) -> str:
    return provider_health(settings)["status"]


class ProviderCaller:
    """Rate-limits all adapter calls and retries only bounded transient failures."""
    def __init__(self, provider: MatchProvider, *, requests_per_minute: int = 60,
                 max_retries: int = 3, sleep: Callable[[float], None] = time.sleep,
                 monotonic: Callable[[], float] = time.monotonic):
        if requests_per_minute < 1 or max_retries < 1:
            raise ValueError("Provider request limit and retry count must be positive")
        self.provider = provider
        self.interval = 60 / requests_per_minute
        self.max_retries = max_retries
        self.sleep = sleep
        self.monotonic = monotonic
        self._last_call: float | None = None
        self._lock = Lock()

    def call(self, method: str, *args: Any) -> Any:
        import httpx
        from app.providers.base import ProviderRateLimited, ProviderUnavailable
        operation = getattr(self.provider, method, None)
        if operation is None or not callable(operation):
            raise ProviderUnavailable(f"PROVIDER_UNAVAILABLE: adapter lacks {method}")
        for attempt in range(self.max_retries):
            with self._lock:
                now = self.monotonic()
                wait_for = max(0, self.interval - (now - self._last_call)) if self._last_call is not None else 0
                if wait_for:
                    self.sleep(wait_for)
                self._last_call = self.monotonic()
            try:
                return operation(*args)
            except ProviderRateLimited as exc:
                if attempt + 1 >= self.max_retries:
                    raise
                self.sleep(exc.retry_after if exc.retry_after is not None else min(30, 2 ** attempt))
            except (ProviderUnavailable, httpx.TimeoutException) as exc:
                if attempt + 1 >= self.max_retries:
                    raise ProviderUnavailable("PROVIDER_UNAVAILABLE: request timed out or failed") from exc
                self.sleep(min(30, 2 ** attempt))
        raise ProviderUnavailable("PROVIDER_UNAVAILABLE: retry budget exhausted")
