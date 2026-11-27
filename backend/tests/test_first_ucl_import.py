from dataclasses import replace
from unittest.mock import MagicMock

import pytest

from app.jobs import first_ucl_import as operator
from test_season_bulk import db


def test_wrong_database_and_missing_approval_fail_before_io(monkeypatch):
    monkeypatch.setattr(operator, "create_engine", lambda *a, **k: pytest.fail("database accessed"))
    with pytest.raises(RuntimeError, match="OPERATOR_APPROVAL_REQUIRED"):
        operator.run(live=True)
    monkeypatch.setenv("SEASON_BULK_OPERATOR_DATABASE_URL", "postgresql://other:secret@localhost/cbcxbcxbcv")
    with pytest.raises(RuntimeError, match="TARGET_DATABASE_MISMATCH"):
        operator.run()
    assert operator.main(["--live"]) == 1


def test_database_identity_and_migration_fail_closed():
    valid = "postgresql://cbcxbcxbcv_user:x@dpg-dbtlovifngtc739kaf1g-a.frankfurt-postgres.render.com/cbcxbcxbcv?sslmode=require"
    operator.validate_database_url(valid)
    for url in (valid.replace("cbcxbcxbcv?", "other?"), valid.replace("dpg-dbtlovifngtc739kaf1g-a.", "attacker."),
                valid.replace("sslmode=require", "sslmode=disable")):
        with pytest.raises(RuntimeError, match="TARGET_DATABASE_MISMATCH"):
            operator.validate_database_url(url)
    connection = MagicMock()
    connection.scalar.return_value = operator.DATABASE
    connection.execute.return_value.scalars.return_value.all.return_value = ["0010_active_statistics"]
    with pytest.raises(RuntimeError, match="MIGRATION_REQUIRED"):
        operator.check_database(connection)
    connection.execute.return_value.scalars.return_value.all.return_value = ["0009_historical_coverage"]
    with pytest.raises(RuntimeError, match="MIGRATION_VERSION_MISMATCH"):
        operator.check_database(connection)
    connection.scalar.return_value = "wrong"
    with pytest.raises(RuntimeError, match="ACTUAL_DATABASE_MISMATCH"):
        operator.check_database(connection)


def test_scope_hash_and_count_fail_closed(monkeypatch):
    source = operator.source_for("uefa.cl", "2024-2025")
    with pytest.raises(RuntimeError, match="SOURCE_SCOPE_MISMATCH"):
        operator.validate_source(replace(source, league="uefa.el"), b"test")
    with pytest.raises(RuntimeError, match="SOURCE_SHA256_MISMATCH"):
        operator.validate_source(source, b"test")
    monkeypatch.setattr(operator, "SHA256", operator.hashlib.sha256(b"test").hexdigest())
    monkeypatch.setattr(operator, "parse_season", lambda *args: [{"regulation": [1, 0]}])
    with pytest.raises(RuntimeError, match="SOURCE_MATCH_COUNT_MISMATCH"):
        operator.validate_source(source, b"test")


def test_archive_replay_and_corruption(db):
    from sqlalchemy import func, select
    from app.historical.season_transfer import import_season
    from app.models.season_bulk import season_imports
    from app.storage.r2 import InMemoryObjectStore
    from test_season_bulk import SOURCE, RAW, APPROVAL

    store = InMemoryObjectStore()
    with db.begin():
        first = import_season(db, store, SOURCE, RAW, approval=APPROVAL)
    with db.begin():
        second = import_season(db, store, SOURCE, RAW, approval=APPROVAL)
    assert second["reused"]
    assert len(store.list_objects()) == 3
    with db.begin():
        assert db.scalar(select(func.count()).select_from(season_imports)) == 1
    store.put_object(first["matches"]["key"], b"corrupt", content_type="application/octet-stream")
    with pytest.raises(RuntimeError, match="ARCHIVE_RECEIPT_MISMATCH"), db.begin():
        import_season(db, store, SOURCE, RAW, approval=APPROVAL)
