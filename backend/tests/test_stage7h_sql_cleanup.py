"""Stage 7H prelaunch SQL cleanup acceptance tests.

Only temporary SQLite and in-memory object store; no production credentials,
external SQL, R2 requests or workflow dispatch.
"""
from datetime import timedelta

import pytest

from app.api import match_center as api
from app.models import ActiveMatch, ArchiveJob, JobRun, MatchResult, SystemSetting
from app.storage import lifecycle as lc
from app.storage.r2 import InMemoryObjectStore
from test_lifecycle import NOW, archive, fixture, plan, session


def counts(sql):
    return (sql.query(ActiveMatch).count(), sql.query(MatchResult).count(),
            sql.query(ArchiveJob).count())


def test_prelaunch_cleanup_preserves_r2_history_and_protected_sql(session):
    match, result = fixture(session, hours=50)
    store = InMemoryObjectStore()
    archive(session, store)
    old_r2 = dict(store._objects)
    before = api.detail_view(session, match.id, source="history", now=NOW, store=store)
    assert before["result"]["home_score"] == 1
    assert counts(session) == (1, 1, 2)
    digest = plan(session, store)["approval_digest"]
    report = lc.execute(session, store, approval=digest, enabled=True, now=NOW)
    assert report["status"] == "COMPLETED" and report["deleted"] == 2
    assert counts(session) == (0, 0, 2)
    assert store._objects == old_r2  # cleanup may not delete/overwrite R2
    identity = session.get(SystemSetting, lc.source_index_key(match))
    assert identity and identity.value["match_id"] == str(match.id)
    after = api.detail_view(session, match.id, now=NOW, store=store)
    assert after["storage"] == "R2" and after["result"] == before["result"]
    assert api.list_view(session, source="history", now=NOW, store=store)["items"]
    audits = session.query(JobRun).filter_by(job_type="retention_cleanup").all()
    assert len(audits) == 1 and audits[0].records_written == 2


@pytest.mark.parametrize("age", [0, 47, 47.999])
def test_prelaunch_cleanup_rejects_unexpired_bundle(session, age):
    fixture(session, hours=age)
    store = InMemoryObjectStore()
    archive(session, store)
    assert all(not row["eligible"] for row in plan(session, store)["records"])
    original = counts(session), dict(store._objects)
    report = lc.automatic(session, store, enabled=False, now=NOW)
    assert report["auto_delete_enabled"] is False
    assert report["deleted"] == 0 and report["LIVE_DELETE"] == "NOT_RUN"
    assert (counts(session), store._objects) == original


@pytest.mark.parametrize("damage", ["absent", "checksum", "metadata", "ledger", "snapshot"])
def test_prelaunch_cleanup_fails_closed_with_corrupted_archive(session, damage):
    match, result = fixture(session, hours=50)
    store = InMemoryObjectStore()
    archive(session, store)
    reviewed = plan(session, store)["approval_digest"]
    key = next(k for k in store._objects if k.startswith("matches/"))
    if damage == "absent":
        store._objects.pop(key)
    elif damage == "checksum":
        body, typ, meta = store._objects[key]
        store._objects[key] = (b"tampered", typ, meta)
    elif damage == "metadata":
        body, typ, meta = store._objects[key]
        store._objects[key] = (body, typ, {**meta, "row-count": "123"})
    elif damage == "ledger":
        session.query(ArchiveJob).filter_by(r2_key=key).one().status = "FAILED"
        session.commit()
    elif damage == "snapshot":
        result.home_score = 4
        session.commit()
    with pytest.raises((PermissionError, RuntimeError)):
        lc.execute(session, store, approval=reviewed, enabled=True, now=NOW)
    assert counts(session)[:2] == (1, 1)
    assert session.query(JobRun).filter_by(job_type="retention_cleanup").count() == 0


def test_prelaunch_cleanup_rejects_changed_approval_digest(session):
    fixture(session, hours=50)
    store = InMemoryObjectStore()
    archive(session, store)
    with pytest.raises(PermissionError, match="APPROVAL_PLAN_CHANGED"):
        lc.execute(session, store, approval="f" * 64, enabled=True, now=NOW)
    assert counts(session)[:2] == (1, 1)


def test_prelaunch_cleanup_rolls_back_sql_when_commit_fails(session, monkeypatch):
    match, _ = fixture(session, hours=50)
    store = InMemoryObjectStore()
    archive(session, store)
    proof = plan(session, store)["approval_digest"]
    old_r2 = dict(store._objects)
    original_commit = session.commit
    def reject():
        raise RuntimeError("OFFLINE_COMMIT_FAILURE")
    monkeypatch.setattr(session, "commit", reject)
    with pytest.raises(RuntimeError, match="OFFLINE_COMMIT_FAILURE"):
        lc.execute(session, store, approval=proof, enabled=True, now=NOW)
    monkeypatch.setattr(session, "commit", original_commit)
    assert counts(session)[:2] == (1, 1)
    assert store._objects == old_r2
    assert session.query(JobRun).filter_by(job_type="retention_cleanup").count() == 0


def test_prelaunch_cleanup_disabled_gate_never_changes_sql_or_r2(session):
    fixture(session, hours=50)
    store = InMemoryObjectStore()
    archive(session, store)
    proof = plan(session, store)["approval_digest"]
    original = counts(session), dict(store._objects)
    with pytest.raises(PermissionError, match="OPERATIONAL_DELETE_DISABLED"):
        lc.execute(session, store, approval=proof, enabled=False, now=NOW)
    assert (counts(session), store._objects) == original
