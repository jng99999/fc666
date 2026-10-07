"""Scoped source previews have no economic/source writes."""
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from apps.api.main import create_app
from apps.api.settings import Settings
from core.paper import requested_event_preview as preview,requested_journal as journal,requested_controls as controls
from tests.test_integration import database
from tests.test_requested_commands import configured,HEADERS
from tests.test_requested_controls import setup,command
from tests.test_requested_execution import request,event,fill
from tests.test_requested_event_preview import proposal

PATH='/api/v1/paper-requested/event-previews'


def body(engine,req,source):
    financial,controlled=controls.read(engine,'account');return proposal(req,financial,controlled,source)


def test_authentication_and_independent_scope_precede_storage(monkeypatch):
    settings=Settings(database_url='postgresql+psycopg://invalid:invalid@127.0.0.1:1/no_db');req=request()
    value={'account_id':'account','request_id':req['request_id'],'expected_financial_revision':1,'expected_control_revision':2,
           'source':{'kind':'LOCAL_PAPER_OPERATOR_INPUT','source_id':'fixture'},'event':event(req,0,'SUBMIT')}
    def forbidden(*args,**kwargs):raise AssertionError('Denied access touched storage')
    monkeypatch.setattr(preview,'capture',forbidden)
    with TestClient(create_app(settings)) as client:assert client.post(PATH,json=value).status_code==503
    with TestClient(create_app(configured(settings,actions=['PREPARE']))) as client:
        assert client.post(PATH,json=value).status_code==401
        assert client.post(PATH,json=value,headers=HEADERS).status_code==403
    with TestClient(create_app(configured(settings,actions=['PREVIEW_EVENT']))) as client:
        assert client.post(PATH,json={**value,'account_id':'other'},headers=HEADERS).status_code==403
        for changes in [{'expected_control_revision':True},{'source':{'kind':'EXCHANGE_VERIFIED','source_id':'fixture'}},{'extra':'ignored'}]:
            assert client.post(PATH,json={**value,**changes},headers=HEADERS).status_code==422


def test_submit_unknown_duplicate_and_late_fill_previews_never_write(database):
    engine,_,settings=database;setup(engine);req=request();journal.prepare(engine,req)
    with TestClient(create_app(configured(settings,actions=['PREVIEW_EVENT']))) as client:
        before=controls.capture(engine,'account');value=body(engine,req,event(req,0,'SUBMIT'))
        response=client.post(PATH,json=value,headers=HEADERS);assert response.status_code==200 and response.headers['cache-control']=='no-store'
        assert preview.verify(response.json())['classification']=='NEW_LOCAL_INPUT' and controls.capture(engine,'account')==before
        journal.accept(engine,'account',req['request_id'],event(req,0,'SUBMIT'))
        for source in [event(req,0,'SUBMIT'),event(req,1,'UNKNOWN_SUBMISSION')]:
            before=controls.capture(engine,'account');response=client.post(PATH,json=body(engine,req,source),headers=HEADERS)
            assert response.status_code==200 and not preview.verify(response.json())['event_persisted']
            assert controls.capture(engine,'account')==before
        command(engine,'stop','STOP')
        before=controls.capture(engine,'account');response=client.post(PATH,json=body(engine,req,fill(req,1)),headers=HEADERS)
        assert response.status_code==200 and controls.capture(engine,'account')==before
        assert preview.verify(response.json())['controls']['state']=='STOPPED'


def test_stale_sequence_conflicting_duplicate_and_stopped_submit_are_rejected(database):
    engine,_,settings=database;setup(engine);req=request();journal.prepare(engine,req);value=body(engine,req,event(req,0,'SUBMIT'))
    with TestClient(create_app(configured(settings,actions=['PREVIEW_EVENT']))) as client:
        command(engine,'pause','PAUSE');before=controls.capture(engine,'account')
        assert client.post(PATH,json=value,headers=HEADERS).status_code==409
        assert client.post(PATH,json=body(engine,req,event(req,0,'SUBMIT')),headers=HEADERS).status_code==409
        assert controls.capture(engine,'account')==before
        command(engine,'resume-again','RESUME');journal.accept(engine,'account',req['request_id'],event(req,0,'SUBMIT'))
        before=controls.capture(engine,'account')
        bad=event(req,0,'SUBMIT');bad['received_at']='2026-01-01T00:00:01+00:00'
        for source in [bad,event(req,2,'ACK')]:assert client.post(PATH,json=body(engine,req,source),headers=HEADERS).status_code==409
        assert controls.capture(engine,'account')==before


def test_missing_account_lock_timeout_and_storage_errors_are_generic(database):
    engine,_,settings=database;setup(engine);req=request();journal.prepare(engine,req);value=body(engine,req,event(req,0,'SUBMIT'))
    with TestClient(create_app(configured(settings,accounts=['account','missing'],actions=['PREVIEW_EVENT']))) as client:
        assert client.post(PATH,json={**value,'account_id':'missing'},headers=HEADERS).status_code==404
        with engine.begin() as conn:
            conn.execute(text("SELECT account_id FROM requested_paper_accounts WHERE account_id='account' FOR UPDATE"))
            response=client.post(PATH,json=value,headers=HEADERS);assert response.status_code==503 and response.headers['cache-control']=='no-store'
    unavailable=Settings(database_url='postgresql+psycopg://invalid:invalid@127.0.0.1:1/no_db')
    with TestClient(create_app(configured(unavailable,actions=['PREVIEW_EVENT']))) as client:
        response=client.post(PATH,json=value,headers=HEADERS);assert response.status_code==503 and set(response.json())=={'detail'}
