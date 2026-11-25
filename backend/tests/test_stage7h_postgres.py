"""Isolated PostgreSQL 16 cleanup contract tests (GitHub PR service ONLY).

Explicit TEST_POSTGRES_URL must point to loopback and db stage7h_ci.
No production DATABASE_URL, R2 network, or cleanup workflow permitted.
"""
import os
from urllib.parse import urlsplit

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

from app.api import match_center as api
from app.db.base import Base
from app.models import ActiveMatch, ArchiveJob, JobRun, MatchResult
from app.storage import lifecycle as lc
from app.storage.r2 import InMemoryObjectStore
from test_lifecycle import NOW, fixture


@pytest.fixture
def session():
    url = os.environ.get("TEST_POSTGRES_URL")
    if not url:
        pytest.skip("Only isolated PostgreSQL CI service can run these tests")
    parsed = urlsplit(url)
    if parsed.scheme not in {"postgresql", "postgresql+psycopg"} or parsed.hostname not in {"127.0.0.1", "localhost"} or parsed.path != "/stage7h_ci":
        pytest.fail("UNSAFE_TEST_DATABASE_TARGET")
    # Never try to use the application production environment.
    if os.environ.get("DATABASE_URL"):
        pytest.fail("PRODUCTION_DATABASE_ENV_MUST_BE_ABSENT")
    engine = create_engine(url, pool_pre_ping=True)
    try:
        with engine.begin() as conn:
            assert conn.execute(text("select current_database()")).scalar_one() == "stage7h_ci"
            # Docker service may report its bridge IP even when the client uses loopback.
        Base.metadata.create_all(engine)
        with Session(engine, expire_on_commit=False) as sql:
            yield sql
            sql.rollback()
    finally:
        # Only this ephemeral PR service database is allowed to be dropped.
        Base.metadata.drop_all(engine)
        engine.dispose()


def test_postgres_fk_and_verified_cleanup_to_r2_history(session):
    match, _ = fixture(session)
    store = InMemoryObjectStore()
    assert lc.archive_early(session, store, now=NOW)["records_written"] == 2
    evidence = lc.plan(session, store, now=NOW)[0]
    original = dict(store._objects)
    assert sum(x["eligible"] for x in evidence["datasets"].values()) == 2
    result = lc.execute(session, store, approval=evidence["approval_digest"], enabled=True, now=NOW)
    assert result["deleted"] == 2 and result["LIVE_DELETE"] == "EXECUTED"  # CI fixture only
    assert session.query(ActiveMatch).count() == 0
    assert session.query(MatchResult).count() == 0
    assert session.query(ArchiveJob).count() == 2 and store._objects == original
    assert session.query(JobRun).filter_by(job_type="retention_cleanup").count() == 1
    # Diagnose the actual indexed-archive failure rather than hiding its cause behind HTTP 503.
    from app.models import SystemSetting
    entry = next(x for x in session.query(SystemSetting).all() if x.key.startswith("source-identity:"))
    history = api.IndexedHistory(session, store=store)
    try:
        detail = history.match(entry, now=NOW)
    finally:
        history.close()
    assert detail["storage"] == "R2" and detail["result"]["home_score"] == 1


def test_postgres_missing_r2_prevents_delete(session):
    match, _ = fixture(session)
    store = InMemoryObjectStore()
    lc.archive_early(session, store, now=NOW)
    digest = lc.plan(session, store, now=NOW)[0]["approval_digest"]
    key = next(k for k in store._objects if k.startswith("matches/"))
    store._objects.pop(key)
    with pytest.raises((PermissionError, RuntimeError)):
        lc.execute(session, store, approval=digest, enabled=True, now=NOW)
    assert session.get(ActiveMatch, match.id)
    assert session.query(MatchResult).count() == 1


def test_postgres_sql_rollback_after_commit_failure(session, monkeypatch):
    match, _ = fixture(session)
    store = InMemoryObjectStore()
    lc.archive_early(session, store, now=NOW)
    digest = lc.plan(session, store, now=NOW)[0]["approval_digest"]
    unchanged = dict(store._objects)
    commit = session.commit
    monkeypatch.setattr(session, "commit", lambda: (_ for _ in ()).throw(RuntimeError("TEST_COMMIT_FAILED")))
    with pytest.raises(RuntimeError, match="TEST_COMMIT_FAILED"):
        lc.execute(session, store, approval=digest, enabled=True, now=NOW)
    monkeypatch.setattr(session, "commit", commit)
    assert session.get(ActiveMatch, match.id) is not None
    assert session.query(MatchResult).count() == 1
    assert session.query(JobRun).filter_by(job_type="retention_cleanup").count() == 0
    assert store._objects == unchanged


def test_postgres_unexpired_record_kept_and_gates_disabled(session):
    match, _ = fixture(session, hours=47)
    store = InMemoryObjectStore()
    lc.archive_early(session, store, now=NOW)
    report = lc.plan(session, store, now=NOW)[0]
    assert sum(x["eligible"] for x in report["datasets"].values()) == 0
    with pytest.raises(PermissionError, match="OPERATIONAL_DELETE_DISABLED"):
        lc.execute(session, store, approval=report["approval_digest"], enabled=False, now=NOW)
    assert session.get(ActiveMatch, match.id) is not None
