"""Exercise the actual archived-detail reader with an isolated SQL ledger and R2 adapter."""
from datetime import datetime, timezone
from uuid import uuid4
import hashlib
import json

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.historical.repository import HistoricalRepository
from app.models import ArchiveJob
from app.storage.r2 import InMemoryObjectStore, parquet_bytes


@pytest.fixture
def archive_environment():
    engine = create_engine("sqlite://")
    ArchiveJob.__table__.create(engine)
    store = InMemoryObjectStore()
    with Session(engine) as session:
        yield session, store
    engine.dispose()


def _archive(session, store, *, match_id, result_verified=True):
    now = datetime(2026, 11, 25, 12, tzinfo=timezone.utc)
    day = now.date()
    match_key = f"matches/season=2026-2027/league=de.bl1/date={day}/part=lifecycle-{hashlib.sha256(f'active_matches:{match_id}'.encode()).hexdigest()[:20]}-fixture.parquet"
    result_key = f"results/season=2026-2027/league=de.bl1/date={day}/part=lifecycle-result-fixture.parquet"
    match = {"id": str(match_id), "start_time": now.isoformat(),
             "league_id": "de.bl1", "home_team": "HOME", "away_team": "AWAY"}
    result = {"match_id": str(match_id), "status": "FINAL", "home_score": 2, "away_score": 1,
              "verified_at": now.isoformat() if result_verified else None}
    for key, dataset, identity, snapshot in (
        (match_key, "matches", str(match_id), match),
        (result_key, "results", str(uuid4()), result),
    ):
        row = {"archive_format": "lifecycle-v1",
               "sql_table": "active_matches" if dataset == "matches" else "match_results",
               "sql_identity": identity,
               "sql_snapshot_json": json.dumps(snapshot, sort_keys=True)}
        payload = parquet_bytes([row])
        checksum = hashlib.sha256(payload).hexdigest()
        store.put_object_if_absent(key, payload, content_type="application/vnd.apache.parquet",
            metadata={"sha256": checksum, "row-count": "1", "archive-key": key})
        session.add(ArchiveJob(archive_date=day, dataset_type=dataset, league="de.bl1",
            r2_key=key, row_count=1, file_size_bytes=len(payload), checksum=checksum,
            status="VERIFIED", verified_at=now))
    session.commit()
    return match_key, result_key


def test_verified_match_detail_is_recovered_from_real_parquet(archive_environment):
    session, store = archive_environment
    match_id = uuid4()
    _archive(session, store, match_id=match_id)
    found = HistoricalRepository(store).get_verified_match_from_archive(session, match_id)
    assert found["match"]["id"] == str(match_id)
    assert found["result"]["home_score"] == 2
    assert found["result"]["verified_at"]


def test_unverified_score_is_never_exposed(archive_environment):
    session, store = archive_environment
    match_id = uuid4()
    _archive(session, store, match_id=match_id, result_verified=False)
    with pytest.raises(IOError, match="HISTORICAL_RESULT_NOT_VERIFIED"):
        HistoricalRepository(store).get_verified_match_from_archive(session, match_id)


def test_corrupt_r2_fixture_is_rejected(archive_environment):
    session, store = archive_environment
    match_id = uuid4()
    fixture_key, _ = _archive(session, store, match_id=match_id)
    store.put_object(fixture_key, b"corrupted", content_type="application/vnd.apache.parquet")
    with pytest.raises(IOError):
        HistoricalRepository(store).get_verified_match_from_archive(session, match_id)


def test_archive_without_result_is_not_served(archive_environment):
    session, store = archive_environment
    match_id = uuid4()
    _archive(session, store, match_id=match_id)
    for job in session.query(ArchiveJob).filter_by(dataset_type="results"):
        job.status = "FAILED"
    session.commit()
    assert HistoricalRepository(store).get_verified_match_from_archive(session, match_id) is None
