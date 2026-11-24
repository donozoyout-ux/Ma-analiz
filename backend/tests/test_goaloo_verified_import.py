"""Real SQLite transaction + in-memory R2 archive tests, with no live secrets."""
from datetime import datetime, timedelta, timezone
import os
from decimal import Decimal
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, select, func
from sqlalchemy.orm import Session

from app.db.base import Base
from app.models import ActiveMatch, ActiveOdds
from app.jobs.goaloo_verified_import import import_verified_odds, ImportSafetyError
from app.providers.goaloo_match_identity import MappingDecision
from app.storage.r2 import InMemoryObjectStore


@pytest.fixture
def session():
    engine = create_engine(os.environ.get("TEST_POSTGRES_URL", "sqlite+pysqlite:///:memory:"))
    Base.metadata.create_all(engine)
    with Session(engine) as s:
        start = datetime(2026, 8, 28, 18, 30, tzinfo=timezone.utc)
        s.add(ActiveMatch(id=uuid4(), provider="openligadb",
                          provider_match_id="83156", league_id="de.bl1",
                          league_name="Bundesliga", season="2026-2027",
                          home_team="FC Bayern München", away_team="VfB Stuttgart",
                          start_time=start, status="FINISHED",
                          expires_at=start+timedelta(days=200)))
        s.commit()
        yield s
    Base.metadata.drop_all(engine)
    engine.dispose()


def mapped(goaloo_id="3018786", open_id="83156", status="MATCHED"):
    return MappingDecision(goaloo_id, open_id if status == "MATCHED" else None,
                           status, "EXACT_TEAMS_AND_TIME",
                           {"league_id": "de.bl1", "season": "2026-2027"})


def quote(**updates):
    q = {
        "provider": "goaloo", "source": "goaloo",
        "provider_odds_id": "8:euro:f:u", "provider_bookmaker_id": "8",
        "bookmaker": "Bet365", "market": "1X2", "selection": "HOME",
        "odds": Decimal("1.22"), "captured_at": datetime(2026, 11, 24, 17, tzinfo=timezone.utc),
        "snapshot_type": "OPENING", "period": "PREMATCH",
        "raw_value": '{"u":"1.22","g":"7","d":"10"}',
    }
    q.update(updates)
    return q


def test_dryrun_performs_no_sql_or_r2_writes(session):
    store = InMemoryObjectStore()
    report = import_verified_odds(session, [mapped()], {"3018786": [quote()]}, store=store)
    assert report["status"] == "DRY_RUN"
    assert report["counts"]["eligible_quotes"] == 1
    assert session.scalar(select(func.count(ActiveOdds.id))) == 0
    assert store.list_objects() == []


def test_live_requires_both_operator_gate_and_object_store(session):
    with pytest.raises(ImportSafetyError, match="APPROVAL"):
        import_verified_odds(session, [mapped()], {"3018786": [quote()]}, dry_run=False)
    with pytest.raises(ImportSafetyError, match="APPROVAL"):
        import_verified_odds(session, [mapped()], {"3018786": [quote()]},
                             dry_run=False, store=InMemoryObjectStore())


def test_verified_sql_write_and_r2_immutable_and_idempotent(session):
    store = InMemoryObjectStore()
    kwargs = {"session": session, "decisions": [mapped()],
              "odds_by_goaloo": {"3018786": [quote()]},
              "store": store, "dry_run": False, "live_approved": True}
    report = import_verified_odds(**kwargs)
    assert report["sql_inserted"] == 1
    assert report["archive_receipt"]["row_count"] == 1
    assert len(store.list_objects("odds/")) == 1
    assert session.scalar(select(func.count(ActiveOdds.id))) == 1
    second = import_verified_odds(**kwargs)
    assert second["sql_inserted"] == 0
    assert second["counts"]["already_in_sql"] == 1
    assert second["archive_receipt"]["key"] == report["archive_receipt"]["key"]
    assert len(store.list_objects("odds/")) == 1
    assert len(store.read_parquet(report["archive_receipt"]["key"])) == 1


def test_unknown_or_review_sources_never_write(session):
    store = InMemoryObjectStore()
    result = import_verified_odds(session, [mapped(status="NEEDS_REVIEW")],
                    {"3018786": [quote()]}, store=store, dry_run=False, live_approved=True)
    assert result["counts"]["quarantined_quotes"] == 1
    assert session.scalar(select(func.count(ActiveOdds.id))) == 0
    assert not store.list_objects()


def test_absent_sql_match_blocks_odds_even_with_verified_identity(session):
    report = import_verified_odds(session, [mapped(open_id="missing")],
                        {"3018786": [quote()]})
    assert report["counts"]["missing_sql_match"] == 1
    assert report["counts"]["quarantined_quotes"] == 1


def test_ah_requires_line_and_keeps_home_away(session):
    sample = quote(market="AH", odds=Decimal("1.85"), line=Decimal("-2"),
                   raw_line="2", selection="HOME")
    store = InMemoryObjectStore()
    report = import_verified_odds(session, [mapped()], {"3018786": [sample]},
                   store=store, dry_run=False, live_approved=True)
    assert report["sql_inserted"] == 1
    odds = session.scalar(select(ActiveOdds))
    assert odds.line_key == "-2.000" and odds.selection == "HOME"


def test_stale_static_quote_without_timestamp_is_not_inserted(session):
    result = import_verified_odds(session, [mapped()], {"3018786": [quote(captured_at=None)]})
    assert result["counts"]["invalid_quotes"] == 1
    assert result["counts"].get("eligible_quotes", 0) == 0


def test_unknown_goaloo_id_fails_closed(session):
    with pytest.raises(ImportSafetyError, match="UNMAPPED_SOURCE_ID"):
        import_verified_odds(session, [mapped()], {"999999": [quote()]})


def test_two_goaloo_ids_to_same_target_fail_closed(session):
    with pytest.raises(ImportSafetyError, match="AMBIGUOUS"):
        import_verified_odds(session, [mapped(), mapped(goaloo_id="other")],
                             {"3018786": [quote()]})


def test_r2_write_failure_does_not_write_sql(session):
    class BrokenStore(InMemoryObjectStore):
        def put_object_if_absent(self, *args, **kwargs):
            raise IOError("simulated R2 outage")
    with pytest.raises(RuntimeError, match="Archive failed"):
        import_verified_odds(session, [mapped()], {"3018786": [quote()]},
                             store=BrokenStore(), dry_run=False, live_approved=True)
    assert session.scalar(select(func.count(ActiveOdds.id))) == 0
