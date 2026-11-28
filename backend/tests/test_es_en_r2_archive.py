"""Read-only Spain/England R2 archive tests with a synthetic in-memory store."""
import json
from types import SimpleNamespace

from fastapi.testclient import TestClient
import pytest

from app.api import es_en_r2_archive as api
from app.main import app
from app.storage.r2 import InMemoryObjectStore, parquet_bytes


def make_archive(monkeypatch, league="es.1", season="2024-2025"):
    store = InMemoryObjectStore()
    store.bucket = api.BUCKET
    store.client = SimpleNamespace(close=lambda: None)
    rows = [
        {"id": "one", "league": league, "season": season, "date": "2024-08-25",
         "home_team": "Home", "away_team": "Away", "round": "Matchday 1",
         "status": "FINISHED", "regulation": [2, 1], "verification": "SOURCE_REPORTED"},
        {"id": "two", "league": league, "season": season, "date": "2024-09-01",
         "home_team": "Next", "away_team": "Other", "round": "Matchday 2",
         "status": "UNKNOWN", "regulation": None, "verification": "SOURCE_REPORTED"},
    ]
    raw = b"synthetic-public-fixtures"
    raw_sha = api.digest(raw)
    monkeypatch.setattr(api, "EXPECTED", {(league, season): (2, 1, raw_sha)})
    prefix = f"season-bulk/openfootball/season={season}/league={league}"
    def receipt(folder, body, ext):
        sha = api.digest(body)
        key = f"{prefix}/{folder}/{sha}.{ext}"
        store.put_object(key, body, content_type="application/octet-stream", metadata={"sha256": sha})
        return {"key": key, "sha256": sha, "size": len(body)}
    raw_receipt = receipt("raw", raw, "json")
    parquet_receipt = receipt("matches", parquet_bytes(rows), "parquet")
    parquet_receipt["row_count"] = 2
    manifest = {"league": league, "season": season, "revision": api.REVISION,
                "source": api.source_for(league, season).url, "licence": "CC0-1.0",
                "cutoff": api.CUTOFF.isoformat(), "matches": 2, "scores": 1,
                "score_verification": "SOURCE_REPORTED", "raw": raw_receipt, "parquet": parquet_receipt}
    payload = json.dumps(manifest).encode()
    store.put_object(f"{prefix}/manifests/{api.digest(payload)}.json", payload,
                     content_type="application/json")
    return store, parquet_receipt


@pytest.mark.parametrize("league", ["es.1", "en.pl"])
def test_reader_filters_and_never_writes(monkeypatch, league):
    store, _ = make_archive(monkeypatch, league=league)
    monkeypatch.setattr(api, "R2ObjectStore", lambda: store)
    response = TestClient(app).get("/api/v1/archive/r2/es-en",
                                  params={"league": league, "season": "2024-2025", "team": "Home"})
    assert response.status_code == 200
    data = response.json()
    assert data["league"] == league and data["total"] == 1
    assert data["matches"][0]["regulation"] == [2, 1]
    assert len(store.list_objects()) == 3


def test_corrupt_object_fails_closed(monkeypatch):
    store, receipt = make_archive(monkeypatch)
    store.put_object(receipt["key"], b"tampered", content_type="application/octet-stream")
    monkeypatch.setattr(api, "R2ObjectStore", lambda: store)
    response = TestClient(app).get("/api/v1/archive/r2/es-en")
    assert response.status_code == 503


def test_invalid_scope_before_storage(monkeypatch):
    monkeypatch.setattr(api, "R2ObjectStore", lambda: pytest.fail("R2 must not be accessed"))
    response = TestClient(app).get("/api/v1/archive/r2/es-en", params={"league": "fr.1"})
    assert response.status_code == 422
