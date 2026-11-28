from types import SimpleNamespace
from unittest.mock import MagicMock

from app.jobs.r2_smoke_test import check_bucket_read_only


def test_unconfigured_never_constructs_r2_client():
    cfg = SimpleNamespace(r2_missing_env=("R2_ACCESS_KEY_ID",), r2_invalid_env=())
    def unreachable():
        raise AssertionError("R2 accessed")
    report = check_bucket_read_only(settings=cfg, store_factory=unreachable)
    assert report["status"] == "BLOCKED"
    assert report["write"] == report["delete"] == "NOT_RUN"


def test_bucket_head_is_only_remote_operation():
    cfg = SimpleNamespace(r2_missing_env=(), r2_invalid_env=())
    client = MagicMock()
    store = SimpleNamespace(client=client, bucket="safe-archive")
    report = check_bucket_read_only(settings=cfg, store_factory=lambda: store)
    assert report["status"] == "BUCKET_REACHABLE"
    assert report["database"] == "NOT_USED"
    assert report["write"] == report["delete"] == "NOT_RUN"
    client.head_bucket.assert_called_once_with(Bucket="safe-archive")
    client.close.assert_called_once()
    assert client.method_calls[0][0] == "head_bucket"


def test_failed_bucket_head_has_no_write_fallback():
    cfg = SimpleNamespace(r2_missing_env=(), r2_invalid_env=())
    client = MagicMock()
    client.head_bucket.side_effect = RuntimeError("unavailable")
    report = check_bucket_read_only(
        settings=cfg, store_factory=lambda: SimpleNamespace(client=client, bucket="safe-archive"))
    assert report["status"] == "BLOCKED"
    assert report["bucket"] == "FAILED"
    assert report["write"] == report["delete"] == "NOT_RUN"
    client.put_object.assert_not_called()
    client.delete_object.assert_not_called()
