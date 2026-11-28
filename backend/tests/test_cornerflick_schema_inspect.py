"""All examples synthetic. No live HTTP, PostgreSQL, R2 or downloaded feed."""
import json
from pathlib import Path

import pytest
from app.providers.cornerflick_schema_inspect import MAX_BYTES, inspect_local_json


def write(tmp_path: Path, payload: bytes, filename="sample.json") -> Path:
    result = tmp_path / filename
    result.write_bytes(payload)
    return result


def test_offline_season_inspection_is_structure_only(tmp_path):
    path = write(tmp_path, json.dumps([
        {"private_team_name": "SENTETİK Takım", "score": 0, "metadata": {"last_updated_at": "2026-11-27"}},
        {"private_team_name": "Diğer Test Takımı", "score": None}
    ]).encode())
    result = inspect_local_json(path, label="season")
    assert result["field_count_inspected"] > 4
    assert result["fields"][0] == {"path": "$", "type": "array", "count": 2}
    assert any(f["path"] == "$[0].score" and f["type"] == "integer" for f in result["fields"])
    assert any(f["path"] == "$[1].score" and f["type"] == "null" for f in result["fields"])
    assert result["verified_score"] is False
    assert result["normalized_or_ingested"] is False
    serialized = json.dumps(result, ensure_ascii=False)
    assert "SENTETİK Takım" not in serialized
    assert "Diğer Test Takımı" not in serialized
    assert "2026-11-27" not in serialized


def test_offline_match_inspection_redacts_values(tmp_path):
    path = write(tmp_path, b'{"meta":{"last_updated_at":"secret"},"source":"some-feed","events":[{"player":"PERSON NAME"}]}')
    result = inspect_local_json(path, label="match")
    paths = [f["path"] for f in result["fields"]]
    assert "$.events[0].player" in paths
    assert "$.meta.last_updated_at" in paths
    assert "PERSON NAME" not in json.dumps(result)
    assert result["verified_source_permission"] is False


@pytest.mark.parametrize("bad", [
    b'{"same":1,"same":2}',
    b'{"value":NaN}',
    b'{"invalid":}',
    b'"just a string"',
    b'null',
    b"",
])
def test_invalid_or_ambiguous_json_fails_closed(tmp_path, bad):
    with pytest.raises(ValueError, match="CORNERFLICK_"):
        inspect_local_json(write(tmp_path, bad), label="match")


def test_oversize_json_rejected_before_parsing(tmp_path):
    with pytest.raises(ValueError, match="TOO_LARGE"):
        inspect_local_json(write(tmp_path, b"[" + b" " * MAX_BYTES + b"]"), label="season")


def test_label_allowlist(tmp_path):
    with pytest.raises(ValueError, match="INVALID_DOCUMENT_LABEL"):
        inspect_local_json(write(tmp_path, b"{}"), label="unknown")


def test_large_nested_arrays_are_bounded(tmp_path):
    path = write(tmp_path, json.dumps({"rows":[{"stats": list(range(200))} for _ in range(300)]}).encode())
    result = inspect_local_json(path, label="season")
    assert result["field_count_inspected"] <= 180
    assert result["fields"][0]["type"] == "object"
    assert result["verified_score"] is False
