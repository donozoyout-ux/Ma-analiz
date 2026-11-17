from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

from app.core.config import ENV_FILE, PROJECT_ROOT, Settings


def test_env_file_is_absolute_and_anchored_at_project_root():
    assert ENV_FILE.is_absolute()
    assert ENV_FILE == PROJECT_ROOT / ".env"


def test_settings_load_same_project_env_from_root_and_backend_directory():
    assert ENV_FILE.exists(), "Project .env file is required for the working-directory check"
    backend = PROJECT_ROOT / "backend"
    script = (
        "import json; from app.core.config import Settings; s=Settings(); "
        "print(json.dumps({'provider':s.provider_id,'leagues':s.enabled_league_ids,"
        "'r2_configured':s.r2_configured,'database_url_valid':s.database_url_valid,"
        "'groq_configured':s.groq_configured},sort_keys=True))"
    )
    env = os.environ.copy()
    config_names = {
        "APP_ENV", "LOG_LEVEL", "DATABASE_URL", "MATCH_PROVIDER", "PROVIDER_NAME",
        "PROVIDER_BASE_URL", "PROVIDER_API_KEY", "SPORTMONKS_API_TOKEN", "ENABLED_LEAGUES",
        "ENABLED_SEASONS", "R2_ENDPOINT", "R2_ACCESS_KEY_ID", "R2_SECRET_ACCESS_KEY",
        "R2_BUCKET", "R2_REGION", "AI_PROVIDER", "AI_API_KEY", "GROQ_API_KEY",
        "GROQ_MODEL", "GROQ_BASE_URL", "TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID",
    }
    for name in config_names:
        env.pop(name, None)
    env["PYTHONPATH"] = str(backend)

    outputs = []
    for working_directory in (PROJECT_ROOT, backend):
        completed = subprocess.run([sys.executable, "-c", script], cwd=working_directory,
                                   env=env, capture_output=True, text=True, check=True)
        outputs.append(json.loads(completed.stdout.strip()))
    assert outputs[0] == outputs[1]


def test_missing_r2_postgres_and_groq_values_remain_unconfigured(monkeypatch):
    for name in ("DATABASE_URL", "R2_ENDPOINT", "R2_ACCESS_KEY_ID", "R2_SECRET_ACCESS_KEY",
                 "R2_BUCKET", "AI_PROVIDER", "AI_API_KEY", "GROQ_API_KEY", "GROQ_MODEL",
                 "GROQ_BASE_URL"):
        monkeypatch.delenv(name, raising=False)
    settings = Settings(_env_file=None)
    assert settings.r2_missing_env == (
        "R2_ENDPOINT", "R2_ACCESS_KEY_ID", "R2_SECRET_ACCESS_KEY", "R2_BUCKET")
    assert settings.r2_configured is False
    assert settings.database_url_missing is True
    assert settings.database_url_valid is False
    assert settings.groq_missing_env == ("AI_PROVIDER", "GROQ_API_KEY", "GROQ_MODEL")
    assert settings.groq_configured is False


def test_malformed_database_url_is_rejected_without_echoing_it(monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    settings = Settings(_env_file=None, database_url="not-a-valid-database-url")
    assert settings.database_url_valid is False
    assert settings.database_url_issues == ("DATABASE_URL",)
