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
    factory=MagicMock(return_value=client)
    monkeypatch.setattr(module.boto3, 'client', factory)
    assert module.r2_check(settings)['status'] == 'ACCESSIBLE'
    client.head_bucket.assert_called_once_with(Bucket=settings.r2_bucket)
    assert [call[0] for call in client.method_calls] == ['head_bucket', 'close']
    client.head_bucket.side_effect=RuntimeError('private-password private-host private-secret')
    result=module.r2_check(settings)
    assert result['status'] == 'FAILED'
    assert 'private-' not in json.dumps(result)


def test_postgres_read_only_and_schema(monkeypatch, settings):
    monkeypatch.setattr(module, "database_inventory", lambda conn, cfg: {"status": "COMPLETE"})
    engine=MagicMock()
    connection=engine.connect.return_value.__enter__.return_value
    monkeypatch.setattr(module, 'create_engine', MagicMock(return_value=engine))
    inspector=MagicMock()
    inspector.default_schema_name="public"
    inspector.get_schema_names.return_value=["public"]
    monkeypatch.setattr(module.MigrationContext, "configure", lambda conn: MagicMock(get_current_heads=lambda: ("0010_active_statistics",)))
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

@pytest.mark.parametrize('code,status,category', [
    ('InvalidAccessKeyId',403,'AUTHENTICATION'),
    ('SignatureDoesNotMatch',403,'AUTHENTICATION'),
    ('AccessDenied',403,'AUTHORIZATION'),
    ('403',403,'AUTHENTICATION_OR_AUTHORIZATION'),
    ('NoSuchBucket',404,'ENDPOINT_OR_BUCKET'),
    ('RequestTimeout',408,'TIMEOUT'),
    ('private-secret',500,'SERVICE_ERROR'),
])
def test_safe_r2_categories(code,status,category):
    exc=module.aws_errors.ClientError({'Error':{'Code':code,'Message':'private-secret'},
        'ResponseMetadata':{'HTTPStatusCode':status}}, 'HeadBucket')
    assert module.r2_error_category(exc)==category

@pytest.mark.parametrize('error,category', [
    (module.aws_errors.ConnectTimeoutError(endpoint_url='private-host'), 'TIMEOUT'),
    (module.aws_errors.ReadTimeoutError(endpoint_url='private-host'), 'TIMEOUT'),
    (module.aws_errors.EndpointConnectionError(endpoint_url='private-host'), 'NETWORK'),
    (module.aws_errors.SSLError(endpoint_url='private-host',error='secret'), 'NETWORK'),
    (ValueError('private-host'), 'ENDPOINT_CONFIGURATION'),
])
def test_transport_error_categories(error,category):
    assert module.r2_error_category(error)==category


def test_schema_revision_and_other_schemas(monkeypatch):
    inspector=MagicMock(default_schema_name='public')
    inspector.get_schema_names.return_value=['public','legacy','pg_catalog','information_schema']
    inspector.get_table_names.side_effect=lambda schema=None: ['active_matches','alembic_version'] if schema=='legacy' else []
    monkeypatch.setattr(module,'inspect',lambda conn:inspector)
    context=MagicMock()
    monkeypatch.setattr(module.MigrationContext,'configure',lambda conn:context)
    for current, issue in [((), 'ALEMBIC_REVISION_MISSING'),
                            (('0001_initial',),'ALEMBIC_REVISION_BEHIND_OR_DIVERGED'),
                            (('private-secret',),'ALEMBIC_REVISION_UNKNOWN'),
                            (('0010_active_statistics',),'REVISION_SCHEMA_MISMATCH')]:
        context.get_current_heads.return_value=current
        result=module.schema_report(MagicMock())
        assert issue in result['schema_issues']
        assert result['other_schemas']=={'legacy':['active_matches']}
        assert result['alembic_version_other_schemas']==['legacy']
        assert 'private-secret' not in json.dumps(result)
        assert result['schema']=='INCOMPLETE'


def test_health_preserves_connection_when_job_tables_missing(monkeypatch):
    from app.api import routes
    settings=Settings(_env_file=None,database_url='postgresql://test:test@localhost/test',
                      r2_endpoint=None,r2_bucket=None,r2_access_key_id=None,r2_secret_access_key=None,
                      ai_provider=None)
    monkeypatch.setattr(routes,'get_settings',lambda:settings)
    engine=MagicMock()
    connection=engine.connect.return_value.__enter__.return_value
    def execute(statement):
        if 'job_runs' in str(statement): raise RuntimeError('missing table')
        return MagicMock()
    connection.execute.side_effect=execute
    monkeypatch.setattr(routes,'engine',engine)
    monkeypatch.setattr(routes,'schema_report',lambda conn:{'schema':'INCOMPLETE'})
    monkeypatch.setattr(routes,'provider_health',lambda:{'status':'PROVIDER_NOT_CONFIGURED'})
    result=routes.health()
    assert result['database']=='CONNECTED'
    assert result['database_schema']=='INCOMPLETE'
    assert result['status']=='degraded'

@pytest.mark.parametrize('url', ['postgresql://user:pass%25@localhost/db','postgresql+psycopg://user:pass%25@localhost/db'])
def test_alembic_env_normalizes_driver_without_connecting(monkeypatch,url):
    import runpy
    import sqlalchemy
    import logging.config
    from alembic import context
    from app.core import config as app_config
    cfg=module.Config()
    monkeypatch.setattr(context,'config',cfg,raising=False)
    monkeypatch.setattr(context,'is_offline_mode',lambda:False)
    monkeypatch.setattr(context,'configure',MagicMock())
    monkeypatch.setattr(context,'begin_transaction',MagicMock())
    monkeypatch.setattr(context,'run_migrations',MagicMock())
    monkeypatch.setattr(app_config,'get_settings',lambda:Settings(_env_file=None,database_url=url))
    factory=MagicMock(return_value=MagicMock())
    monkeypatch.setattr(sqlalchemy,'engine_from_config',factory)
    runpy.run_path(str(module.PROJECT_ROOT/'backend/migrations/env.py'))
    assert factory.call_args.args[0]['sqlalchemy.url']=='postgresql+psycopg://user:pass%25@localhost/db'

@pytest.mark.parametrize('count,expected', [(0,'NO_USER_RELATIONS'),(1,'NOT_EMPTY'),(501,'NOT_EMPTY')])
def test_inventory_conservative_empty_and_identity(settings,count,expected):
    connection=MagicMock()
    schema_result=MagicMock()
    schema_result.scalars.return_value=['public','legacy']
    relation_result=MagicMock()
    relation_result.mappings.return_value.all.return_value=[{'schema':'legacy','name':f'table_{i}','kind':'r','estimated_rows':0} for i in range(count)]
    identity_result=MagicMock()
    identity_result.mappings.return_value.one.return_value={'database_name':'db','database_oid':'123', 'server_version_num':'170000','is_replica':False}
    connection.execute.side_effect=[schema_result,relation_result,identity_result]*3
    result=module.database_inventory(connection,settings)
    assert result['emptiness']==expected
    assert result['status']==('TRUNCATED' if count>500 else 'COMPLETE')
    assert len(result['relations'])==min(count,500)
    assert result['target_confirmation']=='NOT_VERIFIED'
    assert result['configured_database_name_matches'] is True
    assert 'private-' not in json.dumps(result)
    settings.system_expected_database_fingerprint=result['target_fingerprint']
    assert module.database_inventory(connection,settings)['target_confirmation']=='MATCH'
    settings.system_expected_database_fingerprint='wrong'
    assert module.database_inventory(connection,settings)['target_confirmation']=='MISMATCH'
    assert all(str(call.args[0]).strip().startswith('SELECT') for call in connection.execute.call_args_list)


def test_unknown_r2_diagnostics_never_echo_error():
    error=module.aws_errors.ClientError({'Error':{'Code':'secret-endpoint','Message':'private-key'},'ResponseMetadata':{'HTTPStatusCode':502,'HTTPHeaders':{'secret':'key'}}},'HeadBucket')
    assert module.r2_error_details(error)=={'error_family':'SERVICE_RESPONSE','http_status':502,'service_code':'UNRECOGNIZED'}
    assert module.r2_error_category(error)=='SERVICE_ERROR'
    assert module.r2_error_details(RuntimeError('private-key'))['error_family']=='UNCLASSIFIED'
    assert module.r2_error_category(module.aws_errors.ParamValidationError(report='secret'))=='REQUEST_CONFIGURATION'


def test_alembic_absent_vs_empty(monkeypatch):
    inspector=MagicMock(default_schema_name='public')
    inspector.get_schema_names.return_value=['public']
    monkeypatch.setattr(module,'inspect',lambda conn:inspector)
    monkeypatch.setattr(module.MigrationContext,'configure',lambda conn:MagicMock(get_current_heads=lambda:()))
    inspector.get_table_names.return_value=[]
    assert module.schema_report(MagicMock())['alembic_version_state']=='ABSENT'
    inspector.get_table_names.return_value=['alembic_version']
    result=module.schema_report(MagicMock())
    assert result['alembic_version_exists'] is True
    assert result['alembic_version_state']=='EMPTY'


def test_inventory_failure_is_not_empty(monkeypatch,settings):
    engine=MagicMock()
    monkeypatch.setattr(module,'create_engine',lambda *a,**kw:engine)
    monkeypatch.setattr(module,'schema_report',lambda conn:{'schema':'INCOMPLETE'})
    def fail(*args): raise RuntimeError('private-endpoint')
    monkeypatch.setattr(module,'database_inventory',fail)
    result=module.postgres_check(settings)
    assert result['status']=='CONNECTED'
    assert result['inventory']['emptiness']=='NOT_VERIFIED'
    assert 'private-endpoint' not in json.dumps(result)


def test_real_boto_client_without_network(monkeypatch, settings):
    from botocore.stub import Stubber
    # Real SDK client, fake credentials; Stubber prevents every network request.
    client=module.boto3.client('s3',endpoint_url='https://example.invalid',
        aws_access_key_id='test',aws_secret_access_key='test',region_name='auto')
    assert not hasattr(client,'__enter__')
    with pytest.raises(TypeError):
        with client:
            pytest.fail('Boto clients must not be used as context managers')
    monkeypatch.setattr(module.boto3,'client',lambda *args,**kwargs:client)
    with Stubber(client) as stub:
        stub.add_response('head_bucket',{}, {'Bucket':settings.r2_bucket})
        assert module.r2_check(settings)['status']=='ACCESSIBLE'
        stub.assert_no_pending_responses()
    with Stubber(client) as stub:
        stub.add_client_error('head_bucket',service_error_code='AccessDenied',http_status_code=403,
                              expected_params={'Bucket':settings.r2_bucket})
        result=module.r2_check(settings)
        assert result['status']=='FAILED'
        assert result['error_category']=='AUTHORIZATION'
        stub.assert_no_pending_responses()


def test_public_health_omits_administrative_details(monkeypatch, settings):
    from app.api import routes
    app=FastAPI()
    app.include_router(routes.router)
    monkeypatch.setattr(routes,'get_settings',lambda:settings)
    monkeypatch.setattr(routes,'engine',None)
    monkeypatch.setattr(routes,'provider_health',lambda:{'status':'PROVIDER_NOT_CONFIGURED'})
    def forbidden(*args): pytest.fail('Public health must not run database inventory')
    monkeypatch.setattr(module,'database_inventory',forbidden)
    for status, expected in [('ACCESSIBLE','CONNECTED'),('FAILED','UNAVAILABLE'),('unexpected-secret','UNAVAILABLE')]:
        monkeypatch.setattr(routes,'r2_check',lambda cfg:{'status':status,'inventory':'private-secret',
            'error_category':'AUTHENTICATION','error_family':'TYPE_ERROR','target_fingerprint':'secret'})
        response=TestClient(app).get('/health')
        assert response.status_code==200
        assert response.json()['r2']==expected
        assert 'private-secret' not in response.text
        for key in ['inventory','current_revisions','expected_revisions','target_fingerprint','error_category','error_family']:
            assert key not in response.json()
