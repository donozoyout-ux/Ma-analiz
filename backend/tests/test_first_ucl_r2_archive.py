"""No-DB immutable R2-only archive contract tests."""
import hashlib

import pytest

from app.jobs import first_ucl_r2_archive as operator
from app.storage.r2 import InMemoryObjectStore


def fixture_rows():
    return [{"id": f"fixture-{i}", "regulation": [2, 1], "league": "uefa.cl"}
            for i in range(189)]


def test_r2_only_archive_creates_3_immutable_objects_and_reuses(monkeypatch):
    store = InMemoryObjectStore()
    monkeypatch.setattr(operator, "validate_source", lambda *args: fixture_rows())
    raw = b"pinned-source-fixture"
    first = operator.archive_source_only(store, raw)
    assert first["status"] == "R2_ARCHIVE_VERIFIED"
    assert first["fixtures"] == first["regulation_scores"] == 189
    assert first["postgresql"] == "NOT_USED"
    assert len(store.list_objects()) == 3
    second = operator.archive_source_only(store, raw)
    assert first["receipts"] == second["receipts"]
    assert len(store.list_objects()) == 3
    for receipt in first["receipts"].values():
        assert receipt["sha256"] == hashlib.sha256(store.get_object(receipt["key"])).hexdigest()


def test_r2_only_archive_detects_corruption_without_overwriting(monkeypatch):
    store = InMemoryObjectStore()
    monkeypatch.setattr(operator, "validate_source", lambda *args: fixture_rows())
    raw = b"pinned-source-fixture"
    first = operator.archive_source_only(store, raw)
    original = first["receipts"]["matches"]
    store.put_object(original["key"], b"corrupt", content_type="application/octet-stream")
    with pytest.raises(RuntimeError, match="R2_ARCHIVE_RECEIPT_MISMATCH"):
        operator.archive_source_only(store, raw)
    assert store.get_object(original["key"]) == b"corrupt"


def test_r2_only_archive_failure_can_resume_without_sql(monkeypatch):
    store = InMemoryObjectStore()
    monkeypatch.setattr(operator, "validate_source", lambda *args: fixture_rows())
    raw = b"pinned-source-fixture"
    original = store.put_object_if_absent
    def fail_manifest(key, *args, **kwargs):
        if "/manifests/" in key:
            raise RuntimeError("simulated R2 interruption")
        return original(key, *args, **kwargs)
    monkeypatch.setattr(store, "put_object_if_absent", fail_manifest)
    with pytest.raises(RuntimeError, match="simulated R2 interruption"):
        operator.archive_source_only(store, raw)
    assert len(store.list_objects()) == 2
    monkeypatch.setattr(store, "put_object_if_absent", original)
    assert operator.archive_source_only(store, raw)["objects_verified"] == 3
    assert len(store.list_objects()) == 3


def test_r2_only_live_requires_explicit_approval_before_network(monkeypatch):
    monkeypatch.setattr(operator.httpx, "Client", lambda **kw: pytest.fail("network accessed"))
    with pytest.raises(RuntimeError, match="R2_OPERATOR_APPROVAL_REQUIRED"):
        operator.run(live=True)
    with pytest.raises(RuntimeError, match="DRY_RUN_DOES_NOT_ACCEPT_APPROVAL"):
        operator.run(live=False, approval=operator.APPROVAL)


def test_r2_only_dry_run_avoids_r2_and_sql(monkeypatch):
    import httpx
    class MockClient:
        def __enter__(self):
            return self
        def __exit__(self, *args):
            return None
    monkeypatch.setattr(httpx, "Client", lambda **kw: MockClient())
    monkeypatch.setattr(operator, "download", lambda *args: b"verified-source")
    monkeypatch.setattr(operator, "validate_source", lambda *args: fixture_rows())
    report = operator.run(live=False, store_factory=lambda: pytest.fail("R2 accessed"))
    assert report["status"] == "DRY_RUN"
    assert report["r2"] == "NOT_ACCESSED"
    assert report["postgresql"] == "NOT_USED"
