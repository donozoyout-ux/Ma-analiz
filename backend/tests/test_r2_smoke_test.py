from types import SimpleNamespace

from app.jobs.r2_smoke_test import run_smoke
from app.storage.r2 import InMemoryObjectStore


def configured_settings():
    return SimpleNamespace(
        r2_endpoint="https://example.invalid",
        r2_access_key_id="present",
        r2_secret_access_key="present",
        r2_bucket="test-bucket",
        r2_invalid_env=(),
    )


def test_r2_smoke_reports_missing_config_without_creating_store():
    settings = SimpleNamespace(
        r2_endpoint=None,
        r2_access_key_id=None,
        r2_secret_access_key=None,
        r2_bucket=None,
        r2_invalid_env=(),
    )

    def forbidden_factory():
        raise AssertionError("R2 store must not be created when configuration is missing")

    report = run_smoke(store_factory=forbidden_factory, settings=settings)

    assert report["status"] == "NOT_CONFIGURED"
    assert report["missing_env"] == [
        "R2_ENDPOINT", "R2_ACCESS_KEY_ID", "R2_SECRET_ACCESS_KEY", "R2_BUCKET"
    ]
    assert report["write"] == report["read"] == report["delete"] == "NOT_RUN"


def test_r2_smoke_writes_verifies_and_deletes_only_its_unique_object():
    store = InMemoryObjectStore()
    store.put_object("matches/existing.parquet", b"preserve", content_type="application/octet-stream")

    report = run_smoke(store_factory=lambda: store, settings=configured_settings())

    assert report["status"] == "CONNECTED"
    assert all(report[field] == "PASS" for field in
               ("r2_connected", "write", "read", "size", "checksum", "delete"))
    assert report["object_key"].startswith("_smoke_tests/")
    assert report["object_key"].endswith("/health.txt")
    assert store.list_objects("_smoke_tests/") == []
    assert store.get_object("matches/existing.parquet") == b"preserve"


def test_r2_smoke_cleans_up_its_object_after_checksum_failure():
    class TamperedReadStore(InMemoryObjectStore):
        def get_object(self, key):
            if key.startswith("_smoke_tests/"):
                return b"tampered"
            return super().get_object(key)

    store = TamperedReadStore()
    report = run_smoke(store_factory=lambda: store, settings=configured_settings())

    assert report["status"] == "FAILED"
    assert report["read"] == "PASS"
    assert report["checksum"] == "FAIL"
    assert report["delete"] == "PASS"
    assert store.list_objects("_smoke_tests/") == []
