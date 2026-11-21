"""Offline SQL/Parquet failure tests. Fixtures are never live evidence."""
from contextlib import contextmanager
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import json

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.jobs import verified_import as job
from app.models import ActiveMatch, ArchiveJob, MatchResult, SystemSetting
from app.providers.identity import fixture_key
from app.storage.r2 import InMemoryObjectStore, parquet_rows
from test_result_verification import chain


@compiles(JSONB, "sqlite")
def _test_jsonb(element, compiler, **kw):
    return "JSON"


def evidence():
    """Synthetic fresh timestamps for offline tests, not a public source observation."""
    now = datetime.now(timezone.utc)
    rows = chain()
    urls = {"goaloo": "https://football.goaloo.com/jsData/matchResult/json/2026-2027/s8_en.json",
            "openligadb": "https://api.openligadb.de/getmatchdata/bl1/2026/10"}
    for row in rows:
        row.update(observed_at=now.isoformat(), source_url=urls[row["provider"]])
    return {"status": "RESULT_VERIFIED", "live_test": True, "observations": rows,
            "scope": {"league": "de.bl1", "date": "2026-11-20", "timezone": "Europe/Istanbul",
                      "matchday": 10, "goaloo_target_id": "3018869"},
            "requests": [{"url": url, "http_status": 200, "sha256": "a" * 64,
                          "observed_at": now.isoformat()} for url in urls.values()]}


def candidate():
    return job.candidate_from_live(evidence(), datetime.now(timezone.utc))


@pytest.fixture
def session():
    engine = create_engine("sqlite://")
    for model in (ActiveMatch, MatchResult, SystemSetting, ArchiveJob):
        model.__table__.create(engine)
    with Session(engine, expire_on_commit=False) as sql:
        yield sql
    engine.dispose()


def persist(session, store, item=None):
    return job.persist_verified(session, store, item or candidate(), now=datetime.now(timezone.utc))


def test_dry_run_does_not_connect_or_create_storage_even_when_configured():
    def forbidden(*a, **kw):
        pytest.fail("dry-run must never connect to storage")
    result = job.run(probe=lambda *a: evidence(), settings=Settings(_env_file=None),
                     session_context=forbidden, store_factory=forbidden)
    assert result["status"] == "DRY_RUN_PASS"
    assert result["postgres"]["status"] == result["r2"]["status"] == "NOT_RUN"
    assert "DATABASE_URL" in result["missing_env"]


@pytest.mark.parametrize("change", ["saved", "stale", "future", "score", "unfinished", "scope", "hash", "url"])
def test_bad_live_evidence_cannot_start_writes(change):
    report = evidence()
    if change == "saved": report["live_test"] = False
    if change in {"stale", "future"}:
        delta = timedelta(hours=-1 if change == "stale" else 1)
        report["observations"][0]["observed_at"] = (datetime.now(timezone.utc) + delta).isoformat()
    if change == "score": report["observations"][0]["reported_score"] = [2, 1]
    if change == "unfinished": report["observations"][0]["final_confirmed"] = False
    if change == "scope": report["scope"]["date"] = "2026-11-21"
    if change == "hash": report["requests"][0]["sha256"] = "bad"
    if change == "url": report["observations"][0]["source_url"] = "https://unreviewed.invalid"
    def forbidden(*a, **kw): pytest.fail("rejected evidence must block storage")
    result = job.run(persist=True, probe=lambda *a: report, settings=Settings(_env_file=None),
                     session_context=forbidden, store_factory=forbidden)
    assert result["status"] == "BLOCKED"
    assert result["postgres"]["status"] == result["r2"]["status"] == "NOT_RUN"


def test_persist_with_missing_credentials_is_not_run_and_does_not_leak_values():
    cfg = Settings(_env_file=None, r2_bucket="secret-bucket-marker")
    result = job.run(persist=True, probe=lambda *a: evidence(), settings=cfg)
    assert result["error_code"] == "STORAGE_NOT_CONFIGURED"
    assert result["postgres"]["status"] == result["r2"]["status"] == "NOT_RUN"
    assert "secret-bucket-marker" not in json.dumps(result)


def test_twice_is_one_match_one_result_and_same_immutable_objects(session):
    store = InMemoryObjectStore()
    first = persist(session, store)
    original = dict(store._objects)
    first_stamp = session.query(MatchResult).one().verified_at
    second = persist(session, store)
    assert first["status"] == second["status"] == "VERIFIED"
    assert first["postgres"]["created"] and not second["postgres"]["created"]
    assert session.query(ActiveMatch).count() == session.query(MatchResult).count() == 1
    assert session.query(MatchResult).one().verified_at == first_stamp
    assert store._objects == original and len(original) == 2
    assert all(j.status == "VERIFIED" for j in session.query(ArchiveJob))
    for obj in store.list_objects():
        assert "/season=2026-2027/league=de.bl1/date=2026-11-20/part=" in obj.key
        rows = parquet_rows(store.get_object(obj.key))
        assert len(rows) == 1 and rows[0]["licence"] == "ODbL-1.0"
        assert rows[0]["attribution"] == job.ATTRIBUTION
        assert {r["source_match_id"] for r in json.loads(rows[0]["source_provenance_json"])} == {"83237", "3018869"}
        assert "odds" not in rows[0] and "statistics" not in rows[0]


def native_match(provider="goaloo", identity="3018869"):
    return ActiveMatch(provider=provider, provider_match_id=identity, league_id="8", season="2026-2027",
                       home_team="Bayern Munchen", away_team="FC Koln", status="SCHEDULED",
                       start_time=datetime(2026, 11, 20, 19, 30, tzinfo=timezone.utc),
                       expires_at=datetime(2026, 11, 22, tzinfo=timezone.utc))


def test_existing_goaloo_match_is_linked_not_rekeyed_or_duplicated(session):
    match = native_match()
    session.add(match)
    session.commit()
    identity = match.id
    persist(session, InMemoryObjectStore())
    assert session.query(ActiveMatch).count() == 1
    assert match.id == identity and match.provider == "goaloo" and match.provider_match_id == "3018869"
    assert match.home_team == "Bayern Munchen" and match.league_id == "8"
    result = session.query(MatchResult).one()
    assert result.provider == "openligadb" and result.provider_result_id == "83237"
    assert result.match_id == identity and result.finished_at is None
    assert "ODbL-1.0" in result.result_source


@pytest.mark.parametrize("conflict", ["kickoff", "teams", "league", "duplicate", "prior_result"])
def test_existing_conflicts_never_merge_or_overwrite(session, conflict):
    match = native_match()
    if conflict == "kickoff": match.start_time += timedelta(minutes=1)
    if conflict == "teams": match.away_team = "Wrong Club"
    if conflict == "league": match.league_id = "36"
    session.add(match)
    session.flush()
    if conflict == "duplicate":
        other = native_match("openligadb", "83237")
        other.home_team, other.away_team, other.league_id = "FC Bayern München", "1. FC Köln", "de.bl1"
        session.add(other)
    if conflict == "prior_result":
        session.add(MatchResult(match_id=match.id, provider="goaloo", home_score=2, away_score=0,
                                status="FINAL", result_source="prior", verified_at=datetime.now(timezone.utc)))
    session.commit()
    store = InMemoryObjectStore()
    with pytest.raises(job.ImportBlocked): persist(session, store)
    session.rollback()
    assert not store.list_objects() and session.get(SystemSetting, job.KEY) is None
    assert match.status == "SCHEDULED"


def test_late_goaloo_daily_ingestion_respects_the_reviewed_identity(session):
    from app.jobs.ingestion import upsert_matches
    persist(session, InMemoryObjectStore())
    incoming = native_match()
    raw = {k: getattr(incoming, k) for k in ("provider", "provider_match_id", "league_id", "season",
            "home_team", "away_team", "status", "start_time", "expires_at")}
    written, skipped = upsert_matches(session, [raw], now=raw["start_time"] + timedelta(hours=5))
    assert (written, skipped) == (0, 1) and session.query(ActiveMatch).count() == 1
    wrong = {**raw, "provider_match_id": "other"}
    assert fixture_key(wrong) != fixture_key(raw)  # no global team alias


class FailSecondStore(InMemoryObjectStore):
    fail = True
    def put_object_if_absent(self, key, body, **kw):
        if key.startswith("results/") and self.fail:
            self.fail = False
            # Simulate an upload that succeeded but whose acknowledgement was lost.
            super().put_object_if_absent(key, body, **kw)
            raise TimeoutError("SECRET_DO_NOT_REPORT")
        return super().put_object_if_absent(key, body, **kw)


def test_partial_r2_and_ambiguous_upload_retries_without_deleting_anything(session):
    store = FailSecondStore()
    with pytest.raises(job.ImportIncomplete) as failure:
        persist(session, store)
    assert failure.value.progress["postgres"]["status"] == "PASS"
    assert "SECRET_DO_NOT_REPORT" not in json.dumps(failure.value.progress)
    assert session.query(ActiveMatch).count() == session.query(MatchResult).count() == 1
    assert session.get(SystemSetting, job.KEY).value["phase"] == "SQL_COMMITTED"
    original = dict(store._objects)
    assert len(original) == 2
    assert persist(session, store)["status"] == "VERIFIED"
    assert original == store._objects
    assert session.query(ActiveMatch).count() == session.query(MatchResult).count() == 1


def test_conflicting_or_corrupt_existing_object_is_never_overwritten(session):
    store = InMemoryObjectStore()
    first = persist(session, store)
    key = first["r2"]["receipts"]["matches"]["key"]
    store.put_object(key, b"unrelated existing archive", content_type="application/octet-stream")
    before = dict(store._objects)
    with pytest.raises(job.ImportIncomplete): persist(session, store)
    assert before == store._objects
    assert session.query(ActiveMatch).count() == session.query(MatchResult).count() == 1


def test_sql_commit_failure_never_starts_r2(session, monkeypatch):
    store = InMemoryObjectStore()
    original = session.commit
    def failed(): raise RuntimeError("secret SQL connection detail")
    monkeypatch.setattr(session, "commit", failed)
    with pytest.raises(RuntimeError): persist(session, store)
    session.rollback()
    monkeypatch.setattr(session, "commit", original)
    assert not store.list_objects()
    assert session.query(ActiveMatch).count() == session.query(MatchResult).count() == 0
    assert persist(session, store)["status"] == "VERIFIED"


def test_final_journal_commit_failure_reuses_verified_archives(session, monkeypatch):
    original = session.commit
    def fail_final():
        item = session.get(SystemSetting, job.KEY)
        if item and item.value["phase"] == "VERIFIED":
            raise RuntimeError("lost final SQL commit")
        original()
    monkeypatch.setattr(session, "commit", fail_final)
    store = InMemoryObjectStore()
    with pytest.raises(job.ImportIncomplete): persist(session, store)
    original_objects = dict(store._objects)
    monkeypatch.setattr(session, "commit", original)
    assert persist(session, store)["status"] == "VERIFIED"
    assert store._objects == original_objects


def test_scope_cannot_be_widened_from_command_line():
    with pytest.raises(SystemExit): job.main(["--date", "2026-11-21"])
    with pytest.raises(SystemExit): job.main(["--dry-run", "--persist"])


def test_archives_work_with_existing_historical_reader_and_availability_cutoff(session):
    from app.historical.repository import HistoricalRepository
    store = InMemoryObjectStore()
    persist(session, store)
    repository = HistoricalRepository(store)
    event = datetime(2026, 11, 20, tzinfo=timezone.utc)
    args = {"league": "de.bl1", "season": "2026-2027", "start": event, "end": event + timedelta(hours=23)}
    assert repository.get_similarity_history(**args, before=event) == []
    rows = repository.get_similarity_history(**args, before=datetime.now(timezone.utc) + timedelta(seconds=1))
    assert len(rows) == 1 and rows[0]["home_score"] == rows[0]["away_score"] == 1
    assert rows[0]["technical_statistics"] == [] and "historical_odds" not in rows[0]


def test_existing_daily_archiver_does_not_duplicate_controlled_parts_or_drop_attribution(session):
    from app.jobs.ingestion import archive_expired_active_state
    from app.models import ActiveOdds, Prediction
    for model in (ActiveOdds, Prediction): model.__table__.create(session.get_bind())
    store = InMemoryObjectStore()
    persist(session, store)
    original = dict(store._objects)
    now = datetime.now(timezone.utc) + timedelta(days=20)
    counts = archive_expired_active_state(session, store, now=now)
    assert counts == {"matches": 0, "results": 0, "odds": 0, "predictions": 0}
    assert original == store._objects


def test_changed_retry_snapshot_is_rejected_against_sql_and_fresh_event(session):
    store = InMemoryObjectStore()
    persist(session, store)
    journal = session.get(SystemSetting, job.KEY)
    changed = deepcopy(journal.value)
    changed["archive_rows"]["results"]["home_score"] = 99
    journal.value = changed
    session.commit()
    original = dict(store._objects)
    with pytest.raises(job.ImportBlocked, match="JOURNALED_ARCHIVE_SNAPSHOT_CHANGED"):
        persist(session, store)
    assert store._objects == original and session.query(MatchResult).one().home_score == 1


def test_evidence_expiring_during_preflight_is_blocked_before_sql(session):
    item = candidate()
    with pytest.raises(job.ImportBlocked, match="LIVE_EVIDENCE_EXPIRED_BEFORE_SQL"):
        job.persist_verified(session, InMemoryObjectStore(), item,
                             now=datetime.now(timezone.utc) + timedelta(minutes=11))
    assert session.query(ActiveMatch).count() == 0


def test_ambiguous_initial_sql_commit_is_reconciled_without_second_match(session, monkeypatch):
    commit = session.commit
    def acknowledged_late():
        commit()
        raise TimeoutError("acknowledgement lost")
    monkeypatch.setattr(session, "commit", acknowledged_late)
    store = InMemoryObjectStore()
    with pytest.raises(TimeoutError): persist(session, store)
    assert session.query(ActiveMatch).count() == session.query(MatchResult).count() == 1
    assert store.list_objects() == []
    monkeypatch.setattr(session, "commit", commit)
    assert persist(session, store)["status"] == "VERIFIED"
    assert session.query(ActiveMatch).count() == session.query(MatchResult).count() == 1


@pytest.mark.parametrize("busy_on", [None, 1, 2, 3])
@pytest.mark.parametrize("event_identity", [None, "reviewed-fixture-lock-scope"])
def test_production_lock_releases_on_success_or_busy_without_waiting(monkeypatch, busy_on, event_identity):
    from app.db import session as db
    class Scalar:
        def __init__(self, value): self.value = value
        def scalar_one(self): return self.value
    class Connection:
        def __init__(self): self.locks, self.unlocks = [], []
        def execute(self, statement, args):
            if "pg_try_advisory_lock" in str(statement):
                self.locks.append(args["key"])
                return Scalar(len(self.locks) != busy_on)
            self.unlocks.append(args["key"])
            return Scalar(True)
        def commit(self): pass
        def rollback(self): pass
    connection = Connection()
    class Engine:
        @contextmanager
        def connect(self): yield connection
    monkeypatch.setattr(db, "engine", Engine())
    if busy_on:
        with pytest.raises(job.ImportBlocked, match="IMPORT_ALREADY_RUNNING"):
            with job.production_session(Settings(_env_file=None), event_identity=event_identity):
                pytest.fail("busy lock must reject before opening a SQL session")
        assert connection.unlocks == list(reversed(connection.locks[:-1]))
    else:
        with job.production_session(Settings(_env_file=None), event_identity=event_identity): pass
        assert len(connection.locks) == 3
        from app.storage.lifecycle import LOCK_ID
        from app.providers.identity import lock_id
        assert connection.locks[:2] == [LOCK_ID, job.LOCK_ID]
        if event_identity:
            assert connection.locks[2] == lock_id(event_identity)
        assert connection.unlocks == list(reversed(connection.locks))
