"""Independently granted local closure; fixture accounts/credentials only."""
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from apps.api.main import create_app
from apps.api.settings import Settings
from core.paper import requested_finalization as finalization,requested_journal as journal,requested_controls as controls
from tests.test_integration import database
from tests.test_requested_commands import configured,HEADERS,payload as control_payload
from tests.test_requested_controls import setup,command
from tests.test_requested_execution import BASE,STAMP,request,event
from tests.test_requested_finalization import time

PATH='/api/v1/paper-requested/finalization-commands'


def payload(req=None,**updates):
    return {'account_id':'account','request_id':(req or request())['request_id'],'command_id':'void',
            'expected_financial_revision':1,'created_at':time(1),**updates}


def test_authentication_and_separate_action_grant_precede_storage(monkeypatch):
    settings=Settings(database_url='postgresql+psycopg://invalid:invalid@127.0.0.1:1/no_db')
    def forbidden(*args,**kwargs):raise AssertionError('Denied request touched storage')
    monkeypatch.setattr(finalization,'finalize',forbidden)
    with TestClient(create_app(settings)) as client:assert client.post(PATH,json=payload()).status_code==503
    with TestClient(create_app(configured(settings))) as client:
        assert client.post(PATH,json=payload(),headers=HEADERS).status_code==403
        assert client.post(PATH,json=payload()).status_code==401
    with TestClient(create_app(configured(settings,actions=['VOID_UNSUBMITTED']))) as client:
        assert client.post(PATH,json=payload(account_id='other'),headers=HEADERS).status_code==403
        assert client.post('/api/v1/paper-requested/control-commands',json=control_payload(),headers=HEADERS).status_code==403
        for value in [payload(expected_financial_revision=True),payload(expected_financial_revision='1'),payload(request_id='invalid'),payload(extra='ignored')]:
            assert client.post(PATH,json=value,headers=HEADERS).status_code==422


def test_stopped_request_void_and_exact_retry_preserve_control_and_export(database):
    engine,_,settings=database;setup(engine);req=request();journal.prepare(engine,req);command(engine,'stop','STOP')
    with TestClient(create_app(configured(settings,actions=['VOID_UNSUBMITTED']))) as client:
        response=client.post(PATH,json=payload(req),headers=HEADERS)
        assert response.status_code==200 and response.headers['cache-control']=='no-store'
        summary=response.json()['finalized_request_summary']
        assert summary['state']=='VOID_UNSUBMITTED' and summary['account']==BASE and summary['funding']['reserved_cash']=='0'
        assert client.post(PATH,json=payload(req),headers=HEADERS).json()==response.json()
        assert controls.verify(client.get('/api/v1/paper-requested/controls',params={'account_id':'account'}).json())['state']=='STOPPED'
    view=journal.read(engine,'account');assert view['revision']==2 and view['total_events']==0 and view['total_finalizations']==1


def test_old_retry_does_not_release_new_request_and_conflicts_fail(database):
    engine,_,settings=database;setup(engine);req=request();journal.prepare(engine,req)
    with TestClient(create_app(configured(settings,actions=['VOID_UNSUBMITTED']))) as client:
        one=client.post(PATH,json=payload(req),headers=HEADERS);assert one.status_code==200
        newer=request(client_request_id='next',created_at=time(2));journal.prepare(engine,newer);before=journal.read(engine,'account')
        assert client.post(PATH,json=payload(req),headers=HEADERS).json()==one.json()
        for value in [payload(req,expected_financial_revision=3),payload(req,created_at=time(3)),payload(newer,expected_financial_revision=3)]:
            assert client.post(PATH,json=value,headers=HEADERS).status_code==409
        assert journal.read(engine,'account')==before


@pytest.mark.parametrize('unknown',[False,True])
def test_submitted_or_unknown_outcome_cannot_be_voided(database,unknown):
    engine,_,settings=database;setup(engine);req=request();journal.prepare(engine,req);journal.accept(engine,'account',req['request_id'],event(req,0,'SUBMIT'))
    if unknown:journal.accept(engine,'account',req['request_id'],event(req,1,'UNKNOWN_SUBMISSION'))
    before=controls.capture(engine,'account')
    with TestClient(create_app(configured(settings,actions=['VOID_UNSUBMITTED']))) as client:
        response=client.post(PATH,json=payload(req,expected_financial_revision=before['journal']['journal']['revision']),headers=HEADERS)
        assert response.status_code==409 and set(response.json())=={'detail'}
    assert controls.capture(engine,'account')==before


def test_http_void_vs_submit_race_has_one_durable_winner(database):
    engine,_,settings=database;setup(engine);req=request();journal.prepare(engine,req);barrier=Barrier(2)
    def void():
        with TestClient(create_app(configured(settings,actions=['VOID_UNSUBMITTED']))) as client:
            barrier.wait(timeout=5);return client.post(PATH,json=payload(req),headers=HEADERS).status_code
    def submit():
        barrier.wait(timeout=5)
        try:journal.accept(engine,'account',req['request_id'],event(req,0,'SUBMIT'));return True
        except ValueError:return False
    with ThreadPoolExecutor(2) as pool:
        one=pool.submit(void);two=pool.submit(submit);results=(one.result(),two.result())
    assert results in [(200,False),(409,True)]
    view=journal.read(engine,'account');assert view['revision']==2 and view['total_events']+view.get('total_finalizations',0)==1


def test_legacy_account_denied_and_stale_revision_has_no_effect(database):
    engine,_,settings=database;journal.create(engine,'account','fixture:spot',BASE,time(0));req=request();journal.prepare(engine,req);before=journal.read(engine,'account')
    with TestClient(create_app(configured(settings,actions=['VOID_UNSUBMITTED']))) as client:
        assert client.post(PATH,json=payload(req),headers=HEADERS).status_code==409
        assert journal.read(engine,'account')==before
    # The old internal operation remains compatible for explicitly unmanaged local fixtures.
    assert finalization.finalize(engine,'account',req['request_id'],'internal',1,time(1))['state']=='VOID_UNSUBMITTED'


def test_fenced_void_lock_timeout_returns_generic_retryable_error(database):
    engine,_,settings=database;setup(engine);req=request();journal.prepare(engine,req);before=journal.read(engine,'account')
    with engine.begin() as conn:
        conn.execute(text("SELECT account_id FROM requested_paper_accounts WHERE account_id='account' FOR UPDATE"))
        with TestClient(create_app(configured(settings,actions=['VOID_UNSUBMITTED']))) as client:
            response=client.post(PATH,json=payload(req),headers=HEADERS)
            assert response.status_code==503 and response.headers['cache-control']=='no-store'
    assert journal.read(engine,'account')==before


def test_controlled_stale_revision_and_missing_request_are_rejected(database):
    engine,_,settings=database;setup(engine);req=request();journal.prepare(engine,req);before=journal.read(engine,'account')
    with TestClient(create_app(configured(settings,accounts=['account','missing'],actions=['VOID_UNSUBMITTED']))) as client:
        for value in [payload(req,expected_financial_revision=2),payload(req,request_id='0'*64)]:
            assert client.post(PATH,json=value,headers=HEADERS).status_code==409
        assert client.post(PATH,json=payload(req,account_id='missing'),headers=HEADERS).status_code==404
    assert journal.read(engine,'account')==before


def test_storage_failure_is_generic():
    settings=Settings(database_url='postgresql+psycopg://invalid:invalid@127.0.0.1:1/no_db')
    with TestClient(create_app(configured(settings,actions=['VOID_UNSUBMITTED']))) as client:
        response=client.post(PATH,json=payload(),headers=HEADERS)
        assert response.status_code==503 and response.json()=={'detail':'Explicit Paper finalization temporarily unavailable'}
