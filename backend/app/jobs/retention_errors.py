"""Fixed public diagnostics. Exception text/SQL/credentials never leave this module."""
import socket
import ssl

import psycopg
from sqlalchemy import exc as sa

STAGES = frozenset({"CONFIGURATION", "SQL_CONNECTION", "SQL_QUERY", "SCHEMA_CHECK", "LEDGER_CLAIM",
                    "LEDGER_FINISH", "SCHEDULER_AUDIT", "RETENTION", "R2_VERIFICATION", "RETENTION_LOCK"})
STATES = {"28P01": "SQL_AUTHENTICATION_FAILED", "28000": "SQL_AUTHENTICATION_FAILED",
          "3D000": "SQL_DATABASE_NOT_FOUND", "42501": "SQL_PERMISSION_DENIED",
          "42P01": "SQL_TABLE_MISSING", "42703": "SQL_COLUMN_MISSING", "3F000": "SQL_SCHEMA_MISSING",
          "23505": "SQL_UNIQUE_CONSTRAINT", "23502": "SQL_NOT_NULL_CONSTRAINT",
          "23514": "SQL_CHECK_CONSTRAINT", "22001": "SQL_VALUE_TOO_LONG",
          "55P03": "SQL_LOCK_TIMEOUT", "40P01": "SQL_DEADLOCK", "40001": "SQL_SERIALIZATION_FAILURE",
          "53300": "SQL_CONNECTION_LIMIT", "57P01": "SQL_SERVER_UNAVAILABLE"}


def error_code(error, stage):
    # SQLAlchemy wraps the actual DBAPI error in .orig. Follow a bounded chain,
    # but never stringify the outer wrapper (it can contain SQL and parameters).
    chain, seen = [], set()
    current = error
    while current is not None and id(current) not in seen and len(chain) < 8:
        chain.append(current); seen.add(id(current))
        current = getattr(current, "orig", None) or getattr(current, "__cause__", None) or getattr(current, "__context__", None)
    for item in chain:
        state = getattr(item, "sqlstate", None) or getattr(item, "pgcode", None)
        if isinstance(state, str) and state in STATES: return STATES[state]
        if isinstance(state, str) and state.startswith("08"): return "SQL_CONNECTION_FAILED"
        if state == "57014":
            return "SQL_STATEMENT_TIMEOUT" if isinstance(item, psycopg.Error) and "statement timeout" in str(item).lower() else "SQL_QUERY_CANCELLED"
    for item in chain:
        if isinstance(item, sa.TimeoutError): return "SQL_POOL_TIMEOUT"
        if isinstance(item, (TimeoutError, socket.timeout)): return "SQL_CONNECTION_TIMEOUT"
        if isinstance(item, socket.gaierror): return "SQL_DNS_FAILED"
        if isinstance(item, ssl.SSLError): return "SQL_TLS_FAILED"
        if isinstance(item, ConnectionRefusedError): return "SQL_CONNECTION_REFUSED"
        if isinstance(item, ConnectionResetError): return "SQL_CONNECTION_RESET"
        if isinstance(item, (sa.NoSuchModuleError, ModuleNotFoundError)): return "SQL_DRIVER_UNAVAILABLE"
        if isinstance(item, sa.ArgumentError): return "SQL_CONFIGURATION_INVALID"
    # libpq often exposes connect failures without SQLSTATE or typed causes.
    # Inspect only DBAPI text privately for known transport signatures, never emit it.
    for item in chain:
        if not isinstance(item, psycopg.OperationalError): continue
        message = str(item).lower()
        signatures = (
            ("SQL_TLS_FAILED", ("certificate verify failed", "ssl error", "tls handshake", "server does not support ssl")),
            ("SQL_DNS_FAILED", ("could not translate host name", "name or service not known", "failed to resolve host", "nodename nor servname")),
            ("SQL_CONNECTION_TIMEOUT", ("timeout expired", "connection timed out")),
            ("SQL_CONNECTION_REFUSED", ("connection refused",)),
            ("SQL_NETWORK_UNREACHABLE", ("network is unreachable", "no route to host")),
            ("SQL_AUTHENTICATION_FAILED", ("password authentication failed", "no pg_hba.conf entry")),
        )
        for code, fragments in signatures:
            if any(fragment in message for fragment in fragments): return code
    if isinstance(error, AttributeError): return "PYTHON_ATTRIBUTE_ERROR"
    if isinstance(error, TypeError): return "PYTHON_TYPE_ERROR"
    if isinstance(error, ValueError): return "PYTHON_VALUE_ERROR"
    if stage == "SQL_CONNECTION": return "SQL_CONNECTION_FAILED"
    return {"LEDGER_CLAIM": "LEDGER_CLAIM_FAILED", "LEDGER_FINISH": "LEDGER_FINISH_FAILED",
            "SCHEDULER_AUDIT": "SCHEDULER_AUDIT_FAILED", "SCHEMA_CHECK": "SCHEMA_CHECK_FAILED",
            "R2_VERIFICATION": "R2_VERIFICATION_FAILED", "SQL_QUERY": "SQL_QUERY_FAILED",
            "RETENTION_LOCK": "RETENTION_LOCK_FAILED"}.get(stage, "RETENTION_FAILED")
