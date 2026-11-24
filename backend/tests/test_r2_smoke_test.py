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


class GuardedStore(InMemoryObjectStore):
    """Assert that the probe never lists, overwrites, or deletes foreign keys."""
    def __init__(self):
        super().__init__()
        self.deleted = []
        self.client = SimpleNamespace(close=lambda: setattr(self, "closed", True))
        self.closed = False
        super().put_object("matches/archive.parquet", b"archive", content_type="application/octet-stream")

    def list_objects(self, prefix=""):
        raise AssertionError("Probe must not enumerate the bucket")

    def put_object_if_absent(self, key, body, **kwargs):
        assert key.startswith("_smoke_tests/") and key.endswith("/health.txt")
        assert key not in self._objects
        super().put_object_if_absent(key, body, **kwargs)

    def delete_object(self, key):
        assert key.startswith("_smoke_tests/")
        self.deleted.append(key)
        super().delete_object(key)


def test_no_bucket_listing_and_sdk_client_closed():
    store = GuardedStore()
    first = run_smoke(store_factory=lambda: store, settings=configured_settings())
    second = run_smoke(store_factory=lambda: store, settings=configured_settings())
    assert first["cleanup"] == first["delete"] == "PASS"
    assert first["object_key"] != second["object_key"]
    assert store.deleted == [first["object_key"], second["object_key"]]
    assert store.closed
    assert store.get_object("matches/archive.parquet") == b"archive"


def test_configuration_only_never_constructs_a_client():
    def forbidden():
        raise AssertionError("No R2 access allowed")
    report = run_smoke(settings=configured_settings(), store_factory=forbidden, check_config=True)
    assert report["status"] == "CONFIGURED_NOT_TESTED"
    assert report["live_test"] == "NOT_RUN"
    assert report["object_key"] is None
    assert all(report[field] == "NOT_RUN" for field in ("write", "read", "checksum", "cleanup"))


def test_invalid_configuration_reports_names_only_and_never_constructs_client():
    settings = configured_settings()
    settings.r2_invalid_env = ("R2_ENDPOINT", "secret-that-must-not-appear")
    report = run_smoke(settings=settings, store_factory=lambda: (_ for _ in ()).throw(AssertionError()))
    assert report["invalid_env"] == ["R2_ENDPOINT"]
    assert "secret-that-must-not-appear" not in str(report)
    assert report["live_test"] == "NOT_RUN"


def test_configuration_loader_failure_is_redacted(monkeypatch):
    def broken():
        raise ValueError("secret-url-and-password")
    monkeypatch.setattr("app.jobs.r2_smoke_test.get_settings", broken)
    report = run_smoke()
    assert report["error_code"] == "CONFIGURATION_LOAD_FAILED"
    assert "secret-url-and-password" not in str(report)


def test_conditional_write_collision_preserves_foreign_object():
    class Collision(GuardedStore):
        def put_object_if_absent(self, key, body, **kwargs):
            # Another writer wins after HEAD. Even a matching run ID is not ownership.
            InMemoryObjectStore.put_object(self, key, b"foreign", content_type="text/plain",
                metadata={"macanaliz-smoke-id": kwargs["metadata"]["macanaliz-smoke-id"]})
            raise FileExistsError("sensitive-key")
    store = Collision()
    report = run_smoke(settings=configured_settings(), store_factory=lambda: store)
    assert report["write"] == "FAIL" and report["status"] == "FAILED"
    assert report["error_code"] == "KEY_ALREADY_EXISTS"
    assert report["cleanup"] == "FAIL"
    assert store.get_object(report["object_key"]) == b"foreign" and not store.deleted
    assert store.get_object("matches/archive.parquet") == b"archive"
    assert store.closed and "sensitive-key" not in str(report)


def test_read_failure_still_cleans_owned_object_and_redacts_error():
    class Unreadable(GuardedStore):
        def get_object(self, key):
            if key.startswith("_smoke_tests/"):
                raise RuntimeError("password=must-not-appear")
            return super().get_object(key)
    store = Unreadable()
    report = run_smoke(settings=configured_settings(), store_factory=lambda: store)
    assert report["write"] == "PASS" and report["read"] == "FAIL"
    assert report["checksum"] == "NOT_RUN" and report["cleanup"] == "PASS"
    assert report["status"] == "FAILED" and report["error_stage"] == "READ"
    assert "must-not-appear" not in str(report) and store.closed


def test_ambiguous_write_response_only_cleans_confirmed_owned_object():
    class LostResponse(GuardedStore):
        def put_object_if_absent(self, key, body, **kwargs):
            super().put_object_if_absent(key, body, **kwargs)
            raise TimeoutError("secret-endpoint")
    store = LostResponse()
    report = run_smoke(settings=configured_settings(), store_factory=lambda: store)
    assert report["write"] == "FAIL" and report["cleanup"] == "PASS"
    assert store.head_object(report["object_key"]) is None
    assert report["status"] == "FAILED" and "secret-endpoint" not in str(report)


def test_cleanup_failure_does_not_mask_checksum_failure():
    from botocore.exceptions import ClientError
    class BrokenCleanup(GuardedStore):
        def get_object(self, key):
            return b"tampered" if key.startswith("_smoke_tests/") else super().get_object(key)
        def delete_object(self, key):
            raise ClientError({"Error": {"Code": "AccessDenied", "Message": "secret"}}, "DeleteObject")
    store = BrokenCleanup()
    report = run_smoke(settings=configured_settings(), store_factory=lambda: store)
    assert report["checksum"] == "FAIL" and report["cleanup"] == "FAIL"
    assert report["error_stage"] == "CHECKSUM"
    assert report["error_code"] == "SMOKE_OBJECT_VERIFICATION_FAILED"
    assert report["cleanup_error_code"] == "ACCESS_DENIED"
    assert store.head_object(report["object_key"]) is not None
    assert store.closed and "secret" not in str(report)


def test_replaced_owner_is_never_deleted():
    class ChangedOwner(GuardedStore):
        def get_object(self, key):
            actual = super().get_object(key)
            if key.startswith("_smoke_tests/"):
                InMemoryObjectStore.put_object(self, key, b"replacement", content_type="text/plain")
            return actual
    store = ChangedOwner()
    report = run_smoke(settings=configured_settings(), store_factory=lambda: store)
    assert report["cleanup"] == "FAIL" and not store.deleted
    assert report["status"] == "FAILED"
    assert store._objects[report["object_key"]][0] == b"replacement"


def test_acknowledged_delete_is_verified_by_head():
    class NoOpDelete(GuardedStore):
        def delete_object(self, key):
            pass
    store = NoOpDelete()
    report = run_smoke(settings=configured_settings(), store_factory=lambda: store)
    assert report["write"] == report["read"] == report["checksum"] == "PASS"
    assert report["cleanup"] == "FAIL" and report["status"] == "FAILED"
    assert report["cleanup_error_code"] == "SMOKE_OBJECT_STILL_PRESENT"


def test_unknown_service_error_code_and_message_are_redacted():
    from app.jobs.r2_smoke_test import _safe_error_code
    from botocore.exceptions import ClientError
    exc = ClientError({"Error": {"Code": "credential-sensitive-code", "Message": "secret"}}, "PutObject")
    assert _safe_error_code(exc) == "R2_REQUEST_FAILED"


def test_sdk_put_uses_atomic_if_none_match():
    from app.storage.r2 import R2ObjectStore
    store = object.__new__(R2ObjectStore)
    calls = []
    store.bucket = "test-bucket"
    store.client = SimpleNamespace(put_object=lambda **kwargs: calls.append(kwargs))
    store.put_object_if_absent("_smoke_tests/test/health.txt", b"small", content_type="text/plain")
    assert calls[0]["IfNoneMatch"] == "*"
    assert calls[0]["Key"] == "_smoke_tests/test/health.txt"


def test_cli_reports_four_stages_and_distinguishes_not_run(monkeypatch, capsys):
    import app.jobs.r2_smoke_test as command
    report = run_smoke(settings=configured_settings(), store_factory=GuardedStore)
    monkeypatch.setattr(command, "run_smoke", lambda **kwargs: report)
    assert command.main([]) == 0
    output = capsys.readouterr().out
    for stage in ("WRITE", "READ", "CHECKSUM", "CLEANUP"):
        assert f"{stage}=PASS" in output
    report = run_smoke(settings=configured_settings(), check_config=True)
    assert command.main(["--check-config"]) == 2
    output = capsys.readouterr().out
    assert "LIVE_TEST=NOT_RUN" in output
    assert "WRITE=FAIL" in output and "WRITE_REASON=NOT_RUN" in output
    assert "CLEANUP=PASS" not in output
