"""Small, isolated R2 connectivity smoke test with owned-object cleanup only."""
from __future__ import annotations

import hashlib
import sys
from typing import Any, Callable
from uuid import uuid4

from botocore.exceptions import ClientError

from app.core.config import get_settings
from app.storage.r2 import R2NotConfigured, R2ObjectStore


REQUIRED_ENV = (
    "R2_ENDPOINT",
    "R2_ACCESS_KEY_ID",
    "R2_SECRET_ACCESS_KEY",
    "R2_BUCKET",
)
_PAYLOAD = b"MacAnaliz R2 connectivity smoke test\n"


def _safe_error_code(exc: Exception) -> str:
    if isinstance(exc, ClientError):
        code = exc.response.get("Error", {}).get("Code")
        if isinstance(code, str) and code:
            return code[:80]
    if isinstance(exc, R2NotConfigured):
        return "R2_NOT_CONFIGURED"
    return type(exc).__name__.upper()


def run_smoke(*, store_factory: Callable[[], Any] | None = None,
              settings: Any | None = None) -> dict[str, Any]:
    """Run one tiny put/get/hash/delete cycle without a database dependency."""
    cfg = settings or get_settings()
    env_status = {
        name: "CONFIGURED" if str(getattr(cfg, name.lower(), "") or "").strip() else "NOT_CONFIGURED"
        for name in REQUIRED_ENV
    }
    report: dict[str, Any] = {
        "status": "NOT_CONFIGURED",
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
    missing = [name for name, status in env_status.items() if status == "NOT_CONFIGURED"]
    if missing:
        report["missing_env"] = missing
        return report
    invalid = tuple(getattr(cfg, "r2_invalid_env", ()) or ())
    if invalid:
        report.update(status="ERROR", r2_connected="ERROR", error_code="INVALID_R2_CONFIGURATION",
                      invalid_env=list(invalid))
        return report

    store = None
    run_id = uuid4().hex
    key = None
    owns_object = False
    try:
        store = (store_factory() if store_factory is not None
                 else R2ObjectStore(settings=cfg))
        # A successful HEAD (including a confirmed 404 for this unique key)
        # verifies that the endpoint/bucket can be reached before any write.
        for _ in range(5):
            candidate = f"_smoke_tests/{run_id}/health.txt"
            if store.head_object(candidate) is None:
                key = candidate
                report["object_key"] = key
                break
            run_id = uuid4().hex
        if key is None:
            raise RuntimeError("UNIQUE_SMOKE_KEY_UNAVAILABLE")

        digest = hashlib.sha256(_PAYLOAD).hexdigest()
        metadata = {"macanaliz-smoke-id": run_id, "sha256": digest}
        try:
            store.put_object(key, _PAYLOAD, content_type="text/plain; charset=utf-8", metadata=metadata)
            owns_object = True
            report["r2_connected"] = "PASS"
            report["write"] = "PASS"
        except Exception:
            # A transport error may arrive after the server accepted the PUT.
            # Treat the object as ours only if its unique ownership marker is present.
            try:
                info = store.head_object(key)
                owns_object = bool(info and (info.metadata or {}).get("macanaliz-smoke-id") == run_id)
            except Exception:
                owns_object = False
            raise

        info = store.head_object(key)
        if info is None:
            raise RuntimeError("SMOKE_OBJECT_MISSING_AFTER_WRITE")
        try:
            actual = store.get_object(key)
            report["read"] = "PASS"
        except Exception:
            raise
        report["size"] = "PASS" if info.size == len(_PAYLOAD) and len(actual) == len(_PAYLOAD) else "FAIL"
        report["checksum"] = (
            "PASS"
            if hashlib.sha256(actual).hexdigest() == digest
            and (info.metadata or {}).get("sha256") == digest
            else "FAIL"
        )
        if report["size"] != "PASS" or report["checksum"] != "PASS":
            raise RuntimeError("SMOKE_OBJECT_VERIFICATION_FAILED")
        report["object_key"] = key
    except Exception as exc:
        if report["r2_connected"] != "PASS":
            report["r2_connected"] = "ERROR"
        report["error_code"] = _safe_error_code(exc)
    finally:
        if owns_object and store is not None and key is not None:
            try:
                info = store.head_object(key)
                if info is not None and (info.metadata or {}).get("macanaliz-smoke-id") == run_id:
                    store.delete_object(key)
                    report["delete"] = "PASS" if store.head_object(key) is None else "FAIL"
                else:
                    report["delete"] = "FAIL"
                    report["error_code"] = report["error_code"] or "SMOKE_OBJECT_OWNERSHIP_UNCONFIRMED"
            except Exception as exc:
                report["delete"] = "FAIL"
                report["error_code"] = report["error_code"] or _safe_error_code(exc)

    complete = all(report[field] == "PASS" for field in
                   ("r2_connected", "write", "read", "size", "checksum", "delete"))
    report["status"] = "CONNECTED" if complete else "FAILED"
    return report


def main() -> int:
    report = run_smoke()
    for name, status in report["environment"].items():
        print(f"{name}={status}")
    if report["status"] == "NOT_CONFIGURED":
        print("R2_CONNECTED=NOT_CONFIGURED")
        print("WRITE=NOT_RUN")
        print("READ=NOT_RUN")
        print("SIZE=NOT_RUN")
        print("CHECKSUM=NOT_RUN")
        print("DELETE=NOT_RUN")
        print("MISSING_ENV=" + ",".join(report.get("missing_env", [])))
        return 2
    if report.get("invalid_env"):
        print("INVALID_ENV=" + ",".join(report["invalid_env"]))

    print("R2_CONNECTED" if report["r2_connected"] == "PASS" else "R2_CONNECTED=ERROR")
    for field, token in (("write", "WRITE_PASS"), ("read", "READ_PASS"),
                         ("size", "SIZE_PASS"), ("checksum", "CHECKSUM_PASS"),
                         ("delete", "DELETE_PASS")):
        print(token if report[field] == "PASS" else f"{field.upper()}={report[field]}")
    if report.get("object_key"):
        print("OBJECT_KEY=" + report["object_key"])
    if report.get("error_code"):
        print("ERROR_CODE=" + report["error_code"])
    return 0 if report["status"] == "CONNECTED" else 1


if __name__ == "__main__":
    sys.exit(main())
