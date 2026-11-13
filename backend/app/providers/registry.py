"""Provider registry; no vendor endpoints or payload formats are assumed."""
from __future__ import annotations

import time
from collections.abc import Callable
from threading import Lock
from typing import Any

from app.core.config import Settings, get_settings
from app.providers.base import MatchProvider, ProviderNotConfigured, ProviderRateLimited, ProviderUnavailable


ProviderFactory = Callable[[Settings], MatchProvider]
_FACTORIES: dict[str, ProviderFactory] = {}


def register_provider(name: str, factory: ProviderFactory) -> None:
    key = name.strip().lower()
    if not key:
        raise ValueError("Provider name is required")
    _FACTORIES[key] = factory


def create_provider(settings: Settings | None = None) -> MatchProvider:
    cfg = settings or get_settings()
    name = cfg.provider_id
    if not name or not cfg.provider_base_url or not cfg.provider_api_key or not cfg.enabled_league_ids:
        raise ProviderNotConfigured("PROVIDER_NOT_CONFIGURED: set provider name, base URL, API key, and enabled leagues")
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


def provider_status(settings: Settings | None = None) -> str:
    cfg = settings or get_settings()
    if not (cfg.provider_id and cfg.provider_base_url and cfg.provider_api_key and cfg.enabled_league_ids):
        return "PROVIDER_NOT_CONFIGURED"
    if cfg.provider_id.lower() == "mock":
        return "PROVIDER_NOT_CONFIGURED"
    if cfg.provider_id.lower() not in _FACTORIES:
        return "PROVIDER_UNAVAILABLE"
    try:
        provider = create_provider(cfg)
        probe = getattr(provider, "health_check", None)
        if probe is None:
            return "PROVIDER_UNAVAILABLE"
        probe()
        return "PROVIDER_OK"
    except ProviderNotConfigured:
        return "PROVIDER_NOT_CONFIGURED"
    except ProviderRateLimited:
        return "PROVIDER_RATE_LIMITED"
    except ProviderUnavailable:
        return "PROVIDER_UNAVAILABLE"
    except Exception:
        return "PROVIDER_ERROR"


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
