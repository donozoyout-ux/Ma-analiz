from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_env: str = "development"
    log_level: str = "INFO"
    database_url: str | None = None
    match_provider: str | None = None
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
    postgres_critical_mb: int = 1000
    job_history_retention_days: int = 30

    model_config = SettingsConfigDict(env_file="../.env", extra="ignore")


@lru_cache
def get_settings() -> Settings:
    return Settings()
