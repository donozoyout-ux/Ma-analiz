"""Read-only Bundesliga season R2 endpoint: integrity and pagination tests."""
import json
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app.api import bundesliga_r2_archive as archive
from app.main import app
from app.storage.r2 import InMemoryObjectStore, parquet_bytes


def setup_archive(monkeypatch):
    store = InMemoryObjectStore()
    store.bucket = archive.BUCKET
    monkeypatch.setattr(archive, "SEASONS", {"2024-2025": (2, 1, "a" * 64)})
    rows = [
        {"id": "1", "league": "de.bl1", "season": "2024-2025", "date": "2024-11-01",
         "home_team": "Bayern", "away_team": "Dortmund", "round": "Matchday 1",
         "status": "FINISHED", "regulation": [2, 1], "verification": "SOURCE_REPORTED"},
        {"id": "2", "league": "de.bl1", "season": "2024-2025", "date": "2024-11-02",
         "home_team": "Union", "away_team": "Leipzig", "round": "Matchday 1",
         "status": "UNKNOWN", "regulation": None, "verification": "SOURCE_REPORTED"},
    ]
    prefix = "season-bulk/openfootball/season=2024-2025/league=de.bl1"
    raw = b"pinned-data"
    raw_hash = archive._digest(raw)
    monkeypatch.setattr(archive, "SEASONS", {"2024-2025": (2, 1, raw_hash)})
    parq = parquet_bytes(rows)
    def receipt(folder, data, ext):
        key = f"{prefix}/{folder}/{archive._digest(data)}.{ext}"
        store.put_object(key, data, content_type="application/octet-stream")
        return {"key": key, "sha256": archive._digest(data), "size": len(data)}
    raw_ref = receipt("raw", raw, "json")
    parq_ref = receipt("matches", parq, "parquet")
    parq_ref["row_count"] = 2
    manifest = {
        "season": "2024-2025", "league": "de.bl1", "revision": archive.REVISION,
        "licence": "CC0-1.0", "cutoff": archive.CUTOFF, "matches": 2, "scores": 1,
        "score_verification": "SOURCE_REPORTED", "raw": raw_ref, "parquet": parq_ref,
    }
    manifest_raw = json.dumps(manifest).encode()
    manifest_key = f"{prefix}/manifests/{archive._digest(manifest_raw)}.json"
    store.put_object(manifest_key, manifest_raw, content_type="application/json")
    return store, parq_ref


def test_reader_rejects_corruption(monkeypatch):
    store, ref = setup_archive(monkeypatch)
    assert len(archive.read_bundesliga_season(store, "2024-2025")) == 2
    store.put_object(ref["key"], b"corrupt", content_type="application/octet-stream")
    with pytest.raises(ValueError, match="BUNDESLIGA_OBJECT_HASH_MISMATCH"):
        archive.read_bundesliga_season(store, "2024-2025")


def test_endpoint_filters_and_does_not_write(monkeypatch):
    store, _ = setup_archive(monkeypatch)
    store.client = SimpleNamespace(close=lambda: None)
    monkeypatch.setattr(archive, "R2ObjectStore", lambda: store)
    response = TestClient(app).get("/api/v1/archive/r2/bundesliga",
                                   params={"season": "2024-2025", "team": "bayern"})
    assert response.status_code == 200
    assert response.json()["total"] == 1
    assert response.json()["matches"][0]["home_team"] == "Bayern"
    assert len(store.list_objects()) == 3


def test_endpoint_rejects_unknown_season_before_storage(monkeypatch):
    monkeypatch.setattr(archive, "R2ObjectStore", lambda: pytest.fail("R2 accessed"))
    response = TestClient(app).get("/api/v1/archive/r2/bundesliga",
                                   params={"season": "2030-2031"})
    assert response.status_code == 422


def test_endpoint_fails_closed_on_bucket_mismatch(monkeypatch):
    store, _ = setup_archive(monkeypatch)
    store.bucket = "wrong-bucket"
    store.client = SimpleNamespace(close=lambda: None)
    monkeypatch.setattr(archive, "R2ObjectStore", lambda: store)
    response = TestClient(app).get("/api/v1/archive/r2/bundesliga")
    assert response.status_code == 503


def test_archive_date_and_score_filters(monkeypatch):
    store, _ = setup_archive(monkeypatch)
    store.client = SimpleNamespace(close=lambda: None)
    monkeypatch.setattr(archive, "R2ObjectStore", lambda: store)
    client = TestClient(app)
    scored = client.get("/api/v1/archive/r2/bundesliga",
        params={"season": "2024-2025", "match_date": "2024-11-01", "score_filter": "scored"})
    assert scored.status_code == 200
    assert scored.json()["total"] == 1
    assert scored.json()["matches"][0]["regulation"] == [2, 1]
    unknown = client.get("/api/v1/archive/r2/bundesliga",
        params={"season": "2024-2025", "score_filter": "unknown"})
    assert unknown.status_code == 200
    assert unknown.json()["total"] == 1
    assert unknown.json()["matches"][0]["regulation"] is None
    empty = client.get("/api/v1/archive/r2/bundesliga",
        params={"season": "2024-2025", "match_date": "2024-11-03"})
    assert empty.status_code == 200 and empty.json()["total"] == 0
    invalid = client.get("/api/v1/archive/r2/bundesliga",
        params={"season": "2024-2025", "score_filter": "guessed"})
    assert invalid.status_code == 422
