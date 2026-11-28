"""Small, isolated R2 connectivity smoke test with owned-object cleanup only."""
from __future__ import annotations

import argparse
import json
import secrets
import hashlib
import sys
from typing import Any, Callable
from uuid import uuid4

from botocore.exceptions import ClientError, EndpointConnectionError, ConnectTimeoutError, ReadTimeoutError, SSLError, NoCredentialsError

from app.core.config import get_settings
from app.storage.r2 import R2NotConfigured, R2ObjectStore


REQUIRED_ENV = (
    "R2_ENDPOINT",
    "R2_ACCESS_KEY_ID",
    "R2_SECRET_ACCESS_KEY",
    "R2_BUCKET",
)
_PAYLOAD = b"MacAnaliz R2 connectivity smoke test\n"


class ProbeFailure(RuntimeError):
    pass


_SERVICE_ERRORS = {
    "AccessDenied": "ACCESS_DENIED", "InvalidAccessKeyId": "INVALID_ACCESS_KEY",
    "SignatureDoesNotMatch": "SIGNATURE_MISMATCH", "NoSuchBucket": "BUCKET_NOT_FOUND",
    "RequestTimeout": "REQUEST_TIMEOUT", "ExpiredToken": "EXPIRED_TOKEN",
    "PreconditionFailed": "KEY_ALREADY_EXISTS", "ConditionalRequestConflict": "CONDITIONAL_WRITE_CONFLICT",
    "SlowDown": "RATE_LIMITED", "ServiceUnavailable": "SERVICE_UNAVAILABLE",
    "NotImplemented": "CONDITIONAL_WRITE_UNSUPPORTED",
}
_PROBE_ERRORS = {"UNIQUE_SMOKE_KEY_UNAVAILABLE", "SMOKE_OBJECT_MISSING_AFTER_WRITE",
                 "SMOKE_OBJECT_OWNERSHIP_UNCONFIRMED", "SMOKE_OBJECT_VERIFICATION_FAILED",
                 "PARQUET_READBACK_FAILED", "SMOKE_OBJECT_STILL_PRESENT"}


def _safe_error_code(exc: Exception) -> str:
    if isinstance(exc, ClientError):
        code = exc.response.get("Error", {}).get("Code")
        return _SERVICE_ERRORS.get(code, "R2_REQUEST_FAILED") if isinstance(code, str) else "R2_REQUEST_FAILED"
    if isinstance(exc, R2NotConfigured):
        return "R2_NOT_CONFIGURED"
    if isinstance(exc, ProbeFailure) and str(exc) in _PROBE_ERRORS:
        return str(exc)
    for kind, code in ((ConnectTimeoutError, "CONNECT_TIMEOUT"), (ReadTimeoutError, "READ_TIMEOUT"),
                       (SSLError, "TLS_ERROR"), (EndpointConnectionError, "ENDPOINT_UNREACHABLE"),
                       (NoCredentialsError, "CREDENTIALS_UNAVAILABLE"), (FileExistsError, "KEY_ALREADY_EXISTS")):
        if isinstance(exc, kind):
            return code
    return "R2_OPERATION_FAILED"


def check_bucket_read_only(*, store_factory: Callable[[], Any] = R2ObjectStore,
                           settings: Any | None = None) -> dict[str, Any]:
    """Check actual bucket reachability with HEAD only; never write or delete."""
    cfg = settings if settings is not None else get_settings()
    missing = list(getattr(cfg, "r2_missing_env", ()) or ())
    invalid = list(getattr(cfg, "r2_invalid_env", ()) or ())
    if missing or invalid:
        return {"status": "BLOCKED", "bucket": "NOT_CHECKED",
                "missing_env": missing, "invalid_env": invalid,
                "database": "NOT_USED", "write": "NOT_RUN", "delete": "NOT_RUN"}
    store = None
    try:
        store = store_factory()
        store.client.head_bucket(Bucket=store.bucket)
        return {"status": "BUCKET_REACHABLE", "bucket": "PASS",
                "database": "NOT_USED", "write": "NOT_RUN", "delete": "NOT_RUN"}
    except Exception as exc:
        return {"status": "BLOCKED", "bucket": "FAILED",
                "error_code": _safe_error_code(exc),
                "database": "NOT_USED", "write": "NOT_RUN", "delete": "NOT_RUN"}
    finally:
        if store is not None:
            close = getattr(store.client, "close", None)
            if callable(close):
                close()


def run_smoke(*, store_factory: Callable[[], Any] = R2ObjectStore,
              settings: Any | None = None, parquet: bool = False,
              check_config: bool = False) -> dict[str, Any]:
    """Run one tiny put/get/hash/delete cycle without a database dependency."""
    try:
        cfg = settings if settings is not None else get_settings()
    except Exception:
        cfg = None
    env_status = {
        name: "CONFIGURED" if str(getattr(cfg, name.lower(), "") or "").strip() else "NOT_CONFIGURED"
        for name in REQUIRED_ENV
    }
    report: dict[str, Any] = {
        "status": "NOT_CONFIGURED",
        "live_test": "NOT_RUN",
        "cleanup": "NOT_RUN",
        "error_stage": None,
        "cleanup_error_code": None,
        "client_close_error_code": None,
        "environment": env_status,
        "r2_connected": "NOT_CONFIGURED",
        "write": "NOT_RUN",
        "read": "NOT_RUN",
        "size": "NOT_RUN",
        "checksum": "NOT_RUN",
        "delete": "NOT_RUN",
        "object_key": None,
        "error_code": None,
    }
    if cfg is None:
        report.update(status="ERROR", error_code="CONFIGURATION_LOAD_FAILED", error_stage="CONFIG")
        report["environment"] = {name: "UNKNOWN" for name in REQUIRED_ENV}
        return report
    missing = [name for name, status in env_status.items() if status == "NOT_CONFIGURED"]
    if missing:
        report["missing_env"] = missing
        report.update(error_code="R2_NOT_CONFIGURED", error_stage="CONFIG")
        return report
    invalid = tuple(name for name in REQUIRED_ENV if name in (getattr(cfg, "r2_invalid_env", ()) or ()))
    if invalid:
        report.update(status="ERROR", r2_connected="ERROR", error_code="INVALID_R2_CONFIGURATION",
                      invalid_env=list(invalid), error_stage="CONFIG")
        return report

    if check_config:
        report.update(status="CONFIGURED_NOT_TESTED", r2_connected="NOT_TESTED")
        return report

    store = None
    run_id = uuid4().hex
    ownership_token = secrets.token_hex(16)
    key = None
    owns_object = False
    write_attempted = False
    stage = "WRITE"
    report["live_test"] = "ATTEMPTED"
    try:
        payload = _PAYLOAD + run_id.encode("ascii") + b"\n"
        if parquet:
            from app.storage.r2 import parquet_bytes
            payload = parquet_bytes([{"probe": "r2-zstd", "value": 1}])
        store = store_factory()
        # A successful HEAD (including a confirmed 404 for this unique key)
        # verifies that the endpoint/bucket can be reached before any write.
        for _ in range(5):
            candidate = f"_smoke_tests/{run_id}/health." + ("parquet" if parquet else "txt")
            if store.head_object(candidate) is None:
                key = candidate
                report["object_key"] = key
                break
            run_id = uuid4().hex
        if key is None:
            raise ProbeFailure("UNIQUE_SMOKE_KEY_UNAVAILABLE")

        digest = hashlib.sha256(payload).hexdigest()
        metadata = {"macanaliz-smoke-id": run_id, "macanaliz-smoke-owner": ownership_token, "sha256": digest}
        try:
            write_attempted = True
            # Atomic no-overwrite: never fall back to unconditional PUT.
            store.put_object_if_absent(key, payload, content_type="application/vnd.apache.parquet" if parquet else "text/plain; charset=utf-8", metadata=metadata)
            owns_object = True
            report["r2_connected"] = "PASS"
            report["write"] = "PASS"
        except Exception:
            # A transport error may arrive after the server accepted the PUT.
            # Treat the object as ours only if its unique ownership marker is present.
            try:
                info = store.head_object(key)
                owns_object = bool(info and (info.metadata or {}).get("macanaliz-smoke-id") == run_id
                                   and (info.metadata or {}).get("macanaliz-smoke-owner") == ownership_token)
            except Exception:
                owns_object = False
            raise

        stage = "READ"
        info = store.head_object(key)
        if info is None:
            raise ProbeFailure("SMOKE_OBJECT_MISSING_AFTER_WRITE")
        try:
            actual = store.get_object(key)
            report["read"] = "PASS"
        except Exception:
            raise
        stage = "CHECKSUM"
        report["size"] = "PASS" if info.size == len(payload) and len(actual) == len(payload) else "FAIL"
        if parquet:
            from app.storage.r2 import parquet_rows
            report["parquet_rows"] = "PASS" if parquet_rows(actual) == [{"probe": "r2-zstd", "value": 1}] else "FAIL"
            if report["parquet_rows"] != "PASS":
                raise ProbeFailure("PARQUET_READBACK_FAILED")
        report["checksum"] = (
            "PASS"
            if hashlib.sha256(actual).hexdigest() == digest
            and (info.metadata or {}).get("sha256") == digest
            else "FAIL"
        )
        if report["size"] != "PASS" or report["checksum"] != "PASS":
            raise ProbeFailure("SMOKE_OBJECT_VERIFICATION_FAILED")
        report["object_key"] = key
    except Exception as exc:
        report[stage.lower()] = "FAIL"
        report["error_stage"] = stage
        if report["r2_connected"] != "PASS":
            report["r2_connected"] = "ERROR"
        report["error_code"] = _safe_error_code(exc)
    finally:
        if write_attempted and store is not None and key is not None:
            try:
                info = store.head_object(key)
                if info is None:
                    # An unacknowledged PUT could still be in flight.
                    if not owns_object:
                        raise ProbeFailure("SMOKE_OBJECT_OWNERSHIP_UNCONFIRMED")
                elif ((info.metadata or {}).get("macanaliz-smoke-id") == run_id
                      and (info.metadata or {}).get("macanaliz-smoke-owner") == ownership_token):
                    store.delete_object(key)
                    if store.head_object(key) is not None:
                        raise ProbeFailure("SMOKE_OBJECT_STILL_PRESENT")
                else:
                    raise ProbeFailure("SMOKE_OBJECT_OWNERSHIP_UNCONFIRMED")
                report["cleanup"] = "PASS"
            except Exception as exc:
                report["cleanup"] = "FAIL"
                report["cleanup_error_code"] = _safe_error_code(exc)
                if report["error_code"] is None:
                    report.update(error_code=report["cleanup_error_code"], error_stage="CLEANUP")
        if store is not None:
            try:
                close = getattr(getattr(store, "client", None), "close", None)
                if callable(close):
                    close()
            except Exception:
                report["client_close_error_code"] = "CLIENT_CLOSE_FAILED"
                if report["error_code"] is None:
                    report.update(error_code="CLIENT_CLOSE_FAILED", error_stage="CLIENT_CLOSE")
    report["delete"] = report["cleanup"]  # Preserve existing Python callers.

    complete = all(report[field] == "PASS" for field in
                   ("r2_connected", "write", "read", "size", "checksum", "delete"))
    report["status"] = "CONNECTED" if complete and report["error_code"] is None else "FAILED"
    report["live_test"] = "PASS" if report["status"] == "CONNECTED" else "FAIL"
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--parquet", action="store_true", help="Zstandard Parquet turunu da doğrula")
    parser.add_argument("--check-config", action="store_true", help="Yalnız yapılandırmayı kontrol et; R2 isteği gönderme")
    parser.add_argument("--check-bucket", action="store_true", help="R2 bucket HEAD kontrolü; yazma veya silme yok")
    parser.add_argument("--json", action="store_true", help="Gizli değer içermeyen JSON raporu yazdır")
    args = parser.parse_args(argv)
    if args.check_bucket:
        if args.parquet or args.check_config:
            parser.error("--check-bucket başka kontrol modlarıyla birlikte kullanılamaz")
        report = check_bucket_read_only()
        print(json.dumps(report, ensure_ascii=False))
        return 0 if report["status"] == "BUCKET_REACHABLE" else 2
    report = run_smoke(parquet=args.parquet, check_config=args.check_config)
    if args.json:
        print(json.dumps(report, ensure_ascii=False))
    else:
        for name, state in report["environment"].items():
            print(f"{name}={state}")
        print("STATUS=" + report["status"])
        print("LIVE_TEST=" + report["live_test"])
        for field in ("write", "read", "checksum", "cleanup"):
            print(f"{field.upper()}={'PASS' if report[field] == 'PASS' else 'FAIL'}")
            if report[field] == "NOT_RUN":
                print(f"{field.upper()}_REASON=NOT_RUN")
        for field in ("missing_env", "invalid_env"):
            if report.get(field):
                print(field.upper() + "=" + ",".join(report[field]))
        for field in ("object_key", "error_stage", "error_code", "cleanup_error_code", "client_close_error_code"):
            if report.get(field):
                print(field.upper() + "=" + report[field])
        if "parquet_rows" in report:
            print("PARQUET_ROWS=" + report["parquet_rows"])
    if report["status"] == "CONNECTED":
        return 0
    return 2 if report["live_test"] == "NOT_RUN" else 1


if __name__ == "__main__":
    sys.exit(main())
