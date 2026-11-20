"""Authenticated, bounded, read-only dependency checks. Never returns exception text."""
from datetime import datetime, timezone
import hashlib
import secrets
import threading
import time

import boto3
from botocore.config import Config as S3Config
from fastapi import APIRouter, Depends, HTTPException, Response
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.pool import NullPool

from app.core.config import get_settings
from app.db.base import Base
from app import models  # noqa: F401: register required tables

router = APIRouter()
bearer = HTTPBearer(auto_error=False)
_lock = threading.Lock()
_last_check = 0.0


def authorize(credentials: HTTPAuthorizationCredentials | None = Depends(bearer)):
    settings = get_settings()
    configured = settings.system_diagnostics_token
    token = configured.get_secret_value() if configured else ""
    if len(token) < 32:
        raise HTTPException(503, detail="SYSTEM_DIAGNOSTICS_TOKEN_REQUIRED_MIN_32_CHARACTERS")
    if credentials is None or not secrets.compare_digest(
        hashlib.sha256(credentials.credentials.encode()).digest(),
        hashlib.sha256(token.encode()).digest(),
    ):
        raise HTTPException(401, detail="UNAUTHORIZED", headers={"WWW-Authenticate": "Bearer"})
    return settings


def postgres_check(settings):
    result = {"status": "NOT_CONFIGURED", "missing_env": [], "invalid_env": [],
              "schema": "NOT_VERIFIED", "missing_tables": [], "missing_columns": []}
    if settings.database_url_missing:
        result["missing_env"] = ["DATABASE_URL"]
        return result
    if not settings.database_url_valid:
        result.update(status="INVALID_CONFIGURATION", invalid_env=["DATABASE_URL"])
        return result
    engine = None
    try:
        url = settings.database_url.replace("postgresql://", "postgresql+psycopg://", 1)
        engine = create_engine(url, hide_parameters=True, echo=False, poolclass=NullPool,
                               connect_args={"connect_timeout": 5})
        with engine.connect() as connection, connection.begin():
            connection.execute(text("SET TRANSACTION READ ONLY"))
            connection.execute(text("SET LOCAL statement_timeout = '5s'"))
            connection.execute(text("SELECT 1")).scalar_one()
            result["status"] = "CONNECTED"
            inspector = inspect(connection)
            existing = set(inspector.get_table_names())
            for name, table in sorted(Base.metadata.tables.items()):
                if name not in existing:
                    result["missing_tables"].append(name)
                    continue
                columns = {column["name"] for column in inspector.get_columns(name)}
                result["missing_columns"].extend(f"{name}.{column.name}" for column in table.columns
                                                  if column.name not in columns)
            result["schema"] = "INCOMPLETE" if result["missing_tables"] or result["missing_columns"] else "READY"
    except Exception:
        result["status"] = "FAILED" if result["status"] != "CONNECTED" else "CONNECTED"
        result["schema"] = "NOT_VERIFIED"
        result["error_code"] = "POSTGRES_CHECK_FAILED"
    finally:
        if engine is not None:
            engine.dispose()
    return result


def r2_check(settings):
    result = {"status": "NOT_CONFIGURED", "missing_env": list(settings.r2_missing_env),
              "invalid_env": list(settings.r2_invalid_env), "operation": "HEAD_BUCKET"}
    if not settings.r2_configured:
        if result["invalid_env"]:
            result["status"] = "INVALID_CONFIGURATION"
        return result
    try:
        with boto3.client("s3", endpoint_url=settings.r2_endpoint,
                          aws_access_key_id=settings.r2_access_key_id,
                          aws_secret_access_key=settings.r2_secret_access_key,
                          region_name=settings.r2_region,
                          config=S3Config(connect_timeout=5, read_timeout=5,
                                          retries={"total_max_attempts": 1})) as client:
            client.head_bucket(Bucket=settings.r2_bucket)
        result["status"] = "ACCESSIBLE"
    except Exception:
        result.update(status="FAILED", error_code="R2_ACCESS_CHECK_FAILED")
    return result


@router.post("/system/connections")
def connections(response: Response, settings=Depends(authorize)):
    global _last_check
    response.headers["Cache-Control"] = "no-store"
    if not _lock.acquire(blocking=False):
        raise HTTPException(429, detail="CHECK_IN_PROGRESS", headers={"Retry-After": "30"})
    try:
        if time.monotonic() - _last_check < 30:
            raise HTTPException(429, detail="WAIT_30_SECONDS", headers={"Retry-After": "30"})
        _last_check = time.monotonic()
        return {"checked_at": datetime.now(timezone.utc).isoformat(), "read_only": True,
                "postgres": postgres_check(settings), "r2": r2_check(settings)}
    finally:
        _lock.release()
