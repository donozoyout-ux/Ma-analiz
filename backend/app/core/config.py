from datetime import date
from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_env: str = "development"
    log_level: str = "INFO"
    database_url: str | None = None

    match_provider: str | None = None
    provider_name: str | None = None
    provider_base_url: str | None = None
    provider_api_key: str | None = None
    provider_request_timeout_seconds: float = 15
    provider_requests_per_minute: int = 60
    provider_max_retries: int = 3
    enabled_leagues: str = ""
    enabled_seasons: str = ""
    historical_start_date: date | None = None

    r2_endpoint: str | None = None
    r2_access_key_id: str | None = None
    r2_secret_access_key: str | None = None
    r2_bucket: str = "sports-analytics"
    telegram_bot_token: str | None = None
    telegram_chat_id: str | None = None
    ai_provider: str | None = None
    ai_api_key: str | None = None
    cors_origins: str = "http://localhost:3000"
    worker_interval_seconds: int = 300
    postgres_normal_mb: int = 300
    postgres_warning_mb: int = 500
    postgres_high_mb: int = 750
    postgres_critical_mb: int = 750
    job_history_retention_days: int = 30
    active_state_retention_days: int = 14
    odds_retention_days: int = 30

    @property
    def provider_id(self) -> str | None:
        return self.provider_name or self.match_provider

    @property
    def enabled_league_ids(self) -> tuple[str, ...]:
        return tuple(value.strip() for value in self.enabled_leagues.split(",") if value.strip())

    @property
    def enabled_season_ids(self) -> tuple[str, ...]:
        return tuple(value.strip() for value in self.enabled_seasons.split(",") if value.strip())

    model_config = SettingsConfigDict(env_file="../.env", extra="ignore")


@lru_cache
def get_settings() -> Settings:
    return Settings()
