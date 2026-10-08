"""Scoped HTTP health commands; real isolated PostgreSQL, fixture-only cache."""
from copy import deepcopy
from datetime import datetime,timezone
import json,subprocess,sys
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from apps.api.main import create_app
from apps.api.settings import Settings
from core.paper import requested_health as health,requested_journal as journal,requested_preview as preview,requested_ownership as ownership,shared_capital_pool as pools
from core.paper.fault_adapter import sha
from tests.test_integration import database
from tests.test_requested_health import ready,gates
from tests.test_requested_commands import configured,HEADERS
from tests.test_requested_preview import proposal
from tests.test_requested_execution import event
from tests.test_market_quality import evidence

ROOT='/api/v1/paper-requested/'
ENROLL=ROOT+'health-enrollment-commands'
READ=ROOT+'health-captures'
BODY={'account_id':'account','expected_control_revision':2,'expected_financial_revision':0}


def send(client,path,body):return client.post(path,json=body,headers=HEADERS)


def fresh(cache):
    value=evidence(datetime.now(timezone.utc));cache.raw=[json.dumps(value['ticker']),json.dumps(value['book'])]


def client_for(settings,cache,monkeypatch,actions,accounts=None):
    app=create_app(configured(settings,actions=actions,accounts=accounts))
    monkeypatch.setattr(app.state.cache,'mget',cache.mget)
    return TestClient(app)


def test_independent_grants_and_authentication_precede_storage(monkeypatch):
    settings=Settings(database_url='postgresql+psycopg://invalid:invalid@127.0.0.1:1/no_db')
    def forbidden(*args,**kwargs):raise AssertionError('Denied call reached health storage')
    monkeypatch.setattr(health,'enroll',forbidden);monkeypatch.setattr(health,'capture',forbidden)
    cases=[(ENROLL,BODY,'ENROLL_HEALTH'),(READ,{'account_id':'account'},'READ_HEALTH')]
    with TestClient(create_app(settings)) as client:
        for path,body,_ in cases:assert send(client,path,body).status_code==503
    for action in ['ENROLL_HEALTH','READ_HEALTH','PREPARE','ENROLL','INGEST_EVENT']:
        with TestClient(create_app(configured(settings,actions=[action]))) as client:
            for path,body,required in cases:
                assert client.post(path,json=body).status_code==401
                assert send(client,path,{**body,'account_id':'other'}).status_code==403
                if action!=required:assert send(client,path,body).status_code==403
            for update in [{'expected_financial_revision':True},{'expected_financial_revision':1},{'expected_control_revision':'2'},{'health':{}},{'cache':{}}]:
                assert send(client,ENROLL,{**BODY,**update}).status_code==422


def test_enrollment_read_and_missing_lock_errors(database):
    engine,_,settings=database;ready(engine,enroll=False)
    with TestClient(create_app(configured(settings,accounts=['account','missing'],actions=['ENROLL_HEALTH','READ_HEALTH']))) as client:
        before=send(client,READ,{'account_id':'account'});assert before.status_code==200
        assert before.json()['declared_version'] is None and before.json()['policy'] is None and before.json()['gates']==[]
        one=send(client,ENROLL,BODY);assert one.status_code==200 and one.headers['cache-control']=='no-store'
        assert one.json()['enrollment_committed'] and not one.json()['external_submission_allowed']
        assert send(client,ENROLL,BODY).json()==one.json()
        assert send(client,ENROLL,{**BODY,'expected_control_revision':3}).status_code==409
        report=send(client,READ,{'account_id':'account'});assert report.status_code==200
        assert report.headers['cache-control']=='no-store' and health.verify(report.json())==report.json()
        for path,body in [(ENROLL,BODY),(READ,{'account_id':'account'})]:
            assert send(client,path,{**body,'account_id':'missing'}).status_code==404
            with engine.begin() as conn:
                conn.execute(text("SELECT account_id FROM requested_paper_accounts WHERE account_id='account' FOR UPDATE"))
                result=send(client,path,body);assert result.status_code==503 and set(result.json())=={'detail'}
    assert journal.read(engine,'account')['revision']==0


@pytest.mark.parametrize('owned',[False,True])
def test_server_cache_prepare_submit_rollback_and_historical_retry(database,monkeypatch,tmp_path,owned):
    engine,_,settings=database;cache=ready(engine,enroll=False)
    actions=['ENROLL_HEALTH','READ_HEALTH','PREPARE','PREVIEW_EVENT','INGEST_EVENT','CLAIM_OWNERSHIP','DELIVER_OWNED_EVENT']
    with client_for(settings,cache,monkeypatch,actions) as client:
        enrollment=send(client,ENROLL,BODY);assert enrollment.status_code==200
        value=proposal();forecast=preview.capture(engine,value);body={**value,'preview_sha256':forecast['sha256']}
        cache.raw=[None,None];assert send(client,ROOT+'preparation-commands',body).status_code==409
        assert journal.read(engine,'account')['revision']==0 and not gates(engine)
        fresh(cache);one=send(client,ROOT+'preparation-commands',body);assert one.status_code==200,one.text
        req=one.json()['request'];cache.raw=[None,None]
        assert send(client,ROOT+'preparation-commands',body).json()==one.json()
        source={'account_id':'account','request_id':req['request_id'],'expected_financial_revision':1,'expected_control_revision':2,
                'source':{'kind':'LOCAL_PAPER_OPERATOR_INPUT','source_id':'fixture'},'event':event(req,0,'SUBMIT')}
        reviewed=send(client,ROOT+'event-previews',source);assert reviewed.status_code==200
        source['preview_sha256']=reviewed.json()['sha256']
        path=ROOT+'source-commands'
        if owned:
            ownership.claim(engine,'account',req['request_id'],'worker',60,expected_financial_revision=1,expected_control_revision=2,expected_token=0)
            source.update(owner='worker',ownership_token=1);path=ROOT+'owned-source-commands'
        assert send(client,path,source).status_code==409
        assert journal.read(engine,'account')['revision']==1 and len(gates(engine))==1
        fresh(cache);submitted=send(client,path,source);assert submitted.status_code==200,submitted.text
        cache.raw=[None,None];assert send(client,path,source).json()==submitted.json()
        assert send(client,ENROLL,BODY).json()==enrollment.json()
        report=send(client,READ,{'account_id':'account'}).json();assert len(report['gates'])==2
        assert health.verify(report)==report
    path=tmp_path/'export.json';path.write_text(json.dumps(report))
    result=subprocess.run([sys.executable,'-m','scripts.verify_requested_health',str(path)],capture_output=True,text=True,timeout=10)
    assert result.returncode==0,result.stderr
    for mutate in ['missing','duplicate','order','identity','declaration','control']:
        broken=deepcopy(report)
        if mutate=='missing':broken['gates'].pop()
        elif mutate=='duplicate':broken['gates'][1]=deepcopy(broken['gates'][0])
        elif mutate=='order':broken['gates'].reverse()
        elif mutate=='identity':broken['policy']['opening_sha256']='0'*64
        elif mutate=='declaration':broken['declared_version']=None
        else:broken['controls']['revision']+=1
        broken['sha256']=sha({k:v for k,v in broken.items() if k!='sha256'})
        with pytest.raises(ValueError):health.verify(broken)
    path.write_text('{"version":1,"version":2}')
    assert subprocess.run([sys.executable,'-m','scripts.verify_requested_health',str(path)],capture_output=True,timeout=10).returncode!=0


def test_enrollment_cannot_retrofit_existing_request(database):
    engine,_,settings=database;ready(engine,enroll=False);value=proposal();forecast=preview.capture(engine,value)
    journal.prepare(engine,forecast['request'])
    with TestClient(create_app(configured(settings,actions=['ENROLL_HEALTH']))) as client:
        assert send(client,ENROLL,BODY).status_code==409
    report=health.capture(engine,'account');assert report['policy'] is None and not report['gates']


def test_pool_server_cache_and_whole_member_scope(database,monkeypatch):
    from tests.test_shared_capital import pool
    from tests.test_shared_capital_api import configured as pool_settings
    engine,_,settings=database;cache=ready(engine,'a');ready(engine,'b')
    definition=pool()
    for allocation in definition['allocations']:allocation['instrument_id']='binance:spot:BTC-USDT'
    pools.create(engine,definition)
    value=proposal(account_id='a');forecast=preview.capture(engine,value)
    body={**value,'pool_id':'pool','local_preview_sha256':forecast['sha256']};path=ROOT+'pool-preparation-commands'
    app=create_app(pool_settings(settings,actions=['POOL_PREPARE'],accounts=['a']))
    def forbidden(*args,**kwargs):raise AssertionError('Denied pool scope accessed cache')
    monkeypatch.setattr(app.state.cache,'mget',forbidden)
    with TestClient(app) as client:assert send(client,path,body).status_code==403
    app=create_app(pool_settings(settings,actions=['POOL_PREPARE']));monkeypatch.setattr(app.state.cache,'mget',cache.mget)
    with TestClient(app) as client:
        cache.raw=[None,None];assert send(client,path,body).status_code==409
        assert journal.read(engine,'a')['revision']==0
        fresh(cache);one=send(client,path,body);assert one.status_code==200,one.text
        cache.raw=[None,None];assert send(client,path,body).json()==one.json()
    assert len(health.capture(engine,'a')['gates'])==1


def test_authorized_unavailable_storage_is_generic():
    settings=Settings(database_url='postgresql+psycopg://invalid:invalid@127.0.0.1:1/no_db')
    with TestClient(create_app(configured(settings,actions=['ENROLL_HEALTH','READ_HEALTH']))) as client:
        for path,body in [(ENROLL,BODY),(READ,{'account_id':'account'})]:
            result=send(client,path,body);assert result.status_code==503 and set(result.json())=={'detail'}
            assert result.headers['cache-control']=='no-store'


def test_export_bounds_refuse_without_changing_policy_or_finances(database,monkeypatch,tmp_path):
    engine,_,settings=database;ready(engine);before=journal.read(engine,'account')
    original=health.capture(engine,'account');monkeypatch.setattr(health,'MAX_STORED_BYTES',128)
    with TestClient(create_app(configured(settings,actions=['READ_HEALTH']))) as client:
        result=send(client,READ,{'account_id':'account'});assert result.status_code==409
    with pytest.raises(ValueError):health.verify(original)
    assert journal.read(engine,'account')==before
    # The CLI must bound the file read before JSON decoding, even for sparse files.
    path=tmp_path/'too-large.json'
    with path.open('wb') as file:file.truncate(32*1024*1024+1)
    result=subprocess.run([sys.executable,'-m','scripts.verify_requested_health',str(path)],capture_output=True,text=True,timeout=10)
    assert result.returncode!=0 and 'exceeds 32 MiB' in result.stderr


def test_complete_export_after_void_replays_v2_without_current_market(database):
    from core.paper import requested_finalization as finalization
    from tests.test_requested_execution import request,STAMP
    engine,_,_=database;cache=ready(engine);req=request()
    journal.prepare(engine,req,health_cache=cache)
    finalization.finalize(engine,'account',req['request_id'],'void',1,STAMP.isoformat())
    cache.raw=[None,None]
    report=health.capture(engine,'account');assert health.verify(report)==report
    assert len(report['gates'])==1 and journal.read(engine,'account')['revision']==2
