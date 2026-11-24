from datetime import date
from functools import lru_cache
from pathlib import Path
import re
from urllib.parse import urlsplit

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

PROJECT_ROOT = Path(__file__).resolve().parents[3]
ENV_FILE = PROJECT_ROOT / ".env"


class Settings(BaseSettings):
    controlled_import_operator_id: str | None = None
    controlled_import_operator_token_sha256: SecretStr | None = None
    system_expected_database_fingerprint: str | None = None
    system_diagnostics_token: SecretStr | None = None
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
    sportmonks_api_token: str | None = None
    sportmonks_base_url: str = "https://api.sportmonks.com/v3/football"
    sportmonks_timeout_seconds: float = 15
    sportmonks_max_retries: int = 3
    goaloo_base_url: str = "https://football.goaloo.com"
    goaloo_odds_base_url: str = "https://www.goaloo.com"
    openligadb_base_url: str = "https://api.openligadb.de"
    openligadb_timeout_seconds: float = 10.0
    openligadb_requests_per_minute: int = 1
    bundesliga_ingest_enabled: bool = False
    bundesliga_ingest_dry_run: bool = True
    bundesliga_daily_max_requests: int = 10
    bundesliga_daily_max_r2_writes: int = 50
    bundesliga_daily_max_archive_mb: int = 10
    bundesliga_daily_max_matches: int = 50
    enabled_leagues: str = ""
    enabled_seasons: str = ""
    historical_start_date: date | None = None
    historical_collect_odds: bool = True

    r2_endpoint: str | None = None
    r2_access_key_id: str | None = None
    r2_secret_access_key: str | None = None
    r2_bucket: str | None = None
    r2_region: str = "auto"
    telegram_bot_token: str | None = None
    telegram_chat_id: str | None = None
    ai_provider: str | None = None
    ai_api_key: str | None = None
    groq_api_key: str | None = None
    groq_model: str | None = None
    groq_base_url: str = "https://api.groq.com/openai/v1"
    cors_origins: str = "http://localhost:3000"
    worker_interval_seconds: int = 300
    worker_match_interval_seconds: int = 3600
    worker_odds_interval_seconds: int = 1800
    worker_results_interval_seconds: int = 1800
    worker_scanner_interval_seconds: int = 1800
    worker_archive_interval_seconds: int = 900
    worker_cleanup_interval_seconds: int = 3600
    operational_retention_hours: int = Field(default=48, ge=1, le=48)
    operational_delete_enabled: bool = False
    auto_retention_delete_enabled: bool = False
    lifecycle_batch_size: int = Field(default=25, ge=1, le=100)
    lifecycle_child_limit: int = Field(default=500, ge=1, le=2000)
    worker_prune_interval_seconds: int = 86400
    job_run_stale_after_seconds: int = 21600
    historical_ingest_lease_seconds: int = 3600
    postgres_normal_mb: int = 300
    postgres_warning_mb: int = 500
    postgres_high_mb: int = 750
    postgres_critical_mb: int = 750
    job_history_retention_days: int = 30
    active_state_retention_days: int = 2  # Legacy compatibility; lifecycle uses hours, capped at 48.
    odds_retention_days: int = 2
    similarity_weak_threshold: int = 30
    similarity_moderate_threshold: int = 100
    similarity_strong_threshold: int = 500

    @property
    def provider_id(self) -> str | None:
        return self.provider_name or self.match_provider  # Never select a paid vendor from credentials.

    @property
    def provider_api_token(self) -> str | None:
        return self.sportmonks_api_token if (self.provider_id or "").lower() == "sportmonks" else self.provider_api_key

    @property
    def provider_api_base_url(self) -> str | None:
        if (self.provider_id or "").lower() == "sportmonks":
            return self.sportmonks_base_url or self.provider_base_url
        if (self.provider_id or "").lower() == "goaloo":
            return self.provider_base_url or self.goaloo_base_url
        if (self.provider_id or "").lower() == "openligadb":
            return self.openligadb_base_url
        return self.provider_base_url

    @property
    def provider_timeout_seconds(self) -> float:
        return self.sportmonks_timeout_seconds if (self.provider_id or "").lower() == "sportmonks" else self.provider_request_timeout_seconds

    @property
    def provider_retry_count(self) -> int:
        return self.sportmonks_max_retries if (self.provider_id or "").lower() == "sportmonks" else self.provider_max_retries

    @property
    def enabled_league_ids(self) -> tuple[str, ...]:
        return tuple(value.strip() for value in self.enabled_leagues.split(",") if value.strip())

    @property
    def enabled_season_ids(self) -> tuple[str, ...]:
        return tuple(value.strip() for value in self.enabled_seasons.split(",") if value.strip())

    @property
    def r2_configuration_issues(self) -> tuple[str, ...]:
        issues: list[str] = []
        endpoint = (self.r2_endpoint or "").strip()
        try:
            parsed = urlsplit(endpoint)
            endpoint_valid = parsed.scheme == "https" and bool(parsed.hostname) and not parsed.username and not parsed.password
            if parsed.port is not None and not 1 <= parsed.port <= 65535:
                endpoint_valid = False
        except ValueError:
            endpoint_valid = False
        if not endpoint_valid:
            issues.append("R2_ENDPOINT")
        if not (self.r2_access_key_id or "").strip():
            issues.append("R2_ACCESS_KEY_ID")
        if not (self.r2_secret_access_key or "").strip():
            issues.append("R2_SECRET_ACCESS_KEY")
        bucket = (self.r2_bucket or "").strip()
        if (not re.fullmatch(r"[a-z0-9][a-z0-9.-]{1,61}[a-z0-9]", bucket)
                or ".." in bucket or ".-" in bucket or "-." in bucket):
            issues.append("R2_BUCKET")
        return tuple(issues)

    @property
    def r2_missing_env(self) -> tuple[str, ...]:
        missing = []
        if not (self.r2_endpoint or "").strip(): missing.append("R2_ENDPOINT")
        if not (self.r2_access_key_id or "").strip(): missing.append("R2_ACCESS_KEY_ID")
        if not (self.r2_secret_access_key or "").strip(): missing.append("R2_SECRET_ACCESS_KEY")
        if not (self.r2_bucket or "").strip(): missing.append("R2_BUCKET")
        return tuple(missing)

    @property
    def r2_invalid_env(self) -> tuple[str, ...]:
        missing = set(self.r2_missing_env)
        return tuple(name for name in self.r2_configuration_issues if name not in missing)

    @property
    def r2_configured(self) -> bool:
        return not self.r2_configuration_issues

    @property
    def database_url_issues(self) -> tuple[str, ...]:
        value = (self.database_url or "").strip()
        if not value:
            return ("DATABASE_URL",)
        try:
            from sqlalchemy.engine import make_url
            parsed = make_url(value)
            if not parsed.drivername.startswith("postgresql") or not parsed.host or not parsed.database:
                return ("DATABASE_URL",)
        except Exception:
            return ("DATABASE_URL",)
        return ()

    @property
    def database_url_missing(self) -> bool:
        return not (self.database_url or "").strip()

    @property
    def database_url_valid(self) -> bool:
        return not self.database_url_issues

    @property
    def groq_configuration_issues(self) -> tuple[str, ...]:
        issues: list[str] = []
        if (self.ai_provider or "").strip().lower() != "groq":
            issues.append("AI_PROVIDER")
        if not any((value or "").strip() for value in (self.groq_api_key, self.ai_api_key)):
            issues.append("GROQ_API_KEY")
        if not (self.groq_model or "").strip():
            issues.append("GROQ_MODEL")
        base_url = (self.groq_base_url or "").strip()
        try:
            parsed = urlsplit(base_url)
            base_valid = parsed.scheme == "https" and bool(parsed.hostname) and not parsed.username and not parsed.password
            if parsed.port is not None and not 1 <= parsed.port <= 65535:
                base_valid = False
        except ValueError:
            base_valid = False
        if not base_valid:
            issues.append("GROQ_BASE_URL")
        return tuple(issues)

    @property
    def groq_missing_env(self) -> tuple[str, ...]:
        missing = []
        if (self.ai_provider or "").strip().lower() != "groq":
            missing.append("AI_PROVIDER")
        if not any((value or "").strip() for value in (self.groq_api_key, self.ai_api_key)):
            missing.append("GROQ_API_KEY")
        if not (self.groq_model or "").strip():
            missing.append("GROQ_MODEL")
        return tuple(missing)

    @property
    def groq_invalid_env(self) -> tuple[str, ...]:
        missing = set(self.groq_missing_env)
        return tuple(name for name in self.groq_configuration_issues if name not in missing)

    @property
    def groq_configured(self) -> bool:
        return not self.groq_configuration_issues

    model_config = SettingsConfigDict(env_file=ENV_FILE, extra="ignore")


@lru_cache
def get_settings() -> Settings:
    return Settings()
