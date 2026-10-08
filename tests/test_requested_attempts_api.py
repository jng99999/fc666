from copy import deepcopy
import json,os,signal,subprocess,sys
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
import pytest
from fastapi.testclient import TestClient
from apps.api.main import create_app
from apps.api.settings import Settings
from core.paper import requested_attempts as attempts, requested_sources as sources
from core.paper.fault_adapter import sha
from tests.test_integration import database
from tests.test_requested_dispatch import submitted
from tests.test_requested_attempts import command
from tests.test_requested_commands import configured,HEADERS

WRITE='/api/v1/paper-requested/assessment-commands'
READ='/api/v1/paper-requested/assessment-exports'


def test_independent_grants_and_strict_input_precede_storage(monkeypatch):
    settings=Settings(database_url='postgresql+psycopg://invalid:invalid@127.0.0.1:1/no_db')
    cmd=dict(account_id='account',request_id='1'*64,client_id='2'*64,owner='first',ownership_token=1,attempt_id='one',failure='TIMEOUT',expected_financial_revision=2,expected_control_revision=2)
    read={k:cmd[k] for k in ('account_id','request_id')}
    def forbidden(*args,**kwargs):raise AssertionError('Denied request accessed storage')
    monkeypatch.setattr(attempts,'record',forbidden);monkeypatch.setattr(attempts,'export',forbidden)
    with TestClient(create_app(settings)) as client:
        for path,body in [(WRITE,cmd),(READ,read)]:assert client.post(path,json=body,headers=HEADERS).status_code==503
    for action,path,body in [('RECORD_ASSESSMENT',READ,read),('READ_ASSESSMENTS',WRITE,cmd),('QUERY_DISPATCH',READ,read),('DELIVER_OWNED_EVENT',WRITE,cmd)]:
        with TestClient(create_app(configured(settings,actions=[action]))) as client:
            assert client.post(path,json=body).status_code==401
            assert client.post(path,json=body,headers=HEADERS).status_code==403
    with TestClient(create_app(configured(settings,actions=['RECORD_ASSESSMENT','READ_ASSESSMENTS']))) as client:
        for path,body in [(WRITE,cmd),(READ,read)]:assert client.post(path,json={**body,'account_id':'other'},headers=HEADERS).status_code==403
        for change in [dict(ownership_token=True),dict(failure='NOT_FOUND'),dict(expected_financial_revision='2'),dict(extra=True)]:
            assert client.post(WRITE,json={**cmd,**change},headers=HEADERS).status_code==422


def test_command_read_export_and_tamper(database,tmp_path):
    engine,_,settings=database;req,_,_,_=submitted(engine);cmd=command(engine,req);read=dict(account_id='account',request_id=req['request_id']);before=sources.capture(engine,'account')
    with TestClient(create_app(configured(settings,actions=['RECORD_ASSESSMENT','READ_ASSESSMENTS']))) as client:
        empty=client.post(READ,json=read,headers=HEADERS);assert empty.status_code==200 and empty.json()['attempts']==[]
        one=client.post(WRITE,json=cmd,headers=HEADERS);assert one.status_code==200,one.text
        assert client.post(WRITE,json=cmd,headers=HEADERS).json()==one.json()
        response=client.post(READ,json=read,headers=HEADERS);assert response.status_code==200 and response.headers['cache-control']=='no-store'
        report=attempts.verify_export(response.json());assert report['attempts']==[one.json()]
        assert client.post(WRITE,json={**cmd,'failure':'EMPTY_RESULT'},headers=HEADERS).status_code==409
        assert client.post(READ,json={**read,'request_id':'0'*64},headers=HEADERS).status_code==409
        forged=deepcopy(report);forged['attempts'][0]['ordinal']=1;forged['sha256']=sha({k:v for k,v in forged.items() if k!='sha256'})
        with pytest.raises(ValueError):attempts.verify_export(forged)
    assert sources.capture(engine,'account')==before
    path=tmp_path/'export.json';path.write_text(json.dumps(report))
    result=subprocess.run([sys.executable,'-m','scripts.verify_requested_attempts_export',str(path)],capture_output=True,text=True,timeout=10)
    assert result.returncode==0,result.stderr


def test_sigkill_after_insert_rolls_back_and_retry_records_once(database):
    engine,_,_=database;req,_,_,_=submitted(engine);cmd=command(engine,req);before=sources.capture(engine,'account')
    code='''import json,os,signal
from sqlalchemy import create_engine
from core.paper import requested_attempts as a,requested_ownership as own
engine=create_engine(os.environ['FC666_TEST_ATTEMPT_DB'])
original=own._owned
calls=0
def die(*args):
    global calls
    original(*args);calls+=1
    if calls==2:os.kill(os.getpid(),signal.SIGKILL)
own._owned=die
a.record(engine,json.loads(os.environ['FC666_TEST_ATTEMPT_COMMAND']))
'''
    environment={**os.environ,'FC666_TEST_ATTEMPT_DB':engine.url.render_as_string(hide_password=False),'FC666_TEST_ATTEMPT_COMMAND':json.dumps(cmd)}
    child=subprocess.run([sys.executable,'-c',code],env=environment,capture_output=True,timeout=20)
    assert child.returncode==-signal.SIGKILL
    assert attempts.capture(engine,'account',req['request_id'])==[]
    one=attempts.record(engine,cmd);assert attempts.record(engine,cmd)==one
    assert len(attempts.capture(engine,'account',req['request_id']))==1
    assert sources.capture(engine,'account')==before


def test_simultaneous_exact_retries_have_one_record(database):
    engine,_,_=database;req,_,_,_=submitted(engine);cmd=command(engine,req);barrier=Barrier(2)
    def record():barrier.wait(timeout=5);return attempts.record(engine,cmd)
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures=[pool.submit(record) for _ in range(2)];results=[item.result(timeout=15) for item in futures]
    assert results[0]==results[1]
    assert len(attempts.capture(engine,'account',req['request_id']))==1


def test_account_lock_timeout_returns_no_partial_evidence(database):
    from sqlalchemy import text
    engine,_,settings=database;req,_,_,_=submitted(engine);cmd=command(engine,req)
    with TestClient(create_app(configured(settings,actions=['RECORD_ASSESSMENT','READ_ASSESSMENTS']))) as client:
        with engine.begin() as db:
            db.execute(text("SELECT account_id FROM requested_paper_accounts WHERE account_id='account' FOR UPDATE"))
            for path,body in [(WRITE,cmd),(READ,dict(account_id='account',request_id=req['request_id']))]:
                response=client.post(path,json=body,headers=HEADERS)
                assert response.status_code==503 and response.headers['cache-control']=='no-store'
                assert 'attempts' not in response.json() and 'TIMEOUT' not in response.text
    assert attempts.capture(engine,'account',req['request_id'])==[]
