"""Atomic local reservation with fixture-only operator/account inputs."""
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from copy import deepcopy
import json,subprocess,sys,signal
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import event as sql_event,text
from sqlalchemy.exc import SQLAlchemyError
from apps.api.main import create_app
from apps.api.settings import Settings
from core.paper import requested_preparation as preparation,requested_preview as preview,requested_journal as journal,requested_controls as controls,requested_finalization as finalization
from tests.test_integration import database
from tests.test_requested_controls import setup,command,LIMITS
from tests.test_requested_commands import configured,HEADERS
from tests.test_requested_preview import proposal
from tests.test_requested_execution import BASE,request,event,fill,cumulative
from tests.test_requested_finalization import time

PATH='/api/v1/paper-requested/preparation-commands'


def reviewed(engine,**updates):
    value=proposal(**updates);report=preview.capture(engine,value);return {**value,'preview_sha256':report['sha256']},report


def test_authentication_and_prepare_grant_precede_storage(monkeypatch):
    settings=Settings(database_url='postgresql+psycopg://invalid:invalid@127.0.0.1:1/no_db')
    def forbidden(*args,**kwargs):raise AssertionError('Denied request reached storage')
    monkeypatch.setattr(preparation,'prepare',forbidden)
    body={**proposal(),'preview_sha256':'0'*64}
    with TestClient(create_app(settings)) as client:assert client.post(PATH,json=body).status_code==503
    with TestClient(create_app(configured(settings,actions=['PREVIEW_PREPARE']))) as client:
        assert client.post(PATH,json=body,headers=HEADERS).status_code==403
        assert client.post(PATH,json=body).status_code==401
    with TestClient(create_app(configured(settings,actions=['PREPARE']))) as client:
        assert client.post(PATH,json={**body,'account_id':'other'},headers=HEADERS).status_code==403
        for changed in [{'preview_sha256':'wrong'},{'expected_control_revision':True},{'expected_financial_revision':'0'},{'base':BASE}]:
            assert client.post(PATH,json={**body,**changed},headers=HEADERS).status_code==422


def test_atomic_preparation_and_retry_return_initial_acknowledgement(database):
    engine,_,settings=database;setup(engine);body,report=reviewed(engine)
    with TestClient(create_app(configured(settings,actions=['PREPARE']))) as client:
        one=client.post(PATH,json=body,headers=HEADERS)
        assert one.status_code==200 and one.headers['cache-control']=='no-store'
        assert one.json()['request']==report['request'] and one.json()['initial_funding']==report['projected_summary']['funding']
        assert one.json()['preparation_committed'] and not one.json()['external_submission_allowed']
        assert client.post(PATH,json=body,headers=HEADERS).json()==one.json()
        command(engine,'stop','STOP')
        assert client.post(PATH,json=body,headers=HEADERS).json()==one.json()
    view=journal.read(engine,'account')
    assert view['revision']==1 and view['total_events']==0 and view['account']==BASE
    assert controls.verify(controls.capture(engine,'account'))['gates']==[report['admission']]


def test_changed_proposal_digest_or_versions_have_no_side_effect(database):
    engine,_,settings=database;setup(engine);body,_=reviewed(engine);before=controls.capture(engine,'account')
    with TestClient(create_app(configured(settings,actions=['PREPARE']))) as client:
        for update in [{'preview_sha256':'0'*64},{'requested_quantity':'3'},{'expected_control_revision':3},{'expected_financial_revision':1}]:
            assert client.post(PATH,json={**body,**update},headers=HEADERS).status_code==409
        assert controls.capture(engine,'account')==before
        assert client.post(PATH,json=body,headers=HEADERS).status_code==200
        before=controls.capture(engine,'account')
        for update in [{'preview_sha256':'0'*64},{'requested_quantity':'3'},{'expected_control_revision':3},{'created_at':time(1)}]:
            assert client.post(PATH,json={**body,**update},headers=HEADERS).status_code==409
        assert controls.capture(engine,'account')==before


def test_stale_preview_after_control_or_financial_change_is_rejected(database):
    engine,_,settings=database;setup(engine);body,_=reviewed(engine)
    command(engine,'pause','PAUSE')
    with TestClient(create_app(configured(settings,actions=['PREPARE']))) as client:
        before=controls.capture(engine,'account')
        assert client.post(PATH,json=body,headers=HEADERS).status_code==409
        assert controls.capture(engine,'account')==before
        command(engine,'resume-again','RESUME')
        journal.prepare(engine,request(client_request_id='different'))
        before=controls.capture(engine,'account')
        assert client.post(PATH,json=body,headers=HEADERS).status_code==409
        assert controls.capture(engine,'account')==before


def test_retry_after_void_and_new_hold_does_not_reserve_or_release_twice(database):
    engine,_,settings=database;setup(engine);body,report=reviewed(engine)
    with TestClient(create_app(configured(settings,actions=['PREPARE']))) as client:
        one=client.post(PATH,json=body,headers=HEADERS).json()
        finalization.finalize(engine,'account',report['request']['request_id'],'void',1,time(1))
        newer=request(client_request_id='next',created_at=time(2));journal.prepare(engine,newer);before=controls.capture(engine,'account')
        retry=client.post(PATH,json=body,headers=HEADERS)
        assert retry.status_code==200 and retry.json()==one and controls.capture(engine,'account')==before
        assert before['journal']['journal']['active_request_id']==newer['request_id']


def test_retry_reconstructs_v2_prefix_after_earlier_void_and_later_fills(database):
    engine,_,settings=database;setup(engine);earlier=request();journal.prepare(engine,earlier)
    finalization.finalize(engine,'account',earlier['request_id'],'void',1,time(0))
    body,report=reviewed(engine,expected_financial_revision=2)
    with TestClient(create_app(configured(settings,actions=['PREPARE']))) as client:
        one=client.post(PATH,json=body,headers=HEADERS);assert one.status_code==200
        req=report['request'];journal.accept(engine,'account',req['request_id'],event(req,0,'SUBMIT'))
        journal.accept(engine,'account',req['request_id'],fill(req,1,size='4',price='90',fee='3.60'))
        journal.accept(engine,'account',req['request_id'],cumulative(req,2,'4','3.60','360',seal=True))
        before=controls.capture(engine,'account')
        assert client.post(PATH,json=body,headers=HEADERS).json()==one.json()
        assert controls.capture(engine,'account')==before


@pytest.mark.parametrize('same',[False,True])
def test_concurrent_requests_have_one_reservation(database,same):
    engine,_,settings=database;setup(engine);one,_=reviewed(engine);two=one if same else reviewed(engine,client_request_id='another')[0];barrier=Barrier(2)
    with TestClient(create_app(configured(settings,actions=['PREPARE']))) as client:
        def attempt(body):barrier.wait(timeout=5);return client.post(PATH,json=body,headers=HEADERS).status_code
        with ThreadPoolExecutor(2) as pool:results=list(pool.map(attempt,[one,two]))
    assert sorted(results)==([200,200] if same else [200,409])
    view=journal.read(engine,'account');assert view['revision']==1 and len(view['requests'])==1 and view['total_events']==0


def test_pause_vs_prepare_has_one_admission_winner(database):
    engine,_,settings=database;setup(engine);body,_=reviewed(engine);barrier=Barrier(2)
    with TestClient(create_app(configured(settings,actions=['PREPARE']))) as client:
        def prepare():barrier.wait(timeout=5);return client.post(PATH,json=body,headers=HEADERS).status_code
        def pause():barrier.wait(timeout=5);return command(engine,'pause','PAUSE')
        with ThreadPoolExecutor(2) as pool:
            one=pool.submit(prepare);two=pool.submit(pause);status=one.result();two.result()
    view=controls.capture(engine,'account');assert view['controls']['state']=='PAUSED'
    assert (status,view['journal']['journal']['revision']) in [(200,1),(409,0)]


def test_exception_after_request_insert_rolls_back_every_preparation_fact(database):
    engine,_,settings=database;setup(engine);body,_=reviewed(engine);before=controls.capture(engine,'account')
    def fail(conn,cursor,statement,parameters,context,executemany):
        if statement.startswith('INSERT INTO requested_paper_requests'):raise SQLAlchemyError('Injected post-insert fault')
    sql_event.listen(engine,'after_cursor_execute',fail)
    try:
        with pytest.raises(SQLAlchemyError):preparation.prepare(engine,{key:value for key,value in body.items() if key!='preview_sha256'},body['preview_sha256'])
    finally:sql_event.remove(engine,'after_cursor_execute',fail)
    assert controls.capture(engine,'account')==before
    with TestClient(create_app(configured(settings,actions=['PREPARE']))) as client:assert client.post(PATH,json=body,headers=HEADERS).status_code==200


@pytest.mark.parametrize('committed',[False,True])
def test_actual_sigkill_before_commit_or_after_lost_ack_recovers_once(database,committed):
    engine,_,settings=database;setup(engine);body,_=reviewed(engine);before=controls.capture(engine,'account')
    code='''
import json,os,signal,sys
from sqlalchemy import create_engine,event
from apps.api.settings import Settings
from core.paper import requested_preparation as preparation
value=json.load(sys.stdin);engine=create_engine(Settings().database_url.get_secret_value())
if not value['committed']:
 def kill(conn,cursor,statement,parameters,context,executemany):
  if statement.startswith('INSERT INTO requested_paper_requests'):os.kill(os.getpid(),signal.SIGKILL)
 event.listen(engine,'after_cursor_execute',kill)
preparation.prepare(engine,value['proposal'],value['digest'])
os.kill(os.getpid(),signal.SIGKILL)
'''
    original={key:value for key,value in body.items() if key!='preview_sha256'}
    result=subprocess.run([sys.executable,'-c',code],input=json.dumps({'proposal':original,'digest':body['preview_sha256'],'committed':committed}),text=True,capture_output=True,timeout=30)
    assert result.returncode==-signal.SIGKILL,result.stderr
    engine.dispose()
    if not committed:assert controls.capture(engine,'account')==before
    with TestClient(create_app(configured(settings,actions=['PREPARE']))) as client:
        assert client.post(PATH,json=body,headers=HEADERS).status_code==200
        assert client.post(PATH,json=body,headers=HEADERS).status_code==200
    assert journal.read(engine,'account')['revision']==1


def test_unmanaged_old_request_cannot_be_reinterpreted_as_controlled_preparation(database):
    engine,_,settings=database;journal.create(engine,'account','fixture:spot',BASE,time(0));req=request();journal.prepare(engine,req)
    finalization.finalize(engine,'account',req['request_id'],'void',1,time(0))
    controls.enroll(engine,'account',controls.policy('account',LIMITS),'enroll',time(0));command(engine,'resume','RESUME')
    with TestClient(create_app(configured(settings,actions=['PREPARE']))) as client:
        response=client.post(PATH,json={**proposal(client_request_id=req['client_request_id']),'preview_sha256':'0'*64},headers=HEADERS)
        assert response.status_code==409


def test_lock_timeout_missing_account_and_unavailable_storage_are_generic(database):
    engine,_,settings=database;setup(engine);body,_=reviewed(engine);before=controls.capture(engine,'account')
    with TestClient(create_app(configured(settings,accounts=['account','missing'],actions=['PREPARE']))) as client:
        assert client.post(PATH,json={**body,'account_id':'missing'},headers=HEADERS).status_code==404
        with engine.begin() as conn:
            conn.execute(text("SELECT account_id FROM requested_paper_accounts WHERE account_id='account' FOR UPDATE"))
            response=client.post(PATH,json=body,headers=HEADERS)
            assert response.status_code==503 and response.headers['cache-control']=='no-store'
    assert controls.capture(engine,'account')==before
    unavailable=Settings(database_url='postgresql+psycopg://invalid:invalid@127.0.0.1:1/no_db')
    with TestClient(create_app(configured(unavailable,actions=['PREPARE']))) as client:
        response=client.post(PATH,json=body,headers=HEADERS);assert response.status_code==503 and set(response.json())=={'detail'}


def test_halted_sell_reserves_inventory_with_no_cash_or_pnl_effect(database):
    engine,_,settings=database;base={**BASE,'quantity':'10','cost_basis':'500','fees':'3','realized_pnl':'7'};setup(engine,base=base);command(engine,'halt','HALT')
    body,report=reviewed(engine,side='SELL',expected_control_revision=3)
    with TestClient(create_app(configured(settings,actions=['PREPARE']))) as client:
        response=client.post(PATH,json=body,headers=HEADERS)
        assert response.status_code==200 and response.json()['initial_funding']['reserved_quantity']=='4'
        assert response.json()['initial_funding']['available_quantity']=='6'
    view=journal.read(engine,'account');assert view['account']==base and view['total_events']==0
    assert controls.capture(engine,'account')['controls']['state']=='HALTED'


def test_v1_traded_prefix_remains_reconstructible_after_later_v2_void(database):
    from decimal import Decimal
    engine,_,settings=database;setup(engine);earlier=request();journal.prepare(engine,earlier)
    for source in [event(earlier,0,'SUBMIT'),fill(earlier,1,size='4',price='90',fee='3.60'),cumulative(earlier,2,'4','3.60','360',seal=True)]:
        journal.accept(engine,'account',earlier['request_id'],source)
    body,report=reviewed(engine,expected_financial_revision=4,created_at=time(3))
    assert report['journal']['journal']['version']==journal.VERSION
    assert Decimal(report['request']['base']['cash'])==Decimal('636.4')
    with TestClient(create_app(configured(settings,actions=['PREPARE']))) as client:
        one=client.post(PATH,json=body,headers=HEADERS);assert one.status_code==200
        finalization.finalize(engine,'account',report['request']['request_id'],'void',5,time(4))
        latest=request(client_request_id='latest',base=report['request']['base'],created_at=time(5));journal.prepare(engine,latest)
        before=controls.capture(engine,'account');assert before['journal']['journal']['version']==journal.VERSION_V2
        assert client.post(PATH,json=body,headers=HEADERS).json()==one.json()
        assert controls.capture(engine,'account')==before
