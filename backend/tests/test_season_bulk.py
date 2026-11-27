from datetime import datetime, timezone
import os

import httpx
import pytest
from sqlalchemy import create_engine, func, select, update
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session

from app.analysis.season_favorites import analyze_favorites, validate_odds
from app.api.season_coverage import coverage
from app.historical.season_transfer import import_season, transfer_source
from app.models.season_bulk import SEASON_TABLES, season_fixtures, season_imports, season_odds
from app.providers.season_files import DownloadBudget, download, parse_season, source_for
from app.storage.r2 import InMemoryObjectStore

APPROVAL = "IMPORT_REVIEWED_SEASON_ONLY"
SOURCE = source_for("uefa.cl", "2024-2025")
RAW = """= Cup
# Matches 3
\u25aa League, Matchday 1
Tue Sep 17 2024
18:45 Alpha FC (ENG) v Beta FC (FRA) 2-1 (1-1)
\u25aa Finals, Round of 16
Wed Mar 5
21:00 Alpha FC (ENG) v Beta FC (FRA) 1-0 (0-0)
Wed Mar 12
21:00 Beta FC (FRA) v Alpha FC (ENG) 4-3 pen. 2-1 a.e.t. (1-0, 0-0)
""".encode()


@pytest.fixture
def db():
    url = os.getenv("SEASON_BULK_TEST_DATABASE_URL", "sqlite://")
    parsed = make_url(url)
    assert parsed.get_backend_name() == "sqlite" or (parsed.host == "localhost" and parsed.database == "season_test")
    engine = create_engine(url)
    created = []
    try:
        for table in SEASON_TABLES:
            table.create(engine)
            created.append(table)
        with Session(engine) as session:
            yield session
    finally:
        for table in reversed(created):
            table.drop(engine)
        engine.dispose()


def test_parse_normal_time_extra_time_and_second_leg():
    league, first, second = parse_season(SOURCE, RAW)
    assert league["kickoff_utc"] == "2024-09-17T16:45:00+00:00"
    assert league["stage"] == "LEAGUE_PHASE"
    assert first["tie_id"] == second["tie_id"] and (first["leg"], second["leg"]) == (1, 2)
    assert (second["half_time"], second["regulation"], second["extra_time"], second["penalties"]) == (
        [0, 0], [1, 0], [2, 1], [4, 3],
    )
    assert all(row["odds_status"] == "ODDS_UNAVAILABLE" for row in (league, first, second))


def test_request_limit_charges_attempt_and_blocks_redirect():
    budget = DownloadBudget(remaining_attempts=1)
    with httpx.Client(transport=httpx.MockTransport(lambda _: httpx.Response(302, headers={"Location": "https://example.invalid"}))) as client:
        with pytest.raises(httpx.HTTPStatusError):
            download(SOURCE, client, budget)
        with pytest.raises(ValueError, match="DOWNLOAD_QUOTA_EXCEEDED"):
            download(SOURCE, client, budget)


def test_resume_and_preserve_verified_score_and_archive(db):
    store = InMemoryObjectStore()
    calls = []
    def serve(request):
        calls.append(request.url)
        return httpx.Response(200, content=RAW)

    with httpx.Client(transport=httpx.MockTransport(serve)) as client:
        with db.begin():
            first = transfer_source(db, store, SOURCE, client, DownloadBudget(), approval=APPROVAL)
        with db.begin():
            repeat = transfer_source(db, store, SOURCE, client, DownloadBudget(0), approval=APPROVAL)
    assert repeat["reused"] and len(calls) == 1 and len(store.list_objects()) == 3
    assert first["matches"] == repeat["matches"]
    fixture = parse_season(SOURCE, RAW)[0]
    stamp = datetime(2024, 9, 18, tzinfo=timezone.utc)
    with db.begin():
        db.execute(update(season_fixtures).where(season_fixtures.c.id == fixture["id"]).values(verified_at=stamp))
    with db.begin():
        import_season(db, store, SOURCE, RAW.replace(b"2-1 (1-1)", b"3-1 (1-1)"), approval=APPROVAL)
    actual, verified = db.execute(select(season_fixtures.c.data, season_fixtures.c.verified_at).where(
        season_fixtures.c.id == fixture["id"],
    )).one()
    assert actual["regulation"] == [2, 1]
    assert verified.replace(tzinfo=timezone.utc) == stamp
    assert db.scalar(select(func.count()).select_from(season_fixtures)) == 3
    assert db.scalar(select(func.count()).select_from(season_imports)) == 2
    assert store.head_object(first["matches"]["key"]) is not None


def test_storage_failure_has_no_sql_checkpoint_and_can_resume(db, monkeypatch):
    store = InMemoryObjectStore()
    upload = store.put_object_if_absent

    def unavailable(key, *args, **kwargs):
        if "/manifests/" in key:
            raise RuntimeError("storage unavailable")
        return upload(key, *args, **kwargs)

    monkeypatch.setattr(store, "put_object_if_absent", unavailable)
    with pytest.raises(RuntimeError, match="storage unavailable"), db.begin():
        import_season(db, store, SOURCE, RAW, approval=APPROVAL)
    with db.begin():
        assert db.scalar(select(func.count()).select_from(season_fixtures)) == 0
        assert db.scalar(select(func.count()).select_from(season_imports)) == 0
    assert len(store.list_objects()) == 2

    monkeypatch.setattr(store, "put_object_if_absent", upload)
    with db.begin():
        receipt = import_season(db, store, SOURCE, RAW, approval=APPROVAL)
    assert receipt["verification"] == "ARCHIVE_VERIFIED" and len(store.list_objects()) == 3


def quote(row, at="2024-09-17T16:00:00+00:00"):
    return {"fixture_id": row["id"], "observed_at": at, "kind": "CLOSING", "market": "1X2_90",
            "provider": "fixture-test", "bookmaker": "book-test", "source_id": "snapshot", "prices": [1.7, 3.1, 5]}


def test_odds_permissions_dedup_and_coverage(db):
    store = InMemoryObjectStore()
    row = parse_season(SOURCE, RAW)[0]
    observation = quote(row)
    with pytest.raises(ValueError, match="ODDS_NOT_PRE_MATCH"):
        validate_odds(quote(row, row["kickoff_utc"]), {row["id"]: row})
    with pytest.raises(PermissionError, match="BLOCKED_LICENSE_REVIEW"):
        import_season(db, store, SOURCE, RAW, approval=APPROVAL, odds=(observation,))
    with db.begin():
        result = import_season(db, store, SOURCE, RAW, approval=APPROVAL,
                               odds=(observation, observation), odds_permission="test-only")
    assert result["odds_archive"]["row_count"] == 1
    assert db.scalar(select(func.count()).select_from(season_odds)) == 1
    league = coverage("2024-2025", db)["leagues"][0]
    assert league["total"] == 3 and league["odds"] == 1 and league["verified"] == 0
    assert league["favorites"]["status"] == "INSUFFICIENT_SAMPLE"


def test_walk_forward_uses_only_earlier_seasons():
    base = parse_season(SOURCE, RAW)[0]
    fixtures = [
        {**base, "id": str(i), "season": "2024-2025" if i < 200 else "2025-2026",
         "kickoff_utc": "2024-09-17T16:45:00+00:00" if i < 200 else "2025-09-17T16:45:00+00:00",
         "regulation": [2, 1] if i < 200 else [1, 2], "verified_at": "observed"}
        for i in range(201)
    ]
    odds = [quote(row, row["kickoff_utc"].replace("16:45", "16:00")) for row in fixtures]
    result = analyze_favorites(fixtures, odds)
    assert result["sample"] == 201
    assert result["upset"]["actual"] == pytest.approx(1 / 201)
    assert result["backtest"][0]["historical_brier"] == 1
    assert result["backtest"][0]["train_sample"] == 200


def test_season_bulk_migration_upgrade_and_destructive_downgrade_blocked():
    """Isolated migration smoke: add-only schema, never drop historical data."""
    from importlib import import_module
    from alembic.migration import MigrationContext
    from alembic.operations import Operations
    from sqlalchemy import inspect, text

    migration = import_module("migrations.versions.0011_season_bulk")
    engine = create_engine("sqlite://")
    try:
        with engine.begin() as connection:
            ctx = MigrationContext.configure(connection)
            with Operations.context(ctx):
                migration.upgrade()
                assert set(("season_teams", "season_fixtures", "season_odds", "season_imports")) <= set(
                    inspect(connection).get_table_names()
                )
                connection.execute(text(
                    "INSERT INTO season_teams (id, provider, source_name) "
                    "VALUES ('test-1', 'test-source', 'Test FC')"
                ))
                with pytest.raises(RuntimeError, match="SEASON_BULK_DOWNGRADE_REQUIRES_MANUAL_REVIEW"):
                    migration.downgrade()
                assert connection.execute(text("SELECT COUNT(*) FROM season_teams")).scalar_one() == 1
    finally:
        engine.dispose()
