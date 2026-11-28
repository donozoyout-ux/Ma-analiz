"""Explicit isolated two-cluster recovery rehearsal. Never accepts a DB URL argument.

Source: ledger_test@127.0.0.1:55471/ledger71.
Target: restore_admin@127.0.0.1:55472, a fresh isolated cluster.
Requires matching PostgreSQL client binaries on PATH. No .env/settings imports.
"""
import hashlib
import os
from pathlib import Path
import subprocess
import tempfile
import re

import psycopg
from psycopg import sql


def run():
    if os.environ.get("LEDGER_TEST_DATABASE_URL") != "postgresql+psycopg://ledger_test@127.0.0.1:55471/ledger71":
        raise RuntimeError("ISOLATED_LEDGER_DATABASE_REQUIRED")
    if (Path(__file__).resolve().parents[2] / ".env").exists():
        raise RuntimeError("ISOLATED_COPY_WITHOUT_ENV_REQUIRED")
    env = {k:v for k,v in os.environ.items() if k.upper() in {"PATH","SYSTEMROOT","WINDIR","TEMP","TMP","COMSPEC","PATHEXT"}}
    source = dict(host="127.0.0.1",port=55471,user="ledger_test",dbname="ledger71")
    target = dict(host="127.0.0.1",port=55472,user="restore_admin",dbname="ledger71_restore")
    image=os.environ.get("LEDGER_TEST_CLIENT_IMAGE")
    if image not in (None,"postgres:16"):
        raise RuntimeError("UNAPPROVED_ISOLATED_CLIENT_IMAGE")
    workspace=None
    def client_args(args):
        if not image:
            return args
        # CI uses its already-pulled server image, avoiding runner client drift.
        prefix=["docker","run","--rm","--pull=never","--network=host"]
        if workspace:
            prefix += ["--mount",f"type=bind,src={workspace},dst={workspace}"]
        return [*prefix,image,*args]
    def command(*args):
        subprocess.run(client_args(args),env=env,check=True,capture_output=True)
    with psycopg.connect(**source) as conn:
        major=conn.info.server_version//10000
    with psycopg.connect(**dict(target,dbname="postgres")) as conn:
        assert conn.info.server_version//10000==major, "RESTORE_SERVER_MAJOR_MISMATCH"
    for binary in ("pg_dump","pg_dumpall","pg_restore","psql","createdb"):
        version=subprocess.check_output(client_args([binary,"--version"]),env=env,text=True)
        assert int(re.search(r"(\d+)\.",version)[1])==major, "MATCHING_POSTGRES_CLIENT_REQUIRED"
    tables = ("prediction_publications","prediction_settlements","prediction_evidence")
    def snapshot(config):
        with psycopg.connect(**config) as conn:
            conn.execute("SET TIME ZONE 'UTC'")
            result = []
            for table in tables:
                rows=conn.execute(sql.SQL("SELECT row_to_json(t)::text FROM {} t ORDER BY id").format(sql.Identifier(table))).fetchall()
                result.append((len(rows),hashlib.sha256("\n".join(row[0] for row in rows).encode()).hexdigest()))
            functions=conn.execute("SELECT p.proname,p.prosecdef,p.proconfig,r.rolname,p.proacl::text FROM pg_proc p JOIN pg_roles r ON r.oid=p.proowner WHERE p.proname LIKE 'prediction_%' ORDER BY p.proname").fetchall()
            triggers=conn.execute("SELECT c.relname,t.tgname,t.tgenabled FROM pg_trigger t JOIN pg_class c ON c.oid=t.tgrelid WHERE c.relname LIKE 'prediction_%' AND NOT t.tgisinternal ORDER BY 1,2").fetchall()
            return result,functions,triggers
    before=snapshot(source)
    assert all(count>0 for count,_ in before[0]), "run synthetic ledger tests first"
    with tempfile.TemporaryDirectory(prefix="ledger71-synthetic-backup-") as directory:
        workspace=directory
        dump=str(Path(directory)/"ledger.dump"); roles=str(Path(directory)/"roles.sql")
        command("pg_dump","-h","127.0.0.1","-p","55471","-U","ledger_test","-Fc","-f",dump,"ledger71")
        command("pg_dumpall","-h","127.0.0.1","-p","55471","-U","ledger_test","--roles-only","--no-role-passwords","-f",roles)
        command("psql","-h","127.0.0.1","-p","55472","-U","restore_admin","-d","postgres","-v","ON_ERROR_STOP=1","-f",roles)
        command("createdb","-h","127.0.0.1","-p","55472","-U","restore_admin","-O","ledger_test","ledger71_restore")
        command("pg_restore","-h","127.0.0.1","-p","55472","-U","restore_admin","-d","ledger71_restore","--exit-on-error",dump)
    assert snapshot(target)==before, "RESTORE_DATA_OR_GUARDS_MISMATCH"
    with psycopg.connect(**target) as conn:
        assert conn.execute("SELECT version_num FROM alembic_version").fetchone()[0]=="0012_prediction_evidence"
        for role in ("ma_ledger_reader","ma_ledger_writer","ma_ledger_verifier"):
            flags=conn.execute("SELECT rolsuper,rolcreatedb,rolcreaterole,rolcanlogin,rolreplication,rolbypassrls FROM pg_roles WHERE rolname=%s",(role,)).fetchone()
            assert flags==(False,)*6
        for role,table in (("ma_ledger_writer","prediction_publications"),("ma_ledger_verifier","prediction_evidence"),("ledger_test","prediction_settlements")):
            try:
                with conn.transaction():
                    conn.execute(sql.SQL("SET LOCAL ROLE {}").format(sql.Identifier(role)))
                    conn.execute(sql.SQL("DELETE FROM {}").format(sql.Identifier(table)))
            except psycopg.Error:
                pass
            else:
                raise AssertionError("RESTORED_APPEND_ONLY_GUARD_MISSING")
        # A real non-owner login, not just SET ROLE under the test superuser.
        conn.execute("CREATE ROLE ledger71_reader_login LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOBYPASSRLS IN ROLE ma_ledger_reader")
    with psycopg.connect(**dict(target,user="ledger71_reader_login")) as conn:
        assert conn.execute("SELECT count(*) FROM prediction_publications").fetchone()[0]>0
        try:
            conn.execute("INSERT INTO prediction_evidence DEFAULT VALUES")
        except psycopg.errors.InsufficientPrivilege:
            pass
        else:
            raise AssertionError("RESTORED_LOGIN_CAN_WRITE")
    print("ISOLATED_RESTORE_VERIFIED: row hashes, history, roles, trigger owners/search_path, ACLs and restricted login")


if __name__=="__main__":
    run()
