"""Synthetic, zero-network tests for La Liga + Premier League R2 season archiving."""
from datetime import date
import json
from types import SimpleNamespace

import pytest

from app.jobs import es_en_season_r2 as bulk
from app.storage.r2 import InMemoryObjectStore


def fake_file(source):
    year = int(source.season[:4])
    # Fixtures are synthetic; round and team identity stay unambiguous.
    return json.dumps({"matches": [
        {"round": f"Matchday {i + 1}", "date": f"{year}-08-21",
         "team1": f"Home FC {i}", "team2": f"Away FC {i}",
         "score": {"ft": [1, 0]} if i % 2 == 0 else {}}
        for i in range(380)
    ]}).encode()


class Store(InMemoryObjectStore):
    bucket = bulk.BUCKET
    client = SimpleNamespace(close=lambda: None)


def test_two_leagues_three_seasons_source_and_dryrun(monkeypatch):
    counts = []
    def fetch(source):
        counts.append((source.league, source.season, source.url))
        return fake_file(source)
    result = bulk.execute(fetcher=fetch, store_factory=lambda: pytest.fail("R2 accessed"))
    assert result["status"] == "DRY_RUN_VALIDATED"
    assert result["objects_planned"] == 18
    assert len(counts) == 6
    assert set(x[0] for x in counts) == {"es.1", "en.pl"}
    assert all(bulk.REVISION in x[2] for x in counts)
    assert all(s["matches"] == 380 and s["scores"] == 190 for s in result["seasons"])
    assert result["odds_api_credits"] == 0 and result["postgresql"] == "NOT_USED"


def test_future_fixtures_excluded_without_inventing_scores():
    rows = json.loads(fake_file(bulk.source_for("es.1", "2026-2027")))
    rows["matches"][0]["date"] = "2026-12-01"
    report, artifacts = bulk.prepare("es.1", "2026-2027", json.dumps(rows).encode())
    assert report["matches"] == 379
    assert report["scores"] == 189
    assert report["unknown_scores"] == 190
    assert len(artifacts) == 3


def test_live_requires_exact_approval_before_fetch():
    with pytest.raises(ValueError, match="LIVE_APPROVAL_REQUIRED"):
        bulk.execute(live=True, fetcher=lambda _: pytest.fail("fetched"),
                     store_factory=lambda: pytest.fail("opened"))
    with pytest.raises(ValueError, match="DRY_RUN_APPROVAL_CONFLICT"):
        bulk.execute(approval=bulk.APPROVAL, fetcher=lambda _: pytest.fail("fetched"))


def test_live_immutable_replay_and_no_bundesliga(monkeypatch):
    monkeypatch.setattr(bulk, "get_settings", lambda: SimpleNamespace(r2_bucket=bulk.BUCKET))
    store = Store()
    first = bulk.execute(live=True, approval=bulk.APPROVAL,
                         fetcher=fake_file, store_factory=lambda: store)
    assert first["status"] == "R2_ARCHIVE_VERIFIED"
    assert first["objects_verified"] == 18
    assert len(store.list_objects()) == 18
    assert not any("de.bl1" in item.key for item in store.list_objects())
    original = store.put_object_if_absent
    store.put_object_if_absent = lambda *args, **kwargs: pytest.fail("overwrote existing")
    second = bulk.execute(live=True, approval=bulk.APPROVAL,
                          fetcher=fake_file, store_factory=lambda: store)
    assert second["previously_existing"] == 18
    store.put_object_if_absent = original


def test_conflicting_preexisting_object_blocks_all_writes(monkeypatch):
    monkeypatch.setattr(bulk, "get_settings", lambda: SimpleNamespace(r2_bucket=bulk.BUCKET))
    store = Store()
    _, objects = bulk.prepare("es.1", "2024-2025", fake_file(bulk.source_for("es.1", "2024-2025")))
    store.put_object(objects[0][0], b"bad", content_type="application/json")
    with pytest.raises(ValueError, match="R2_OBJECT_CONFLICT"):
        bulk.execute(live=True, approval=bulk.APPROVAL,
                     fetcher=fake_file, store_factory=lambda: store)
    assert len(store.list_objects()) == 1
    assert store.get_object(objects[0][0]) == b"bad"


def test_invalid_score_and_missing_fixture_fail_before_store():
    source = bulk.source_for("en.pl", "2024-2025")
    rows = json.loads(fake_file(source))
    rows["matches"][0]["score"]["ft"] = [True, 1]
    with pytest.raises(ValueError, match="INVALID_SOURCE_SCORE"):
        bulk.prepare(source.league, source.season, json.dumps(rows).encode())
    rows["matches"].pop()
    with pytest.raises(ValueError, match="SOURCE_EXPECTED_380_FIXTURES"):
        bulk.prepare(source.league, source.season, json.dumps(rows).encode())
