import json
from unittest.mock import MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from app.api import connections as module
from app.core.config import Settings

TOKEN = 'test-only-access-token-with-at-least-32-characters'

@pytest.fixture
def settings():
    return Settings(_env_file=None, system_diagnostics_token=TOKEN,
        database_url='postgresql://user:private-password@private-host/db',
        r2_endpoint='https://private-account.r2.cloudflarestorage.com',
        r2_access_key_id='private-access-key', r2_secret_access_key='private-secret', r2_bucket='private-bucket')

@pytest.fixture
def client(monkeypatch, settings):
    app = FastAPI()
    app.include_router(module.router)
    monkeypatch.setattr(module, 'get_settings', lambda: settings)
    monkeypatch.setattr(module, '_last_check', 0)
    return TestClient(app)

@pytest.mark.parametrize('header', [None, 'Bearer wrong', 'Basic anything'])
def test_unauthorized_never_checks(client, monkeypatch, header):
    def forbidden(*args):
        pytest.fail('Unauthorized network access')
    monkeypatch.setattr(module, 'postgres_check', forbidden)
    monkeypatch.setattr(module, 'r2_check', forbidden)
    assert client.post('/system/connections', headers={'Authorization': header} if header else {}).status_code == 401

@pytest.mark.parametrize('token', [None, 'short'])
def test_auth_fails_closed(client, settings, token):
    from pydantic import SecretStr
    settings.system_diagnostics_token = SecretStr(token) if token else None
    assert client.post('/system/connections', headers={'Authorization': f'Bearer {TOKEN}'}).status_code == 503


def test_endpoint_rate_limit_and_no_cache(client, monkeypatch):
    pg = MagicMock(return_value={'status': 'CONNECTED'})
    r2 = MagicMock(return_value={'status': 'ACCESSIBLE'})
    monkeypatch.setattr(module, 'postgres_check', pg)
    monkeypatch.setattr(module, 'r2_check', r2)
    headers={'Authorization': f'Bearer {TOKEN}'}
    response=client.post('/system/connections', headers=headers)
    assert response.status_code == 200
    assert response.headers['cache-control'] == 'no-store'
    assert response.json()['read_only'] is True
    assert client.post('/system/connections', headers=headers).status_code == 429
    pg.assert_called_once()
    r2.assert_called_once()


def test_missing_config_no_clients(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail('Missing configuration must not connect')
    monkeypatch.setattr(module, 'create_engine', forbidden)
    monkeypatch.setattr(module.boto3, 'client', forbidden)
    settings=Settings(_env_file=None, database_url=None, r2_endpoint=None,
                      r2_access_key_id=None, r2_secret_access_key=None, r2_bucket=None)
    assert module.postgres_check(settings)['missing_env'] == ['DATABASE_URL']
    assert set(module.r2_check(settings)['missing_env']) == {'R2_ENDPOINT','R2_ACCESS_KEY_ID','R2_SECRET_ACCESS_KEY','R2_BUCKET'}


def test_r2_only_head_and_redacted_error(monkeypatch, settings):
    client=MagicMock()
    client.__enter__.return_value=client
    factory=MagicMock(return_value=client)
    monkeypatch.setattr(module.boto3, 'client', factory)
    assert module.r2_check(settings)['status'] == 'ACCESSIBLE'
    client.head_bucket.assert_called_once_with(Bucket=settings.r2_bucket)
    assert [call[0] for call in client.method_calls] == ['head_bucket']
    client.head_bucket.side_effect=RuntimeError('private-password private-host private-secret')
    result=module.r2_check(settings)
    assert result['status'] == 'FAILED'
    assert 'private-' not in json.dumps(result)


def test_postgres_read_only_and_schema(monkeypatch, settings):
    engine=MagicMock()
    connection=engine.connect.return_value.__enter__.return_value
    monkeypatch.setattr(module, 'create_engine', MagicMock(return_value=engine))
    inspector=MagicMock()
    inspector.get_table_names.return_value=list(module.Base.metadata.tables)
    inspector.get_columns.side_effect=lambda name: [{'name': c.name} for c in module.Base.metadata.tables[name].columns]
    monkeypatch.setattr(module, 'inspect', lambda conn: inspector)
    result=module.postgres_check(settings)
    assert result['schema'] == 'READY'
    queries=[str(call.args[0]) for call in connection.execute.call_args_list]
    assert queries == ['SET TRANSACTION READ ONLY', "SET LOCAL statement_timeout = '5s'", 'SELECT 1']
    engine.dispose.assert_called_once()
    inspector.get_table_names.return_value=[]
    assert module.postgres_check(settings)['schema'] == 'INCOMPLETE'
    inspector.get_table_names.return_value=list(module.Base.metadata.tables)
    inspector.get_columns.side_effect=lambda name: []
    assert module.postgres_check(settings)['missing_columns']


def test_postgres_failure_redacted(monkeypatch, settings):
    monkeypatch.setattr(module, 'create_engine', MagicMock(side_effect=RuntimeError(settings.database_url)))
    result=module.postgres_check(settings)
    assert result['status'] == 'FAILED'
    assert 'private-' not in json.dumps(result)
