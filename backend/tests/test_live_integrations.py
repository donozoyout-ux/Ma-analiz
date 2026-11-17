"""Opt-in, real-service connectivity checks; skipped without local credentials."""
from __future__ import annotations

import hashlib
import json
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, inspect, text

from app.core.config import get_settings


def test_r2_live_bucket_probe_upload_read_checksum_delete():
    from app.storage.r2 import R2NotConfigured, R2ObjectStore

    cfg = get_settings()
    if not (cfg.r2_endpoint and cfg.r2_access_key_id and cfg.r2_secret_access_key and cfg.r2_bucket):
        pytest.skip("R2 credentials are not configured")
    try:
        store = R2ObjectStore()
        prefixes = ("matches/", "odds/", "results/", "statistics/", "predictions/",
                    "ai-analysis/", "metrics/", "lessons/")
        prefix_probe = {prefix: store.client.list_objects_v2(
            Bucket=store.bucket, Prefix=prefix, MaxKeys=1).get("KeyCount", 0) for prefix in prefixes}
        key = f"health/phase4-connectivity-{uuid4().hex}.json"
        payload = json.dumps({"phase": "phase4", "status": "ok"}, separators=(",", ":")).encode()
        try:
            store.put_object(key, payload, content_type="application/json")
            actual = store.get_object(key)
            assert actual == payload
            assert hashlib.sha256(actual).digest() == hashlib.sha256(payload).digest()
        finally:
            store.delete_object(key)
        assert store.head_object(key) is None
        assert set(prefix_probe) == set(prefixes)
    except R2NotConfigured:
        pytest.skip("R2 credentials are not configured")


def test_postgres_live_select_migration_status_and_read_only_transaction():
    cfg = get_settings()
    if not cfg.database_url:
        pytest.skip("DATABASE_URL is not configured")
    engine = create_engine(cfg.database_url, hide_parameters=True, pool_pre_ping=True)
    try:
        with engine.connect() as connection:
            with connection.begin():
                assert connection.execute(text("SELECT 1")).scalar_one() == 1
                tables = set(inspect(connection).get_table_names())
                revision = None
                if "alembic_version" in tables:
                    revision = connection.execute(text("SELECT version_num FROM alembic_version")).scalar_one_or_none()
            assert "alembic_version" in tables, "Migration status unavailable: alembic_version table is missing"
            assert revision is not None, "Migration status unavailable: no applied revision"
    finally:
        engine.dispose()


def test_groq_live_structured_evidence_round_trip():
    from app.analysis.ai import analyze_with_ai
    from app.analysis.groq import GroqProvider

    cfg = get_settings()
    api_key = cfg.groq_api_key or (cfg.ai_api_key if (cfg.ai_provider or "").lower() == "groq" else None)
    if not api_key or not cfg.groq_model:
        pytest.skip("Groq credentials or GROQ_MODEL are not configured")
    provider = GroqProvider(api_key=api_key, model=cfg.groq_model, base_url=cfg.groq_base_url,
                            timeout_seconds=15)
    try:
        output = analyze_with_ai(provider, {"match": {"home": "evidence-only", "away": "evidence-only"},
            "evidence": [{"id": "phase4-form-1", "feature": "form_sample", "value": 0.6,
                          "sample_size": 5, "source": "deterministic-test-payload",
                          "timestamp": "2026-11-16T12:00:00+00:00"}]})
        assert output.status == "AVAILABLE", output.error
        referenced = set()
        for field in ("summary", "conclusion", "strengths", "weaknesses", "positive_signals",
                      "negative_signals", "risks"):
            items = output.output[field] if isinstance(output.output[field], list) else [output.output[field]]
            referenced.update(evidence_id for item in items for evidence_id in item["evidence_ids"])
        assert referenced <= {"phase4-form-1"}
    finally:
        provider.close()
