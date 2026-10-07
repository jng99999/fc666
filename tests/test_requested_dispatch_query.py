from copy import deepcopy
import pytest
from fastapi.testclient import TestClient
from core.paper import requested_dispatch_query as query, requested_dispatch as dispatch, requested_ownership as own, requested_sources as sources
from core.paper.fault_adapter import sha
from tests.test_integration import database
from tests.test_requested_dispatch import submitted
from tests.test_requested_commands import configured, HEADERS
from apps.api.main import create_app

PATH='/api/v1/paper-requested/dispatch-queries'


def test_query_identity_takeover_and_read_only(database,monkeypatch):
    engine,_,settings=database
    req,lease,_,_=submitted(engine)
    client_id=dispatch.capture(engine,'account')['dispatches'][0]['dispatch']['client_id']
    body=dict(account_id='account',request_id=req['request_id'],client_id=client_id,owner='first',ownership_token=1)
    before=sources.capture(engine,'account')
    with TestClient(create_app(configured(settings,actions=['QUERY_DISPATCH']))) as client:
        response=client.post(PATH,json=body,headers=HEADERS)
        assert response.status_code==200,response.text
        assert response.headers['cache-control']=='no-store'
        report=query.verify(response.json())
        assert report['selected']['action']=='WAIT_LOCAL_RESULT'
        assert not report['resubmission_allowed']
        for update in [dict(client_id='0'*64),dict(owner='other'),dict(ownership_token=2),dict(request_id='0'*64)]:
            assert client.post(PATH,json={**body,**update},headers=HEADERS).status_code==409
        assert client.post(PATH,json={**body,'ownership_token':True},headers=HEADERS).status_code==422
        forged=deepcopy(report);forged['resubmission_allowed']=True;forged['sha256']=sha({k:v for k,v in forged.items() if k!='sha256'})
        with pytest.raises(ValueError):query.verify(forged)
        monkeypatch.setattr(own,'clock',lambda db:lease['expires_us'])
        assert client.post(PATH,json=body,headers=HEADERS).status_code==409
        own.claim(engine,'account',req['request_id'],'second',60)
        assert client.post(PATH,json=body,headers=HEADERS).status_code==409
        response=client.post(PATH,json={**body,'owner':'second','ownership_token':2},headers=HEADERS)
        assert response.status_code==200,response.text
        assert response.json()['client_id']==client_id
        assert query.verify(response.json())['selected']==report['selected']
    assert sources.capture(engine,'account')==before


def test_default_and_independent_query_scope_before_storage(monkeypatch):
    from apps.api.settings import Settings
    settings=Settings(database_url='postgresql+psycopg://invalid:invalid@127.0.0.1:1/no_db')
    body=dict(account_id='account',request_id='1'*64,client_id='2'*64,owner='first',ownership_token=1)
    def forbidden(*args,**kwargs):raise AssertionError('Denied query reached storage')
    monkeypatch.setattr(query,'capture',forbidden)
    with TestClient(create_app(settings)) as client:
        assert client.post(PATH,json=body,headers=HEADERS).status_code==503
    with TestClient(create_app(configured(settings,actions=['DELIVER_OWNED_EVENT']))) as client:
        assert client.post(PATH,json=body).status_code==401
        assert client.post(PATH,json=body,headers=HEADERS).status_code==403
    with TestClient(create_app(configured(settings,actions=['QUERY_DISPATCH']))) as client:
        assert client.post(PATH,json={**body,'account_id':'other'},headers=HEADERS).status_code==403


def test_unknown_outcome_expiry_during_query_and_offline_cli(database,monkeypatch,tmp_path):
    import json,subprocess,sys
    from tests.test_requested_sources import reviewed
    from tests.test_requested_ownership import deliver
    from tests.test_requested_execution import event
    engine,_,_=database;req,lease,_,_=submitted(engine)
    value,_=reviewed(engine,req,event(req,1,'UNKNOWN_SUBMISSION'));deliver(engine,value,'first',1)
    client_id=dispatch.capture(engine,'account')['dispatches'][0]['dispatch']['client_id']
    args=dict(account_id='account',request_id=req['request_id'],client_id=client_id,owner='first',ownership_token=1)
    report=query.capture(engine,**args)
    assert report['selected']['state']=='UNKNOWN_SUBMISSION'
    assert report['selected']['funding']['reserved_cash'].startswith('404.')
    path=tmp_path/'query.json';path.write_text(json.dumps(report))
    result=subprocess.run([sys.executable,'-m','scripts.verify_requested_dispatch_query',str(path)],capture_output=True,text=True,timeout=10)
    assert result.returncode==0,result.stderr
    path.write_text('{"version":"a","version":"b"}')
    assert subprocess.run([sys.executable,'-m','scripts.verify_requested_dispatch_query',str(path)],capture_output=True,timeout=10).returncode!=0
    before=sources.capture(engine,'account')
    original=dispatch.snapshot
    def expires(*values):
        result=original(*values)
        monkeypatch.setattr(own,'clock',lambda db:lease['expires_us'])
        return result
    monkeypatch.setattr(dispatch,'snapshot',expires)
    with pytest.raises(ValueError):query.capture(engine,**args)
    assert sources.capture(engine,'account')==before
