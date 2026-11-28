"""R2-only Bundesliga bulk safety contracts with synthetic fixtures."""
import json
from datetime import date
from types import SimpleNamespace

import pytest

from app.jobs import bundesliga_season_r2 as bulk
from app.storage.r2 import InMemoryObjectStore


def source_file(season):
    year = int(season[:4])
    return json.dumps({"matches": [
        {"round": f"Matchday {i + 1}", "date": f"{year}-08-01", "team1": f"Home {i}",
         "team2": f"Away {i}", "score": {"ht": [0, 0], "ft": [1, 0]}}
        for i in range(306)
    ]}).encode()


@pytest.fixture
def sources(monkeypatch):
    values = {season: source_file(season) for season in bulk.HASHES}
    monkeypatch.setattr(bulk, "HASHES", {season: bulk.digest(raw) for season, raw in values.items()})
    monkeypatch.setattr(bulk, "download", lambda source, client, budget: values[source.season])
    monkeypatch.setattr(bulk, "get_settings", lambda: SimpleNamespace(r2_bucket=bulk.BUCKET))
    return values


class Store(InMemoryObjectStore):
    bucket = bulk.BUCKET
    client = SimpleNamespace(close=lambda: None)


def test_approval_dry_run_live_and_replay(sources):
    store = Store()
    with pytest.raises(RuntimeError, match="LIVE_APPROVAL_REQUIRED"):
        bulk.run(live=True, store_factory=lambda: pytest.fail("store constructed"))
    report = bulk.run(store_factory=lambda: store)
    assert report["status"] == "BLOCKED_AWAITING_LIVE_APPROVAL"
    assert len(store.list_objects()) == 0
    assert report["object_count"] == 9
    first = bulk.run(live=True, approval=bulk.APPROVAL, store_factory=lambda: store)
    assert first["status"] == "R2_ARCHIVE_VERIFIED" and first["existing_objects"] == 0
    original = store.put_object_if_absent
    store.put_object_if_absent = lambda *args, **kwargs: pytest.fail("duplicate upload")
    second = bulk.run(live=True, approval=bulk.APPROVAL, store_factory=lambda: store)
    assert second["existing_objects"] == 9 and len(store.list_objects()) == 9
    store.put_object_if_absent = original


def test_bad_hash_count_cutoff_and_unlabelled_score(sources, monkeypatch):
    season = "2026-2027"
    raw = json.loads(sources[season])
    raw["matches"][0]["date"] = "2026-11-29"
    raw["matches"][1]["score"] = [0, 0]
    data = json.dumps(raw).encode()
    with pytest.raises(RuntimeError, match="SOURCE_SHA256_MISMATCH"):
        bulk.prepare(season, data)
    monkeypatch.setitem(bulk.HASHES, season, bulk.digest(data))
    report, _ = bulk.prepare(season, data)
    assert report["matches"] == 305 and report["scores"] == 304
    assert report["unknown_status"] == 1
    assert all(row["date"] <= "2026-11-28" for row in report["rows"])
    raw["matches"][2]["score"]["ft"] = [True, 1]
    data = json.dumps(raw).encode()
    monkeypatch.setitem(bulk.HASHES, season, bulk.digest(data))
    with pytest.raises(RuntimeError, match="INVALID_SOURCE_SCORE"):
        bulk.prepare(season, data)
    raw["matches"].pop()
    data = json.dumps(raw).encode()
    monkeypatch.setitem(bulk.HASHES, season, bulk.digest(data))
    with pytest.raises(RuntimeError, match="SOURCE_SEASON_COUNT_MISMATCH"):
        bulk.prepare(season, data)


def test_daily_identity_and_corrupt_existing_object(sources):
    store = Store()
    report, artifacts = bulk.prepare("2026-2027", sources["2026-2027"])
    row = report["rows"][0]
    daily = {"league": "de.bl1", "provider": "openligadb", "date": row["date"], "matches": [{
        "league_id": "de.bl1", "match_date": row["date"], "home_team": row["home_team"],
        "away_team": row["away_team"], "provider_match_id": "123"}]}
    body = bulk.canonical(daily)
    key = bulk.DAILY_PREFIX + "date=" + row["date"] + "/" + bulk.digest(body) + ".json"
    store.put_object(key, body, content_type="application/json", metadata={"sha256": bulk.digest(body)})
    result = bulk.compare_daily(store, report["rows"])
    assert result["matched"] == 1 and result["unmatched"] == result["ambiguous"] == 0
    assert result["links"][0]["openligadb_id"] == "123"
    store.put_object(artifacts[0][0], b"corrupt", content_type="application/json")
    with pytest.raises(RuntimeError, match="ARCHIVE_OBJECT_CONFLICT"):
        bulk.run(live=True, approval=bulk.APPROVAL, store_factory=lambda: store)
    assert store.get_object(artifacts[0][0]) == b"corrupt"
