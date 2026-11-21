"""Synthetic exceptions only; never real credentials or production connections."""
from contextlib import contextmanager
import json
import socket
import ssl

import psycopg
import pytest
from sqlalchemy import exc as sa

from app.jobs.retention_errors import error_code
from app.jobs import actions_retention as actions
from app.models import JobRun
from test_actions_retention import cfg, factory, result
from test_lifecycle import NOW, session

PRIVATE="postgresql://private_user:PRIVATE_PASSWORD@private-host/private-db TOKEN_PRIVATE"


@pytest.mark.parametrize("state,code",[
    ("28P01","SQL_AUTHENTICATION_FAILED"),("42501","SQL_PERMISSION_DENIED"),
    ("42P01","SQL_TABLE_MISSING"),("42703","SQL_COLUMN_MISSING"),
    ("3F000","SQL_SCHEMA_MISSING"),("3D000","SQL_DATABASE_NOT_FOUND"),
    ("23505","SQL_UNIQUE_CONSTRAINT"),("23502","SQL_NOT_NULL_CONSTRAINT"),
    ("23514","SQL_CHECK_CONSTRAINT"),("22001","SQL_VALUE_TOO_LONG"),
    ("55P03","SQL_LOCK_TIMEOUT"),("40P01","SQL_DEADLOCK"),
    ("40001","SQL_SERIALIZATION_FAILURE"),("53300","SQL_CONNECTION_LIMIT")])
def test_wrapped_sqlstate_never_exposes_private_details(state,code):
    original=psycopg.errors.lookup(state)(PRIVATE)
    wrapped=sa.DBAPIError("SELECT PRIVATE_SQL",{"password":PRIVATE},original)
    assert error_code(wrapped,"LEDGER_CLAIM")==code
    assert "PRIVATE" not in error_code(wrapped,"LEDGER_CLAIM")


@pytest.mark.parametrize("error,code",[
    (socket.gaierror(PRIVATE),"SQL_DNS_FAILED"),(ssl.SSLError(PRIVATE),"SQL_TLS_FAILED"),
    (TimeoutError(PRIVATE),"SQL_CONNECTION_TIMEOUT"),(sa.TimeoutError(PRIVATE),"SQL_POOL_TIMEOUT"),
    (ConnectionRefusedError(PRIVATE),"SQL_CONNECTION_REFUSED"),
    (psycopg.OperationalError("failed to resolve host "+PRIVATE),"SQL_DNS_FAILED"),
    (psycopg.OperationalError("SSL error: certificate verify failed "+PRIVATE),"SQL_TLS_FAILED"),
    (psycopg.OperationalError("connection timeout expired "+PRIVATE),"SQL_CONNECTION_TIMEOUT"),
    (psycopg.errors.QueryCanceled("canceling statement due to statement timeout "+PRIVATE),"SQL_STATEMENT_TIMEOUT"),
])
def test_transport_timeout_and_tls_classification(error,code):
    assert error_code(error,"SQL_CONNECTION")==code


def test_sqlstate_wins_over_misleading_message():
    assert error_code(psycopg.errors.UndefinedColumn("timeout expired "+PRIVATE),"LEDGER_CLAIM")=="SQL_COLUMN_MISSING"


def test_connect_failure_does_not_claim_ledger_or_run_retention():
    @contextmanager
    def failed():raise socket.gaierror(PRIVATE);yield
    def forbidden(**kw):pytest.fail("retention must not execute")
    report,code=actions.run_once("dry-run",cfg=cfg(),session_factory=failed,runner=forbidden)
    assert code==1 and report["failure_stage"]=="SQL_CONNECTION" and report["error_code"]=="SQL_DNS_FAILED"
    assert report["LIVE_DELETE"]=="NOT_RUN" and PRIVATE not in json.dumps(report)


def test_connected_but_ledger_column_missing_is_not_reported_as_connection_error(session,monkeypatch):
    execute=session.scalar
    def broken(statement,*args,**kw):
        if "job_runs" in str(statement):raise sa.ProgrammingError(None,None,psycopg.errors.UndefinedColumn(PRIVATE))
        return execute(statement,*args,**kw)
    monkeypatch.setattr(session,"scalar",broken)
    report,code=actions.run_once("dry-run",cfg=cfg(),session_factory=factory(session),runner=lambda **kw:pytest.fail("no retention"))
    assert code==1 and report["failure_stage"]=="LEDGER_CLAIM" and report["error_code"]=="SQL_COLUMN_MISSING"


def test_retention_error_is_classified_and_failure_is_audited(session):
    def failed(**kw):raise sa.OperationalError(None,None,psycopg.errors.InsufficientPrivilege(PRIVATE))
    report,code=actions.run_once("dry-run",now=NOW,cfg=cfg(),session_factory=factory(session),runner=failed)
    assert code==1 and report["failure_stage"]=="RETENTION" and report["error_code"]=="SQL_PERMISSION_DENIED"
    assert session.query(JobRun).one().status=="FAILED" and PRIVATE not in json.dumps(report)


def test_ledger_finish_failure_has_its_own_stage(session,monkeypatch):
    from app.jobs.ledger import SQLLedger
    monkeypatch.setattr(SQLLedger,"finish",lambda *a,**kw:(_ for _ in ()).throw(TimeoutError(PRIVATE)))
    report,code=actions.run_once("dry-run",now=NOW,cfg=cfg(),session_factory=factory(session),runner=lambda **kw:result())
    assert code==1 and report["failure_stage"]=="LEDGER_FINISH" and report["error_code"]=="SQL_CONNECTION_TIMEOUT"


def test_scheduler_audit_failure_does_not_masquerade_as_retention_failure(session,monkeypatch):
    get=session.get
    def failed(model,key,*a,**kw):
        if key=="scheduler:github-actions-retention":raise sa.ProgrammingError(None,None,psycopg.errors.InsufficientPrivilege(PRIVATE))
        return get(model,key,*a,**kw)
    monkeypatch.setattr(session,"get",failed)
    report,code=actions.run_once("dry-run",now=NOW,cfg=cfg(),session_factory=factory(session),runner=lambda **kw:result())
    assert code==1 and report["failure_stage"]=="SCHEDULER_AUDIT" and report["error_code"]=="SQL_PERMISSION_DENIED"


def test_core_schema_result_keeps_schema_stage(session):
    def incomplete(**kw):return {"status":"SCHEMA_NOT_READY","failure_stage":"SCHEMA_CHECK",
        "error_code":"PREPARED_STATISTICS_MIGRATION_NOT_APPLIED","LIVE_DELETE":"NOT_RUN","deleted":0}
    report,code=actions.run_once("dry-run",now=NOW,cfg=cfg(),session_factory=factory(session),runner=incomplete)
    assert code==1 and report["failure_stage"]=="SCHEMA_CHECK"


def test_audit_factory_bounds_timeout_preserves_tls_and_disposes(monkeypatch):
    import sqlalchemy
    import sqlalchemy.orm
    options={}
    class Engine:
        def dispose(self):options["disposed"]=True
    def engine(url,**kw):options.update(url=url,**kw);return Engine()
    @contextmanager
    def sql(*a,**kw):yield object()
    monkeypatch.setattr(sqlalchemy,"create_engine",engine)
    monkeypatch.setattr(sqlalchemy.orm,"Session",sql)
    settings=cfg().model_copy(update={"database_url":"postgresql://offline@localhost/offline?sslmode=verify-full"})
    with actions.audit_session(settings):pass
    assert options["connect_args"]=={"connect_timeout":10} and options["pool_timeout"]==10
    assert "sslmode=verify-full" in options["url"] and options["hide_parameters"] and options["disposed"]
