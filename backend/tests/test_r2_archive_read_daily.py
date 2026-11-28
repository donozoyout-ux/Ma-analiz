"""Tests use only in-memory R2, never production DB or Cloudflare."""
from datetime import date, datetime, timezone
import json

import pytest

from app.api.r2_archive import read_verified_archive
from app.jobs import daily_r2_matches as daily
from app.jobs.first_ucl_r2_archive import archive_source_only
from app.storage.r2 import InMemoryObjectStore


def archived_rows():
    return [{
        "id": str(i), "league": "uefa.cl", "season": "2024-2025",
        "home_team": "Team A", "away_team": "Team B",
        "date": "2024-11-01", "kickoff_utc": None,
        "round": "League", "status": "FINISHED",
        "regulation": [1, 0], "verification": "SOURCE_REPORTED",
    } for i in range(189)]


def test_read_verified_r2_archive_and_corruption(monkeypatch):
    from app.jobs import first_ucl_r2_archive as writer
    monkeypatch.setattr(writer, "validate_source", lambda *args: archived_rows())
    store = InMemoryObjectStore()
    report = archive_source_only(store, b"fixture-source")
    # Exercise all checks with isolated fixture source digest.
    from app.api import r2_archive as reader
    monkeypatch.setattr(reader, "SOURCE_SHA256", report["receipts"]["raw"]["sha256"])
    assert len(read_verified_archive(store)) == 189
    key = report["receipts"]["matches"]["key"]
    store.put_object(key, b"corrupted", content_type="application/octet-stream")
    with pytest.raises(ValueError, match="ARCHIVE_OBJECT_HASH_MISMATCH"):
        read_verified_archive(store)


def match(mid="1", score=None):
    return {
        "provider_match_id": mid, "league_id": "de.bl1", "match_date": date(2026, 11, 28),
        "home_team": "Example Home", "away_team": "Example Away",
        "start_time": datetime(2026, 11, 28, 18, tzinfo=timezone.utc),
        "status": "FINISHED" if score else "SCHEDULED", "score": score,
    }


class FakeProvider:
    data = [match()]
    def __init__(self, **kwargs):
        pass
    def __enter__(self):
        return self
    def __exit__(self, *args):
        return False
    def get_matches(self, day):
        return self.data
    def get_competition_matches(self, shortcut, day):
        raise AssertionError("Unexpected league requested")


def test_daily_r2_live_requires_approval_before_io():
    with pytest.raises(RuntimeError, match="DAILY_R2_OPERATOR_APPROVAL_REQUIRED"):
        daily.run(day=date(2026, 11, 28), live=True,
                  provider_factory=lambda **kwargs: pytest.fail("source accessed"))


def test_daily_r2_dry_run_avoids_storage():
    report = daily.run(day=date(2026, 11, 28), provider_factory=FakeProvider,
                       store_factory=lambda: pytest.fail("R2 accessed"))
    assert report["status"] == "DRY_RUN"
    assert report["postgresql"] == "NOT_USED"
    assert report["r2"] == "NOT_ACCESSED"


def test_daily_r2_snapshot_reuses_and_changes_when_score_changes(monkeypatch):
    monkeypatch.setenv("DAILY_R2_EXPECTED_BUCKET", "test-daily-bucket")
    store = InMemoryObjectStore()
    store.bucket = "test-daily-bucket"
    day = date(2026, 11, 28)
    try:
        FakeProvider.data = [match()]
        first = daily.run(day=day, live=True, approval=daily.APPROVAL,
                          provider_factory=FakeProvider, store_factory=lambda: store)
        second = daily.run(day=day, live=True, approval=daily.APPROVAL,
                           provider_factory=FakeProvider, store_factory=lambda: store)
        assert first["leagues"][0]["key"] == second["leagues"][0]["key"]
        assert len(store.list_objects()) == 1
        FakeProvider.data = [match(score=[2, 1])]
        updated = daily.run(day=day, live=True, approval=daily.APPROVAL,
                            provider_factory=FakeProvider, store_factory=lambda: store)
        assert updated["leagues"][0]["key"] != first["leagues"][0]["key"]
        assert len(store.list_objects()) == 2
        assert json.loads(store.get_object(updated["leagues"][0]["key"]))["matches"][0]["score"] == [2, 1]
    finally:
        FakeProvider.data = [match()]


def test_daily_rejects_wrong_date_and_duplicate_without_writes():
    with pytest.raises(RuntimeError, match="DAY_OUT_OF_REVIEWED_SEASON"):
        daily.run(day=date(2025, 11, 28), provider_factory=FakeProvider)
    with pytest.raises(ValueError, match="DAILY_DUPLICATE_MATCH_ID"):
        daily.validate_matches([match(), match()], date(2026, 11, 28), "de.bl1")


def test_daily_live_rejects_unexpected_bucket_without_writes(monkeypatch):
    monkeypatch.setenv("DAILY_R2_EXPECTED_BUCKET", "approved-bucket")
    store = InMemoryObjectStore()
    store.bucket = "different-bucket"
    with pytest.raises(RuntimeError, match="DAILY_R2_BUCKET_MISMATCH"):
        daily.run(day=date(2026, 11, 28), live=True, approval=daily.APPROVAL,
                  provider_factory=FakeProvider, store_factory=lambda: store)
    assert store.list_objects() == []


def test_daily_live_requires_explicit_target_bucket(monkeypatch):
    monkeypatch.delenv("DAILY_R2_EXPECTED_BUCKET", raising=False)
    with pytest.raises(RuntimeError, match="DAILY_R2_EXPECTED_BUCKET_REQUIRED"):
        daily.run(day=date(2026, 11, 28), live=True, approval=daily.APPROVAL,
                  provider_factory=FakeProvider,
                  store_factory=lambda: pytest.fail("R2 accessed"))


def test_daily_ignores_source_bookkeeping_timestamp(monkeypatch):
    monkeypatch.setenv("DAILY_R2_EXPECTED_BUCKET", "sdfgdsgdg")
    store = InMemoryObjectStore()
    store.bucket = "sdfgdsgdg"
    FakeProvider.data = [{**match(), "raw_source_updated_at": "2026-11-28T09:00:00"}]
    try:
        first = daily.run(day=date(2026, 11, 28), live=True, approval=daily.APPROVAL,
                          provider_factory=FakeProvider, store_factory=lambda: store)
        FakeProvider.data = [{**match(), "raw_source_updated_at": "2026-11-28T09:15:00"}]
        second = daily.run(day=date(2026, 11, 28), live=True, approval=daily.APPROVAL,
                           provider_factory=FakeProvider, store_factory=lambda: store)
        assert first["leagues"][0]["key"] == second["leagues"][0]["key"]
        assert len(store.list_objects()) == 1
    finally:
        FakeProvider.data = [match()]


def test_daily_empty_and_missing_score(monkeypatch):
    monkeypatch.setenv("DAILY_R2_EXPECTED_BUCKET", "sdfgdsgdg")
    store = InMemoryObjectStore()
    store.bucket = "sdfgdsgdg"
    try:
        FakeProvider.data = []
        report = daily.run(day=date(2026, 11, 28), live=True, approval=daily.APPROVAL,
                           provider_factory=FakeProvider, store_factory=lambda: store)
        assert report["leagues"][0]["status"] == "NO_MATCHES_REPORTED"
        assert store.list_objects() == []
        FakeProvider.data = [{**match(), "status": "FINISHED", "score": None}]
        report = daily.run(day=date(2026, 11, 28), live=True, approval=daily.APPROVAL,
                           provider_factory=FakeProvider, store_factory=lambda: store)
        assert json.loads(store.get_object(report["leagues"][0]["key"]))["matches"][0]["score"] is None
    finally:
        FakeProvider.data = [match()]


def test_daily_network_error_never_constructs_store():
    class Offline(FakeProvider):
        def get_matches(self, day):
            raise OSError("source unavailable")
    with pytest.raises(OSError, match="source unavailable"):
        daily.run(day=date(2026, 11, 28), live=True, approval=daily.APPROVAL,
                  provider_factory=Offline, store_factory=lambda: pytest.fail("R2 accessed"))


def test_daily_partial_archive_retries_without_overwriting(monkeypatch):
    monkeypatch.setenv("DAILY_R2_EXPECTED_BUCKET", "sdfgdsgdg")
    store = InMemoryObjectStore()
    store.bucket = "sdfgdsgdg"
    put = store.put_object_if_absent
    def upload_then_fail(*args, **kwargs):
        put(*args, **kwargs)
        raise OSError("connection lost after upload")
    monkeypatch.setattr(store, "put_object_if_absent", upload_then_fail)
    with pytest.raises(OSError, match="connection lost"):
        daily.run(day=date(2026, 11, 28), live=True, approval=daily.APPROVAL,
                  provider_factory=FakeProvider, store_factory=lambda: store)
    assert len(store.list_objects()) == 1
    monkeypatch.setattr(store, "put_object_if_absent", lambda *a, **kw: pytest.fail("duplicate PUT"))
    result = daily.run(day=date(2026, 11, 28), live=True, approval=daily.APPROVAL,
                       provider_factory=FakeProvider, store_factory=lambda: store)
    assert result["leagues"][0]["status"] == "R2_VERIFIED"
    assert len(store.list_objects()) == 1


def test_previous_day_and_today_share_one_request_across_istanbul_midnight(monkeypatch):
    from zoneinfo import ZoneInfo
    tz = ZoneInfo("Europe/Istanbul")
    current = datetime(2026, 11, 28, 21, 5, tzinfo=timezone.utc).astimezone(tz).date()
    calls = []
    class WindowProvider(FakeProvider):
        def get_competition_window(self, shortcut, start, end):
            calls.append((shortcut, start, end))
            return [{**match("1"), "match_date": start}, {**match("2"), "match_date": end}]
    report = daily.run(day=current, include_previous_day=True, provider_factory=WindowProvider,
                       store_factory=lambda: pytest.fail("R2 accessed"))
    assert calls == [("bl1", date(2026, 11, 28), date(2026, 11, 29))]
    assert [(item["date"], item["matches"]) for item in report["leagues"]] == [
        ("2026-11-28", 1), ("2026-11-29", 1)]


def test_elapsed_kickoff_status_is_not_certified_live():
    data = daily.validate_matches([{**match(), "status": "LIVE"}], date(2026, 11, 28), "de.bl1")
    assert data[0]["status"] == "IN_PROGRESS_UNCONFIRMED"
