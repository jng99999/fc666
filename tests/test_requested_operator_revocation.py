import math
from concurrent.futures import ThreadPoolExecutor
from threading import Event
from typing import get_args
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from apps.api.main import create_app
from apps.api.settings import Settings
from apps.api.requested_operator_fence import ACTIONS,OperatorRouter
from core.paper import segmented_revocations as revocations,requested_controls as controls
from tests.test_integration import database
from tests.test_requested_commands import TOKEN,HEADERS,PATH,configured,payload
from tests.test_requested_controls import setup
from tests.test_requested_execution import STAMP


def sample(schema,definitions,key=''):
    if '$ref' in schema:return sample(definitions[schema['$ref'].split('/')[-1]],definitions,key)
    if 'anyOf' in schema:return sample(next(item for item in schema['anyOf'] if item.get('type')!='null'),definitions,key)
    if 'enum' in schema:return schema['enum'][0]
    if 'const' in schema:return schema['const']
    kind=schema.get('type')
    if kind=='object':
        result={name:sample(schema['properties'][name],definitions,name) for name in schema.get('required',[])}
        if not result and schema.get('minProperties',0):return {'fixture:spot':'1'}
        return result
    if kind=='array':
        if key=='account_ids':return ['account']
        return [sample(schema['items'],definitions,key)]*max(1,schema.get('minItems',0))
    if kind=='integer':return math.ceil(max(0,schema.get('minimum',0),schema.get('exclusiveMinimum',-1)+1))
    if kind=='number':return 1
    if kind=='boolean':return False
    if kind=='string':
        if key=='account_id':return 'account'
        if key=='pool_id':return 'pool'
        if 'pattern' in schema:return '0'*64
        if key.endswith('_at'):return STAMP.isoformat()
        return 'fixture' if key.endswith('_id') or key=='owner' else '1'
    return {}


def test_all_registered_operator_commands_deny_global_revocation_before_financial_access(database):
    engine,_,base=database
    actions=list(get_args(get_args(Settings.model_fields['paper_operator_actions'].annotation)[0]))
    settings=Settings(database_url=base.database_url,paper_operator_token=TOKEN,paper_operator_accounts=['account','segmented:account'],
                      paper_operator_actions=actions,paper_operator_pool_ids=['pool'])
    app=create_app(settings)
    revocations.revoke(engine,TOKEN,'withdraw-all')
    paths=[]
    with TestClient(app) as client:
        document=app.openapi()
        for path,methods in document['paths'].items():
            if not path.startswith('/api/v1/paper-requested/') or 'post' not in methods:continue
            schema=methods['post']['requestBody']['content']['application/json']['schema']
            value=sample(schema,document['components']['schemas'])
            response=client.post(path,json=value,headers=HEADERS)
            assert response.status_code==401,(path,response.text)
            assert response.headers['cache-control']=='no-store' and TOKEN not in response.text
            paths.append(path.rsplit('/',1)[-1])
        assert client.get('/api/v1/paper-segmented/evidence',params={'ledger_id':'segmented:account'},headers=HEADERS).status_code==401
    assert set(paths)==set(ACTIONS) and len(paths)==23
    with engine.connect() as db:
        assert db.scalar(text('SELECT count(*) FROM requested_paper_accounts'))==0
        assert db.scalar(text('SELECT count(*) FROM requested_paper_events'))==0


def test_legacy_original_retry_is_denied_after_recreation(database):
    engine,_,base=database;setup(engine);settings=configured(base)
    with TestClient(create_app(settings)) as client:
        assert client.post(PATH,json=payload(),headers=HEADERS).status_code==200
    before=controls.capture(engine,'account');revocations.revoke(engine,TOKEN,'withdraw-all')
    with TestClient(create_app(settings)) as client:
        assert client.post(PATH,json=payload(),headers=HEADERS).status_code==401
    assert controls.capture(engine,'account')==before


def test_revocation_waits_for_admitted_legacy_financial_transaction(database,monkeypatch):
    engine,_,base=database;setup(engine);admitted=Event();release=Event();started=Event();finished=Event()
    original=controls.command
    def pause(*args,**kwargs):
        admitted.set();assert release.wait(5);return original(*args,**kwargs)
    monkeypatch.setattr(controls,'command',pause)
    def withdraw():
        started.set();result=revocations.revoke(engine,TOKEN,'withdraw-all');finished.set();return result
    with TestClient(create_app(configured(base))) as client,ThreadPoolExecutor(2) as pool:
        earlier=pool.submit(client.post,PATH,json=payload(),headers=HEADERS)
        try:
            assert admitted.wait(5);later=pool.submit(withdraw);assert started.wait(5)
            assert not finished.wait(.05);release.set()
            assert earlier.result(timeout=5).status_code==200;later.result(timeout=5)
        finally:release.set()
        before=controls.capture(engine,'account')
        assert client.post(PATH,json=payload(),headers=HEADERS).status_code==401
        assert controls.capture(engine,'account')==before


def test_unavailable_revocation_state_blocks_legacy_commands_without_secret_leak(database,monkeypatch):
    engine,_,base=database;setup(engine);before=controls.capture(engine,'account')
    def fail(*args):raise DBAPIError('sensitive',{},Exception(TOKEN))
    monkeypatch.setattr(revocations,'audit',fail)
    with TestClient(create_app(configured(base))) as client:
        response=client.post(PATH,json=payload(),headers=HEADERS)
        assert response.status_code==503 and TOKEN not in response.text and 'sensitive' not in response.text
    assert controls.capture(engine,'account')==before


def test_scoped_segmented_deny_does_not_grant_or_block_legacy_actions(database):
    engine,_,base=database;setup(engine)
    revocations.revoke(engine,TOKEN,'segmented-read-only',ledger_id='segmented:account',action='READ_SEGMENTED_EVIDENCE')
    with TestClient(create_app(configured(base))) as client:assert client.post(PATH,json=payload(),headers=HEADERS).status_code==200


def test_unknown_operator_route_cannot_silently_skip_fence():
    router=OperatorRouter(None,None)
    with pytest.raises(ValueError):router.add_api_route('/api/v1/paper-requested/future',lambda:None,methods=['POST'])
    with pytest.raises(ValueError):router.add_api_route('/api/v1/paper-requested/control-commands',lambda:None,methods=['GET'])
