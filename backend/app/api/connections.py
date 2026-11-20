"""Authenticated, bounded, read-only dependency checks. Never returns exception text."""
from datetime import datetime, timezone
import hashlib
import secrets
import threading
import time

import boto3
from botocore.config import Config as S3Config
from botocore import exceptions as aws_errors
from alembic.config import Config
from alembic.script import ScriptDirectory
from alembic.runtime.migration import MigrationContext
from fastapi import APIRouter, Depends, HTTPException, Response
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.pool import NullPool

from app.core.config import PROJECT_ROOT, get_settings
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



def schema_report(connection):
    """Inspect only; never creates or stamps an Alembic version table."""
    inspector = inspect(connection)
    default_schema = inspector.default_schema_name
    existing = set(inspector.get_table_names())
    missing_tables, missing_columns = [], []
    for name, table in sorted(Base.metadata.tables.items()):
        if name not in existing:
            missing_tables.append(name)
            continue
        columns = {column["name"] for column in inspector.get_columns(name)}
        missing_columns.extend(f"{name}.{column.name}" for column in table.columns if column.name not in columns)
    other_schemas = {}
    version_schemas = []
    for schema in inspector.get_schema_names():
        if schema == default_schema or schema == "information_schema" or schema.startswith("pg_"):
            continue
        tables = set(inspector.get_table_names(schema=schema))
        matches = sorted(tables.intersection(Base.metadata.tables))
        if matches:
            other_schemas[schema] = matches
        if "alembic_version" in tables:
            version_schemas.append(schema)
    cfg = Config(str(PROJECT_ROOT / "backend" / "alembic.ini"))
    cfg.set_main_option("script_location", str(PROJECT_ROOT / "backend" / "migrations"))
    script = ScriptDirectory.from_config(cfg)
    expected = sorted(script.get_heads())
    known = {revision.revision for revision in script.walk_revisions()}
    current = set(MigrationContext.configure(connection).get_current_heads())
    # Unknown DB strings are not echoed: version rows can contain arbitrary content.
    safe_current = sorted(current.intersection(known))
    unknown = bool(current - known)
    issues = []
    if missing_tables: issues.append("MISSING_TABLES")
    if missing_columns: issues.append("MISSING_COLUMNS")
    if other_schemas: issues.append("EXPECTED_TABLES_IN_OTHER_SCHEMAS")
    if version_schemas: issues.append("ALEMBIC_VERSION_IN_OTHER_SCHEMAS")
    if not current: issues.append("ALEMBIC_REVISION_MISSING")
    elif unknown: issues.append("ALEMBIC_REVISION_UNKNOWN")
    elif current != set(expected): issues.append("ALEMBIC_REVISION_BEHIND_OR_DIVERGED")
    if current == set(expected) and (missing_tables or missing_columns):
        issues.append("REVISION_SCHEMA_MISMATCH")
    return {"schema": "INCOMPLETE" if issues else "READY", "default_schema": default_schema,
            "missing_tables": missing_tables, "missing_columns": missing_columns,
            "other_schemas": other_schemas, "alembic_version_other_schemas": version_schemas,
            "current_revisions": safe_current, "unknown_revision": unknown,
            "expected_revisions": expected, "schema_issues": issues}


def r2_error_category(exc):
    # Only fixed categories leave this function. HeadBucket can omit detailed codes.
    if isinstance(exc, (aws_errors.ConnectTimeoutError, aws_errors.ReadTimeoutError)):
        return "TIMEOUT"
    if isinstance(exc, (aws_errors.NoCredentialsError, aws_errors.PartialCredentialsError)):
        return "AUTHENTICATION"
    if isinstance(exc, aws_errors.EndpointConnectionError):
        return "NETWORK"  # DNS/unreachable host; does not prove a bad endpoint.
    if isinstance(exc, (aws_errors.ConnectionClosedError, aws_errors.SSLError, aws_errors.ProxyConnectionError)):
        return "NETWORK"
    if isinstance(exc, aws_errors.ClientError):
        code = exc.response.get("Error", {}).get("Code", "")
        status = exc.response.get("ResponseMetadata", {}).get("HTTPStatusCode")
        if code in {"InvalidAccessKeyId", "SignatureDoesNotMatch", "InvalidToken", "ExpiredToken", "AuthFailure"} or status == 401:
            return "AUTHENTICATION"
        if code == "AccessDenied": return "AUTHORIZATION"
        if status == 403: return "AUTHENTICATION_OR_AUTHORIZATION"
        if code in {"NoSuchBucket", "PermanentRedirect", "AuthorizationHeaderMalformed", "InvalidEndpoint"} or status in {301, 307, 404}:
            return "ENDPOINT_OR_BUCKET"
        if code in {"RequestTimeout", "RequestTimeoutException"} or status in {408, 504}:
            return "TIMEOUT"
    if isinstance(exc, ValueError): return "ENDPOINT_CONFIGURATION"
    return "UNKNOWN"


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
            result.update(schema_report(connection))
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
    except Exception as exc:
        result.update(status="FAILED", error_code="R2_ACCESS_CHECK_FAILED", error_category=r2_error_category(exc))
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
