"""Stage 7H.3 independent in-memory R2 recovery, no production data."""
from copy import deepcopy

import pytest

from app.api import match_center as api
from app.models import ActiveMatch, ArchiveJob, MatchResult
from app.storage import lifecycle as lc
from app.storage.r2 import InMemoryObjectStore
from r2_recovery_support import RecoveryRejected, backup, restore
from test_lifecycle import NOW, fixture, session

def prepared(session):
    match, _ = fixture(session, hours=50)
    source = InMemoryObjectStore()
    result = lc.archive_early(session,source,now=NOW)
    assert result["records_written"] == 2
    return match, source

def test_independent_copy_restores_archive_only_history_and_sql_integrity(session):
    match, source = prepared(session)
    immutable_primary = deepcopy(source._objects)
    replica = backup(session, source)
    assert replica is not source and replica._objects == immutable_primary
    # Real cleanup code operates only on disposable SQLite data, not production.
    digest = lc.plan(session,source,now=NOW)[0]["approval_digest"]
    deleted = lc.execute(session,source,approval=digest,enabled=True,now=NOW)
    assert deleted["deleted"] == 2
    assert session.query(ActiveMatch).count() == session.query(MatchResult).count() == 0
    assert session.query(ArchiveJob).count() == 2
    # Total loss of the *synthetic* original store, restored solely from replica.
    source._objects.clear()
    recovered = InMemoryObjectStore()
    receipt = restore(session,replica,target=recovered)
    assert receipt["status"] == "PASS" and receipt["objects"] == 2
    detail = api.detail_view(session,match.id,now=NOW,store=recovered)
    assert detail["storage"] == "R2" and detail["result"]["home_score"] == 1
    assert recovered._objects == immutable_primary
    assert replica._objects == immutable_primary

@pytest.mark.parametrize("fault",["missing","content","metadata","extra"])
def test_corrupt_or_incomplete_independent_copy_is_rejected_before_restore(session,fault):
    _,source = prepared(session)
    replica = backup(session,source)
    original = deepcopy(source._objects)
    key = sorted(replica._objects)[0]
    if fault == "missing":
        replica._objects.pop(key)
    elif fault == "content":
        _,kind,meta = replica._objects[key]
        replica._objects[key] = (b"corrupt",kind,meta)
    elif fault == "metadata":
        body,kind,meta = replica._objects[key]
        replica._objects[key] = (body,kind,{**meta,"row-count":"999"})
    else:
        replica.put_object("unexpected.parquet",b"bad",content_type="application/vnd.apache.parquet")
    destination = InMemoryObjectStore()
    with pytest.raises(RecoveryRejected):
        restore(session,replica,target=destination)
    assert destination._objects == {} and source._objects == original

@pytest.mark.parametrize("fault",["missing","content","metadata","ledger"])
def test_source_damage_or_unverified_ledger_blocks_backup(session,fault):
    _,source = prepared(session)
    key = sorted(source._objects)[0]
    if fault == "missing":
        source._objects.pop(key)
    elif fault == "content":
        _,kind,meta=source._objects[key]
        source._objects[key]=(b"tampered",kind,meta)
    elif fault == "metadata":
        body,kind,meta=source._objects[key]
        source._objects[key]=(body,kind,{**meta,"sha256":"bad"})
    else:
        session.query(ArchiveJob).filter_by(r2_key=key).one().status="FAILED"
        session.commit()
    with pytest.raises(RecoveryRejected):
        backup(session,source)

def test_restore_never_overwrites_existing_target(session):
    _,source=prepared(session)
    replica=backup(session,source)
    target=InMemoryObjectStore()
    target.put_object("protected",b"do not change",content_type="text/plain")
    previous=deepcopy(target._objects)
    with pytest.raises(RecoveryRejected,match="RESTORE_TARGET_NOT_EMPTY"):
        restore(session,replica,target=target)
    assert target._objects==previous

def test_sql_ledger_checksum_mismatch_rejects_replica(session):
    _,source=prepared(session)
    replica=backup(session,source)
    session.query(ArchiveJob).first().checksum="a"*64
    session.commit()
    destination=InMemoryObjectStore()
    with pytest.raises(RecoveryRejected,match="OBJECT_READBACK_NOT_VERIFIED"):
        restore(session,replica,target=destination)
    assert not destination._objects
