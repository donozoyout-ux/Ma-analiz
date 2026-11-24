"""Isolated ephemeral PostgreSQL contract for Bundesliga durable quota.

No production database, credentials, or R2 access is used.
"""
import os

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.models import SystemSetting
from app.storage.quota import DurableQuotaTracker, QuotaExceededError


def test_durable_quota_survives_new_tracker_and_enforces_limits():
    url = os.environ.get("BUNDESLIGA_TEST_DATABASE_URL")
    if not url:
        pytest.skip("Ephemeral PostgreSQL URL not configured")
    engine = create_engine(url, pool_pre_ping=True)
    SystemSetting.__table__.create(engine, checkfirst=True)
    try:
        settings = Settings(
            bundesliga_daily_max_requests=2,
            bundesliga_daily_max_r2_writes=2,
            bundesliga_daily_max_archive_mb=1,
            bundesliga_daily_max_matches=9,
            _env_file=None,
        )
        first = DurableQuotaTracker(settings, engine)
        first.reserve_request()
        first.reserve_matches(3)
        first.reserve_r2_write(1, 500)
        second = DurableQuotaTracker(settings, engine)
        second.reserve_request()
        summary = second.get_summary()
        assert summary["requests"]["used"] == 2
        assert summary["matches"]["used"] == 3
        assert summary["r2_writes"]["used"] == 1
        assert summary["r2_bytes"]["used"] == 500
        with pytest.raises(QuotaExceededError, match="daily_requests"):
            second.reserve_request()
        with pytest.raises(QuotaExceededError, match="daily_r2_bytes"):
            second.reserve_r2_write(1, 2 * 1024 * 1024)
        assert first.get_summary()["requests"]["used"] == 2
    finally:
        SystemSetting.__table__.drop(engine, checkfirst=True)
        engine.dispose()
