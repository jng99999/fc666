"""Atomic staged-input application uses isolated PostgreSQL accounts."""
from copy import deepcopy
import pytest
from sqlalchemy import event as sql_event
from sqlalchemy.exc import SQLAlchemyError
from fastapi.testclient import TestClient
from apps.api.main import create_app
from apps.api.settings import Settings
from core.paper import requested_inbox as inbox,requested_journal as journal,requested_ownership as ownership
from tests.test_integration import database
from tests.test_requested_health import ready
from tests.test_five_block_foundations import staged
from tests.test_requested_execution import request,event,fill
from tests.test_requested_commands import configured,HEADERS


def command(receipt,revision=1):
    return dict(account_id='account',ordinal=receipt['ordinal'],staging_sha256=receipt['sha256'],
                expected_financial_revision=revision,expected_control_revision=2,owner='worker',ownership_token=1)


def test_apply_order_funds_retry_and_original_receipt(database):
    engine,_,_=database;ready(engine,enroll=False);value=staged(engine,1)
    pending=inbox.stage(engine,value)
    with pytest.raises(ValueError):inbox.apply(engine,command(pending))
    submit=inbox.stage(engine,{**value,'event':event(request(),0,'SUBMIT')})
    inbox.apply(engine,command(submit))
    result=inbox.apply(engine,command(pending,2))
    assert journal.read(engine,'account')['revision']==3
    assert journal.read(engine,'account')['requests'][0]['events'][1]==value['event']
    assert inbox.apply(engine,command(pending,3))==result
    assert inbox.capture(engine,'account')['records'][0]==pending
    assert inbox.capture(engine,'account')['application_observations'][0]['journal_state']=='PRESENT_IN_JOURNAL'
    assert journal.read(engine,'account')['account']['cash']=='909.10'
    for change in [dict(expected_financial_revision=2),dict(owner='other'),dict(staging_sha256='0'*64)]:
        with pytest.raises(ValueError):inbox.apply(engine,{**command(pending,3),**change})
    assert journal.read(engine,'account')['revision']==3


def test_apply_fault_rolls_back_source_and_event(database):
    engine,_,_=database;ready(engine,enroll=False);value=staged(engine,0)
    receipt=inbox.stage(engine,{**value,'event':event(request(),0,'SUBMIT')})
    def fail(conn,cursor,statement,*args):
        if statement.startswith('INSERT INTO requested_paper_events'):raise SQLAlchemyError('fixture fault')
    sql_event.listen(engine,'after_cursor_execute',fail)
    try:
        with pytest.raises(SQLAlchemyError):inbox.apply(engine,command(receipt))
    finally:sql_event.remove(engine,'after_cursor_execute',fail)
    assert journal.read(engine,'account')['revision']==1
    assert inbox.capture(engine,'account')['application_observations'][0]['journal_state']=='NOT_PRESENT_IN_JOURNAL'
    assert inbox.apply(engine,command(receipt))['event_committed']


def test_apply_expired_owner_and_overflow_preserve_staging(database,monkeypatch):
    engine,_,_=database;ready(engine,enroll=False);receipt=inbox.stage(engine,staged(engine))
    with pytest.raises(ValueError):inbox.apply(engine,command(receipt))
    monkeypatch.setattr(ownership,'clock',lambda db:receipt['received_us']+100_000_000)
    with pytest.raises(ValueError):inbox.apply(engine,command(receipt))
    assert journal.read(engine,'account')['revision']==1
    assert inbox.capture(engine,'account')['records']==[receipt]


def test_application_grant_denied_before_storage(monkeypatch):
    def forbidden(*args,**kwargs):raise AssertionError('Denied request reached storage')
    monkeypatch.setattr(inbox,'apply',forbidden)
    settings=Settings(database_url='postgresql+psycopg://invalid:invalid@127.0.0.1:1/no_db')
    body=command({'ordinal':0,'sha256':'0'*64})
    path='/api/v1/paper-requested/inbox-application-commands'
    with TestClient(create_app(configured(settings,actions=['STAGE_LOCAL_INPUT','READ_LOCAL_INBOX']))) as client:
        assert client.post(path,json=body).status_code==401
        assert client.post(path,json=body,headers=HEADERS).status_code==403


def test_application_server_health_failure_is_atomic(database):
    engine,_,_=database;cache=ready(engine,enroll=True);req=request()
    journal.prepare(engine,req,health_cache=cache)
    ownership.claim(engine,'account',req['request_id'],'worker',60,expected_financial_revision=1,expected_control_revision=2,expected_token=0)
    value=dict(account_id='account',request_id=req['request_id'],expected_financial_revision=1,owner='worker',ownership_token=1,source={'kind':'LOCAL_PAPER_OPERATOR_INPUT','source_id':'fixture'})
    receipt=inbox.stage(engine,{**value,'event':event(request(),0,'SUBMIT')})
    with pytest.raises(ValueError):inbox.apply(engine,command(receipt),health_cache=None)
    assert journal.read(engine,'account')['revision']==1
    assert inbox.capture(engine,'account')['application_observations'][0]['journal_state']=='NOT_PRESENT_IN_JOURNAL'
