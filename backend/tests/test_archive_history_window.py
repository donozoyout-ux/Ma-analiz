"""Regression coverage for historical match visibility beyond the live 31-day window."""
from datetime import date, datetime, timezone

from app.api import match_center


NOW = datetime(2026, 11, 25, 12, 0, tzinfo=timezone.utc)


def test_history_default_window_includes_august_28_bundesliga_fixture():
    start, end = match_center.day_bounds(now=NOW, history_only=True)
    kickoff = datetime(2026, 8, 28, 18, 30, tzinfo=timezone.utc)
    assert start <= kickoff < end
    assert (end - start).days >= 119


def test_regular_match_feed_remains_a_bounded_31_day_window():
    start, end = match_center.day_bounds(now=NOW)
    august_fixture = datetime(2026, 8, 28, 18, 30, tzinfo=timezone.utc)
    assert not start <= august_fixture < end
    assert (end - start).days <= 32


def test_explicit_archive_date_remains_a_single_calendar_day():
    day = date(2026, 8, 28)
    start, end = match_center.day_bounds(day, now=NOW, history_only=True)
    assert start <= datetime(2026, 8, 28, 18, 30, tzinfo=timezone.utc) < end
    assert (end-start).days == 1


def test_history_list_view_uses_120_day_scope(monkeypatch):
    seen = {}
    def catalog(session, *, source, cursor, start, end, league):
        seen.update(source=source, start=start, end=end)
        return []
    monkeypatch.setattr(match_center, "catalog", catalog)
    result = match_center.list_view(object(), source="history", now=NOW)
    assert result["items"] == []
    assert seen["source"] == "history"
    assert seen["start"] <= datetime(2026, 8, 28, 18, 30, tzinfo=timezone.utc)
