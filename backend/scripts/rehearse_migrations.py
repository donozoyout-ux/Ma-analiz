"""Disposable local PostgreSQL rehearsal. Never accepts a database URL or loads .env."""
import json
import subprocess
import sys
import time
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def docker(*args):
    return subprocess.check_output(['docker', *args], stderr=subprocess.PIPE, text=True).strip()


def main():
    container_name = 'mac-migration-rehearsal-' + uuid.uuid4().hex[:10]
    created = False
    try:
        docker('run', '--detach', '--name', container_name, '--label', 'mac-analiz.rehearsal=true',
               '--tmpfs', '/var/lib/postgresql/data', '-e', 'POSTGRES_HOST_AUTH_METHOD=trust',
               '-e', 'POSTGRES_DB=migration_rehearsal', '-p', '127.0.0.1::5432', 'postgres:17')
        created = True
        port = docker('port', container_name, '5432/tcp').split(':')[-1]
        assert port.isdigit()
        from sqlalchemy import create_engine, inspect, text
        from app.core import config as app_config
        from alembic.config import Config
        from alembic.script import ScriptDirectory
        from alembic import command
        from alembic.autogenerate import compare_metadata
        from alembic.runtime.migration import MigrationContext
        from app.db.base import Base
        from app import models
        url = f'postgresql+psycopg://postgres@127.0.0.1:{port}/migration_rehearsal'
        # Override the factory before Alembic env.py imports it. No production config is read.
        app_config.get_settings = lambda: app_config.Settings(_env_file=None, database_url=url)
        engine = create_engine(url)
        for attempt in range(60):
            try:
                with engine.connect() as conn:
                    conn.execute(text('SELECT 1'))
                break
            except Exception:
                if attempt == 59: raise
                time.sleep(1)
        cfg = Config(str(Path(__file__).resolve().parents[1] / 'alembic.ini'))
        cfg.set_main_option('script_location', str(Path(__file__).resolve().parents[1] / 'migrations'))
        revisions = list(reversed(list(ScriptDirectory.from_config(cfg).walk_revisions())))
        with engine.connect() as conn:
            assert inspect(conn).get_table_names() == []
        applied = []
        for revision in revisions:
            command.upgrade(cfg, revision.revision)
            with engine.connect() as conn:
                assert conn.execute(text('SELECT version_num FROM alembic_version')).scalar_one() == revision.revision
            applied.append(revision.revision)
        with engine.connect() as conn:
            inspector = inspect(conn)
            assert set(inspector.get_table_names()) == set(Base.metadata.tables) | {'alembic_version'}
            for name, table in Base.metadata.tables.items():
                actual = {c['name']: c for c in inspector.get_columns(name)}
                assert set(actual) == {c.name for c in table.columns}, name
                for column in table.columns:
                    assert actual[column.name]['nullable'] == column.nullable, (name, column.name)
                assert set(inspector.get_pk_constraint(name)['constrained_columns']) == {c.name for c in table.primary_key}
            drift = compare_metadata(MigrationContext.configure(conn), Base.metadata)
            assert not drift, drift
            version = conn.execute(text('SHOW server_version')).scalar_one()
        command.upgrade(cfg, 'head')  # rerun must be a no-op
        engine.dispose()
        print(json.dumps({'status':'PASS','postgres_version':version,'applied':applied,
                          'application_tables':len(Base.metadata.tables),'columns_nullability_primary_keys':'PASS',
                          'model_schema_diff':'NONE','head_rerun':'PASS','production_access':'NOT_RUN'}))
    finally:
        if created:
            docker('rm', '--force', container_name)


if __name__ == '__main__':
    main()
