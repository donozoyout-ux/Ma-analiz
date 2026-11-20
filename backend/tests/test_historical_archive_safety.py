from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

import httpx
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.historical.repository import HistoricalRepository
from app.jobs.ingestion import archive_rows, historical_ingest, cleanup_verified_archives
from app.models import ActiveMatch, ActiveOdds, ArchiveJob, Bookmaker, HistoricalIngestDay
from app.providers.base import ProviderAccessRestricted, ProviderInvalidResponse, ProviderUnavailable
from app.providers.goaloo import GoalooProvider
from app.providers.goaloo_statistics import parse_statistics
from app.providers.registry import ProviderCaller
from app.storage.archive import SQLArchiveTracker
from app.storage.r2 import InMemoryObjectStore

DAY = date(2025, 8, 15)
AT = datetime(2025, 8, 15, 19, tzinfo=timezone.utc)


def test_statistics_parser_preserves_zero_percentage_missing_and_period():
    rows = parse_statistics('''<ul id="ftstat"><li><span class="stat-c">0</span>
        <span class="stat-title">Shots</span><span class="stat-c">-</span></li>
        <li><span class="stat-c">61%</span><span class="stat-title">Possession</span>
        <span class="stat-c">39%</span></li></ul>''')
    assert rows[0]["home_value"] == 0 and rows[0]["away_value"] is None
    assert rows[1]["home_value"] == 61 and rows[1]["unit"] == "percent"
    assert rows[0]["period"] == "ftstat"
    with pytest.raises(ProviderInvalidResponse):
        parse_statistics("<html>captcha</html>")
    with pytest.raises(ProviderInvalidResponse):
        parse_statistics('<ul id="ftstat"><li><span class="stat-title">Shots</span></li></ul>')


def test_statistics_request_failure_and_redirect_are_never_empty_success():
    for response in (httpx.Response(403), httpx.Response(200, text="<html>challenge</html>")):
        with httpx.Client(transport=httpx.MockTransport(lambda request: response)) as client:
            provider = GoalooProvider(client=client)
            with pytest.raises((ProviderUnavailable, ProviderInvalidResponse, ProviderAccessRestricted)):
                provider.get_statistics("2789129")


def test_robots_policy_blocks_ajax_before_any_odds_download_and_is_cached():
    paths = []
    def handler(request):
        paths.append(request.url.path)
        assert request.url.path == "/robots.txt"
        return httpx.Response(200, text="User-agent: *\nDisallow: /ajax/\n")
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        provider = GoalooProvider(client=client, requests_per_minute=1000000)
        provider._fixture_cache["2789129"] = {"status": "FINISHED"}
        for _ in range(2):
            with pytest.raises(ProviderAccessRestricted):
                provider.get_odds("2789129")
        assert paths == ["/robots.txt"]


def test_late_arrivals_append_without_overwrite_and_reader_discovers_supplements():
    store = InMemoryObjectStore()
    kwargs = dict(dataset="matches", season="2025-2026", league="36", day=DAY)
    a = {"provider": "p", "provider_match_id": "a", "start_time": AT}
    b = {**a, "provider_match_id": "b"}
    archive_rows(store, None, rows=[a], **kwargs)
    original_key = store.list_objects()[0].key
    original = store.get_object(original_key)
    assert archive_rows(store, None, rows=[a, b], **kwargs)["records_written"] == 1
    assert archive_rows(store, None, rows=[b, a], **kwargs)["records_written"] == 0
    assert store.get_object(original_key) == original
    keys = HistoricalRepository(store).partition_keys(dataset="matches", season="2025-2026", league="36", start=AT, end=AT)
    assert len(keys) == 2
    assert len(HistoricalRepository(store)._read(keys, before=AT)) == 2
    info = store.head_object(original_key)
    store.put_object(original_key, original + b"bad", content_type="application/vnd.apache.parquet", metadata=info.metadata)
    with pytest.raises(IOError):
        archive_rows(store, None, rows=[a, b], **kwargs)


class Adapter:
    name = "goaloo"
    statistics_supported = True
    fail_second = True

    def get_leagues(self): return [{"id": "36"}]
    def get_matches(self, day):
        return [{"provider_match_id": identity, "league_id": "36", "season": "2025-2026",
                 "home_team": "H", "away_team": "A", "start_time": AT, "status": "FINISHED"}
                for identity in ("a", "b")]
    def get_results(self, day):
        return [{"provider_match_id": identity, "home_score": 1, "away_score": 0, "status": "FINAL"}
                for identity in ("a", "b")]
    def get_odds(self, identity):
        if identity == "b" and self.fail_second:
            raise ProviderUnavailable("PROVIDER_UNAVAILABLE")
        return [{"provider_odds_id": kind, "bookmaker": "Book", "market": "1X2", "selection": "HOME",
                 "odds": Decimal("2.1"), "period": "PREMATCH", "snapshot_type": kind}
                for kind in ("OPENING", "LATEST", "LIVE")]
    def get_statistics(self, identity):
        return [{"id": "shots", "name": "Shots", "home_value": 0, "away_value": 2}]


def test_partial_day_resumes_preserves_each_match_odds_and_statistics(monkeypatch):
    settings = Settings(_env_file=None, enabled_leagues="36", enabled_seasons="2025-2026")
    monkeypatch.setattr("app.jobs.ingestion.get_settings", lambda: settings)
    engine = create_engine("sqlite://")
    for model in (HistoricalIngestDay, ArchiveJob): model.__table__.create(engine)
    adapter = Adapter()
    caller = ProviderCaller(adapter, requests_per_minute=1000000, max_retries=1)
    store = InMemoryObjectStore()
    with Session(engine) as session:
        args = dict(season="2025-2026", league="36", start_date=DAY, end_date=DAY)
        first = historical_ingest(session, caller, store, **args)
        assert first["status"] == "PARTIAL" and first["odds_written"] == 1
        assert session.query(HistoricalIngestDay).one().coverage["odds:b"] == "FAILED"
        adapter.fail_second = False
        second = historical_ingest(session, caller, store, **args)
        assert second["status"] == "SUCCESS" and second["odds_written"] == 1
        assert session.query(HistoricalIngestDay).one().status == "COMPLETED"
        objects = store.list_objects()
        third = historical_ingest(session, caller, store, **args)
        assert third["records_written"] == 0 and store.list_objects() == objects
        rows = [row for obj in objects if obj.key.startswith("odds/") for row in store.read_parquet(obj.key)]
        assert {row["provider_match_id"] for row in rows} == {"a", "b"}
        assert {row["snapshot_type"] for row in rows} == {"OPENING"}
        assert all(row["source_captured_at"] is None for row in rows)
        stats = [row for obj in objects if obj.key.startswith("statistics/") for row in store.read_parquet(obj.key)]
        assert len(stats) == 2 and all(row["home_value"] == 0 for row in stats)
    engine.dispose()


def test_cleanup_preserves_unarchived_unexpired_and_modified_sql_rows():
    engine = create_engine("sqlite://")
    for model in (ActiveMatch, Bookmaker, ActiveOdds, ArchiveJob): model.__table__.create(engine)
    store = InMemoryObjectStore()
    with Session(engine) as session:
        match = ActiveMatch(provider="p", provider_match_id="m", season="2025-2026", league_id="36",
                            home_team="H", away_team="A", start_time=AT, status="FINISHED", expires_at=AT)
        session.add(match); session.flush()
        odds = [ActiveOdds(match_id=match.id, provider="p", bookmaker=str(i), market="1X2", selection="HOME",
                           odds=Decimal("2"), captured_at=AT, expires_at=AT) for i in range(4)]
        session.add_all(odds); session.commit()
        rows = [{"odds_id": str(item.id), "match_id": str(match.id), "odds": 2.0, "captured_at": AT,
                 "provider": "p", "bookmaker": item.bookmaker, "market": "1X2", "selection": "HOME", "line": None,
                 "snapshot_type": "OBSERVED", "period": "UNKNOWN", "source": "unknown"}
                for item in odds[:3]]
        archive_rows(store, SQLArchiveTracker(session), dataset="odds", season="2025-2026", league="36", day=DAY, rows=rows)
        odds[1].expires_at = datetime.now(timezone.utc) + timedelta(days=1)
        odds[2].odds = Decimal("3")
        session.commit()
        assert cleanup_verified_archives(session, store) == 1
        assert {item.bookmaker for item in session.query(ActiveOdds)} == {"1", "2", "3"}
        assert store.list_objects()  # Database cleanup never deletes R2.
    engine.dispose()


def test_parquet_smoke_roundtrip_uses_zstd_and_cleans_only_probe():
    from app.jobs.r2_smoke_test import run_smoke
    from test_r2_smoke_test import configured_settings
    store = InMemoryObjectStore()
    report = run_smoke(settings=configured_settings(), store_factory=lambda: store, parquet=True)
    assert report["status"] == "CONNECTED" and report["parquet_rows"] == "PASS"
    assert not store.list_objects()


def test_migration_bootstrap_does_not_precreate_columns_added_by_later_revisions():
    import io
    from pathlib import Path
    from alembic import command
    from alembic.config import Config
    output = io.StringIO()
    root = Path(__file__).resolve().parents[1]
    cfg = Config(str(root / "alembic.ini"), output_buffer=output)
    cfg.set_main_option("script_location", str(root / "migrations"))
    command.upgrade(cfg, "head", sql=True)
    sql = output.getvalue()
    bootstrap = sql.split("-- Running upgrade 0001_initial")[0]
    assert "snapshot_type" not in bootstrap
    assert "lease_token" not in bootstrap
    assert "coverage JSON" not in bootstrap
    assert "CREATE TABLE match_analyses" not in bootstrap
    assert sql.count("ADD COLUMN coverage") == 1
    assert sql.count("ADD COLUMN snapshot_type") == 1
    assert sql.count("CREATE TABLE match_analyses") == 1


def test_source_odds_timestamp_is_required_for_observed_history_and_never_inferred(monkeypatch):
    class LicensedAdapter(Adapter):
        name = "licensed-test"
        def get_odds(self, identity):
            base = {"provider_odds_id": "q", "bookmaker": "Book", "market": "1X2", "selection": "HOME",
                    "odds": 2.0, "period": "PREMATCH"}
            return [{**base, "captured_at": AT - timedelta(minutes=10)},
                    {**base, "captured_at": AT + timedelta(minutes=10)}, base]
    monkeypatch.setattr("app.jobs.ingestion.get_settings", lambda: Settings(
        _env_file=None, enabled_leagues="36", enabled_seasons="2025-2026"))
    engine = create_engine("sqlite://")
    for model in (HistoricalIngestDay, ArchiveJob): model.__table__.create(engine)
    store = InMemoryObjectStore()
    with Session(engine) as session:
        result = historical_ingest(session, ProviderCaller(LicensedAdapter(), requests_per_minute=1000000), store,
                                   season="2025-2026", league="36", start_date=DAY, end_date=DAY)
        assert result["status"] == "SUCCESS"
        rows = [row for obj in store.list_objects("odds/") for row in store.read_parquet(obj.key)]
        assert len(rows) == 2
        assert {row["snapshot_type"] for row in rows} == {"OBSERVED"}
        assert all(row["source_captured_at"] == AT - timedelta(minutes=10) for row in rows)
    engine.dispose()


def test_pre_2025_seasons_never_request_odds_and_failed_statistics_are_not_empty(monkeypatch):
    class StatsFailure(Adapter):
        def get_odds(self, identity): raise AssertionError("outside requested odds scope")
        def get_statistics(self, identity): raise ProviderUnavailable("PROVIDER_UNAVAILABLE")
        def get_matches(self, day):
            return [{**row, "start_time": datetime(2023, 8, 11, 19, tzinfo=timezone.utc), "season": "2023-2024"}
                    for row in super().get_matches(day)]
    monkeypatch.setattr("app.jobs.ingestion.get_settings", lambda: Settings(
        _env_file=None, enabled_leagues="36", enabled_seasons="2023-2024"))
    engine = create_engine("sqlite://")
    for model in (HistoricalIngestDay, ArchiveJob): model.__table__.create(engine)
    with Session(engine) as session:
        result = historical_ingest(session, ProviderCaller(StatsFailure(), requests_per_minute=1000000, max_retries=1),
                                   InMemoryObjectStore(), season="2023-2024", league="36",
                                   start_date=date(2023, 8, 11), end_date=date(2023, 8, 11))
        assert result["status"] == "PARTIAL" and result["odds_written"] == 0
        progress = session.query(HistoricalIngestDay).one()
        assert progress.status == "PARTIAL"
        assert progress.coverage["statistics:a"] == "FAILED"
        assert progress.failed_match_ids == ["statistics:a", "statistics:b"]
    engine.dispose()


def test_retired_sportmonks_probe_never_contacts_provider_even_with_fixture_argument(monkeypatch, capsys):
    from app.jobs import sportmonks_probe
    def forbidden(*args, **kwargs):
        raise AssertionError("Paid provider must never be constructed")
    monkeypatch.setattr("app.providers.sportmonks.SportmonksProvider", forbidden)
    assert sportmonks_probe.main(["--fixture-id", "sportmonks-id"]) == 2
    assert "PAID_PROVIDER_DISABLED" in capsys.readouterr().out


def test_retired_sportmonks_probe_does_not_require_or_read_a_token(monkeypatch, capsys):
    from app.jobs import sportmonks_probe
    def forbidden():
        raise AssertionError("Retired probe must not read credentials")
    monkeypatch.setattr("app.core.config.get_settings", forbidden)
    assert sportmonks_probe.main([]) == 2
    assert "PAID_PROVIDER_DISABLED" in capsys.readouterr().out


def test_match_cleanup_handles_supplements_and_preserves_stored_analysis():
    from app.models import Coupon, CouponItem, MatchAnalysis, MatchResult, Prediction
    engine = create_engine("sqlite://")
    for model in (ActiveMatch, Bookmaker, ActiveOdds, ArchiveJob, MatchResult, Prediction, Coupon, CouponItem, MatchAnalysis):
        model.__table__.create(engine)
    store = InMemoryObjectStore()
    with Session(engine) as session:
        matches = [ActiveMatch(provider="p", provider_match_id=str(i), league_id="36", season="2025-2026",
                               home_team="H", away_team="A", start_time=AT, status="FINISHED", expires_at=AT)
                   for i in range(3)]
        session.add_all(matches); session.flush()
        session.add(MatchAnalysis(match_id=matches[2].id, analysis_timestamp=AT, analysis_status="AVAILABLE",
                                  final_decision="NO_BET", model_version="test", feature_version="test", analysis_output="{}"))
        session.commit()
        rows = [{"provider": "p", "provider_match_id": str(i), "league_id": "36", "season": "2025-2026",
                 "home_team": "H", "away_team": "A", "start_time": AT, "status": "FINISHED"} for i in range(3)]
        tracker = SQLArchiveTracker(session)
        archive_rows(store, tracker, dataset="matches", season="2025-2026", league="36", day=DAY, rows=rows[:1])
        archive_rows(store, tracker, dataset="matches", season="2025-2026", league="36", day=DAY, rows=rows)
        assert cleanup_verified_archives(session, store) == 2
        assert session.query(ActiveMatch).one().provider_match_id == "2"
        assert session.query(MatchAnalysis).count() == 1
        assert len(store.list_objects()) == 2
    engine.dispose()


def test_unfinished_fixture_does_not_freeze_checkpoint_or_historical_row(monkeypatch):
    class Pending(Adapter):
        fail_second = False
        final = False
        def get_matches(self, day):
            return [{**row, "status": "FINISHED" if self.final else "SCHEDULED"} for row in super().get_matches(day)]
    monkeypatch.setattr("app.jobs.ingestion.get_settings", lambda: Settings(
        _env_file=None, enabled_leagues="36", enabled_seasons="2025-2026"))
    engine = create_engine("sqlite://")
    for model in (HistoricalIngestDay, ArchiveJob): model.__table__.create(engine)
    store = InMemoryObjectStore()
    provider = Pending()
    caller = ProviderCaller(provider, requests_per_minute=1000000)
    with Session(engine) as session:
        args = dict(season="2025-2026", league="36", start_date=DAY, end_date=DAY)
        first = historical_ingest(session, caller, store, **args)
        assert first["status"] == "PARTIAL" and not store.list_objects()
        provider.final = True
        second = historical_ingest(session, caller, store, **args)
        assert second["status"] == "SUCCESS"
        assert session.query(HistoricalIngestDay).one().status == "COMPLETED"
    engine.dispose()
