"""Test-only PostgreSQL 16 recovery harness. No production entry point or SDK."""
from datetime import date, datetime
from decimal import Decimal
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
from uuid import UUID, uuid4

from sqlalchemy import MetaData, create_engine, inspect, select, text
from sqlalchemy.engine import make_url


class RecoveryFailure(RuntimeError):
    """Only fixed codes are exposed; subprocess stderr is never published."""


def target(env):
    if env.get("STAGE7H_RECOVERY_ENABLED") != "true":
        raise RecoveryFailure("RECOVERY_TEST_OPT_IN_REQUIRED")
    if env.get("DATABASE_URL") or any(value for key,value in env.items() if key.startswith("R2_")):
        raise RecoveryFailure("PRODUCTION_STORAGE_ENV_FORBIDDEN")
    if any(env.get(k) for k in ("PGHOST", "PGPORT", "PGSERVICE", "PGSERVICEFILE", "PGDATABASE", "PGUSER", "PGPASSWORD", "PGOPTIONS", "PGPASSFILE")):
        raise RecoveryFailure("LIBPQ_ENV_OVERRIDES_FORBIDDEN")
    if any(env.get(k) != "false" for k in ("AUTO_RETENTION_DELETE_ENABLED", "OPERATIONAL_DELETE_ENABLED")):
        raise RecoveryFailure("DELETE_GATES_MUST_BE_FALSE")
    try:
        url = make_url(env.get("TEST_POSTGRES_URL", ""))
        safe = (url.drivername in {"postgresql", "postgresql+psycopg"} and url.host in {"127.0.0.1", "localhost"}
                and url.database == "stage7h_ci" and url.username == "stage7h_test"
                and url.password == "stage7h_local_only" and not url.query and url.port is not None)
    except Exception:
        safe = False
    if not safe:
        raise RecoveryFailure("UNSAFE_RECOVERY_TEST_TARGET")
    container = env.get("STAGE7H_POSTGRES_CONTAINER", "")
    if not re.fullmatch(r"[0-9a-f]{12,64}", container):
        raise RecoveryFailure("ISOLATED_CONTAINER_ID_REQUIRED")
    return url.set(drivername="postgresql+psycopg"), container


def canonical(value):
    if isinstance(value, (UUID, Decimal)): return str(value)
    if isinstance(value, (datetime, date)): return value.isoformat()
    if isinstance(value, dict): return {k:canonical(v) for k,v in value.items()}
    if isinstance(value, (list, tuple)): return [canonical(v) for v in value]
    return value


def digest(value):
    return hashlib.sha256(json.dumps(canonical(value), sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def snapshot(engine):
    """All public tables/columns/rows, constraints and indexes, not sample counts."""
    metadata = MetaData();metadata.reflect(bind=engine, schema="public")
    data, columns = {}, {}
    with engine.connect() as conn:
        for full_name, table in sorted(metadata.tables.items()):
            rows = [canonical(dict(row)) for row in conn.execute(select(table)).mappings()]
            rows.sort(key=lambda row: json.dumps(row,sort_keys=True))
            data[table.name] = {"count":len(rows),"sha256":digest(rows)}
            columns[table.name] = [{"name":c["name"], "type":str(c["type"]), "nullable":c["nullable"], "default":c.get("default")}
                                   for c in inspect(engine).get_columns(table.name,schema="public")]
        constraints = [dict(row) for row in conn.execute(text("""
            SELECT r.relname AS table_name, c.conname, c.contype,
                   pg_get_constraintdef(c.oid) AS definition, c.convalidated, c.condeferrable, c.condeferred
            FROM pg_constraint c JOIN pg_class r ON r.oid=c.conrelid
            JOIN pg_namespace n ON n.oid=r.relnamespace
            WHERE n.nspname='public' ORDER BY r.relname,c.conname
        """)).mappings()]
        indexes = [dict(row) for row in conn.execute(text("""
            SELECT tablename,indexname,indexdef FROM pg_indexes WHERE schemaname='public'
            ORDER BY tablename,indexname
        """)).mappings()]
    return {"tables":data,"columns":columns,"columns_sha256":digest(columns),"constraints":constraints,"indexes_sha256":digest(indexes)}


class RecoveryHarness:
    def __init__(self, env, directory):
        self.url, self.container = target(env)
        self.directory = Path(directory)
        self.token = uuid4().hex[:16]
        self.databases = set()
        self.admin = None
        self.engines = []
        self.report = {"stage":"7H.2","status":"NOT_VERIFIED","evidence":"SYNTHETIC_POSTGRESQL_16_CONTAINER",
            "checks":{},"PRODUCTION_SQL_WRITE":"NOT_RUN","PRODUCTION_R2_WRITE":"NOT_RUN","LIVE_DELETE":"NOT_RUN",
            "AUTO_RETENTION_DELETE_ENABLED":False,"OPERATIONAL_DELETE_ENABLED":False}

    def command(self, args, *, payload=None):
        # A fresh minimal environment prevents libpq/Docker inheritance overrides.
        env = {k:v for k,v in os.environ.items() if k in {"PATH","HOME","SYSTEMROOT","WINDIR","TEMP","TMP","DOCKER_CONFIG"}}
        env["PGPASSWORD"] = "stage7h_local_only"
        try:
            return subprocess.run(["docker",*args],input=payload,capture_output=True,env=env,timeout=60,check=False)
        except Exception:
            raise RecoveryFailure("RECOVERY_CONTAINER_COMMAND_FAILED") from None

    def verify_container(self):
        result = self.command(["inspect",self.container])
        try:
            item = json.loads(result.stdout)[0]
            expected = {"POSTGRES_USER=stage7h_test", "POSTGRES_PASSWORD=stage7h_local_only", "POSTGRES_DB=stage7h_ci"}
            bindings = item["NetworkSettings"]["Ports"]["5432/tcp"]
            safe = (result.returncode == 0 and item["State"]["Running"]
                    and item["Config"]["Image"] in {"postgres:16","docker.io/library/postgres:16"}
                    and expected.issubset(set(item["Config"]["Env"]))
                    and any(p["HostIp"] == "127.0.0.1" and int(p["HostPort"]) == self.url.port for p in bindings))
        except Exception:
            safe = False
        if not safe: raise RecoveryFailure("ISOLATED_CONTAINER_IDENTITY_NOT_VERIFIED")
        self.admin = create_engine(self.url,isolation_level="AUTOCOMMIT",hide_parameters=True,connect_args={"connect_timeout":5})
        with self.admin.connect() as conn:
            version = int(conn.scalar(text("SHOW server_version_num")))
            if not 160000 <= version < 170000: raise RecoveryFailure("POSTGRESQL_16_REQUIRED")
            cluster = conn.scalar(text("SELECT system_identifier::text FROM pg_control_system()"))
        proof = self.command(["exec","--env","PGPASSWORD",self.container,"psql","--host=127.0.0.1",
            "--username=stage7h_test","--no-password","--dbname=stage7h_ci","--tuples-only","--no-align",
            "--command=SELECT system_identifier::text FROM pg_control_system()"])
        if proof.returncode or proof.stdout.decode().strip() != cluster:
            raise RecoveryFailure("CONTAINER_DATABASE_CLUSTER_MISMATCH")
        for tool in ("pg_dump", "pg_restore"):
            result = self.command(["exec",self.container,tool,"--version"])
            if result.returncode or not re.search(rb"PostgreSQL\) 16\.",result.stdout):
                raise RecoveryFailure("POSTGRESQL_16_TOOLS_REQUIRED")
        self.report["server_major"] = 16

    def database(self, suffix):
        if not re.fullmatch(r"[a-z_]{1,20}",suffix): raise RecoveryFailure("INVALID_TEST_DATABASE_NAME")
        name = f"stage7h2_{self.token}_{suffix}"
        with self.admin.connect() as conn:
            conn.execute(text(f'CREATE DATABASE "{name}" TEMPLATE template0'))
        self.databases.add(name)
        engine = create_engine(self.url.set(database=name),hide_parameters=True,connect_args={"connect_timeout":5})
        self.engines.append(engine)
        return name, engine

    def check_name(self, name):
        if name not in self.databases or not name.startswith(f"stage7h2_{self.token}_"):
            raise RecoveryFailure("UNOWNED_TEST_DATABASE")

    def empty(self, name, engine):
        self.check_name(name)
        with engine.connect() as conn:
            count = conn.scalar(text("""SELECT count(*) FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
                WHERE n.nspname NOT IN ('pg_catalog','information_schema') AND n.nspname NOT LIKE 'pg_toast%'
                AND c.relkind IN ('r','p','v','m','S','f')"""))
        if count: raise RecoveryFailure("RESTORE_TARGET_NOT_EMPTY")

    def dump(self, name, *, schema_only=False):
        self.check_name(name)
        args = ["exec","--env","PGPASSWORD",self.container,"pg_dump","--host=127.0.0.1","--username=stage7h_test",
                "--no-password","--format=custom","--no-owner","--no-privileges",f"--dbname={name}"]
        if schema_only: args.append("--schema-only")
        result = self.command(args)
        if result.returncode or not result.stdout.startswith(b"PGDMP"):
            raise RecoveryFailure("PG_DUMP_FAILED")
        return result.stdout

    def raw_restore(self, name, engine, *, payload=None, missing_file=False):
        self.empty(name,engine)
        args = ["exec","-i","--env","PGPASSWORD",self.container,"pg_restore","--host=127.0.0.1","--username=stage7h_test",
                "--no-password","--exit-on-error","--single-transaction","--no-owner","--no-privileges",f"--dbname={name}"]
        if missing_file: args.append(f"/tmp/stage7h2-{self.token}-missing.dump")
        return self.command(args,payload=payload)

    def restore(self, path, checksum, name, engine, expected):
        if not Path(path).is_file(): raise RecoveryFailure("BACKUP_MISSING")
        payload = Path(path).read_bytes()
        if hashlib.sha256(payload).hexdigest() != checksum: raise RecoveryFailure("BACKUP_CHECKSUM_MISMATCH")
        result = self.raw_restore(name,engine,payload=payload)
        if result.returncode: raise RecoveryFailure("PG_RESTORE_FAILED")
        actual=snapshot(engine)
        if actual != expected:
            self.report["last_restore_mismatch"]={
                "sections":[k for k in expected if expected[k]!=actual.get(k)],
                "tables":[k for k in expected["tables"] if expected["tables"][k]!=actual["tables"].get(k)],
                "columns":{k:{"expected":expected["columns"][k],"actual":actual["columns"].get(k)}
                           for k in expected["columns"] if expected["columns"][k]!=actual["columns"].get(k)},
                "constraints": {"expected":expected["constraints"],"actual":actual["constraints"]}
                    if expected["constraints"]!=actual["constraints"] else None}
            raise RecoveryFailure("RESTORE_CONTENT_MISMATCH")
        return {"status":"PASS","sha256":checksum,"bytes":len(payload)}

    def close(self):
        for engine in self.engines: engine.dispose()
        if self.admin:
            with self.admin.connect() as conn:
                for name in sorted(self.databases):
                    self.check_name(name)
                    conn.execute(text(f'DROP DATABASE "{name}"'))
            self.admin.dispose()
