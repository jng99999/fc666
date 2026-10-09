from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
import pytest
from sqlalchemy import event as sql_event,text
from sqlalchemy.exc import SQLAlchemyError,DBAPIError
from core.paper import requested_inbox as inbox,requested_journal as journal,requested_controls as controls,shared_capital_pool as pools,shared_capital_admission as admission
from core.portfolio import pool_risk
from tests.test_integration import database
from tests.test_requested_health import ready
from tests.test_five_block_foundations import staged
from tests.test_requested_execution import request,event,fill,cumulative,BASE,STAMP
from tests.test_requested_controls import LIMITS
from tests.test_shared_capital_admission import proposed


def drain_command():return dict(account_id='account',request_id=request()['request_id'],expected_financial_revision=1,expected_control_revision=2,owner='worker',ownership_token=1,max_events=16)


def test_atomic_drain_selects_sequence_and_seals_funds(database):
    engine,_,_=database;ready(engine,enroll=False);value=staged(engine,0);req=request()
    events=[event(req,0,'SUBMIT'),fill(req,1,size='4',fee='3.60'),cumulative(req,2,'4','3.60','360',seal=True)]
    receipts=[inbox.stage(engine,{**value,'event':e}) for e in reversed(events)]
    result=inbox.drain(engine,{**drain_command(),'max_events':2})
    assert result['events_committed']==2 and result['state']=='BATCH_LIMIT' and result['final_financial_revision']==3
    result=inbox.drain(engine,{**drain_command(),'expected_financial_revision':3})
    assert result['events_committed']==1 and result['state']=='NO_PENDING_INPUT'
    financial=journal.read(engine,'account');assert financial['active_request_id'] is None and financial['account']['cash']=='636.40'
    assert inbox.capture(engine,'account')['records']==receipts
    assert inbox.drain(engine,{**drain_command(),'expected_financial_revision':4})['events_committed']==0


def test_drain_fault_after_first_event_rolls_back_entire_batch(database):
    engine,_,_=database;ready(engine,enroll=False);value=staged(engine,0);req=request()
    for e in [event(req,0,'SUBMIT'),fill(req,1)]:inbox.stage(engine,{**value,'event':e})
    calls=[]
    def fail(conn,cursor,statement,*args):
        if statement.startswith('INSERT INTO requested_paper_events'):
            calls.append(statement)
            if len(calls)==2:raise SQLAlchemyError('fixture batch fault')
    sql_event.listen(engine,'after_cursor_execute',fail)
    try:
        with pytest.raises(SQLAlchemyError):inbox.drain(engine,drain_command())
    finally:sql_event.remove(engine,'after_cursor_execute',fail)
    assert journal.read(engine,'account')['revision']==1
    with engine.connect() as db:assert db.scalar(text('SELECT count(*) FROM requested_paper_sources'))==0
    assert inbox.drain(engine,drain_command())['events_committed']==2


def risk_ready(engine,cap='5'):
    instrument='binance:spot:BTC-USDT'
    for name in ['a','b']:
        journal.create(engine,name,instrument,BASE,STAMP.isoformat())
        controls.enroll(engine,name,controls.policy(name,LIMITS),'enroll-'+name,STAMP.isoformat())
        controls.command(engine,name,'resume-'+name,1,'RESUME',STAMP.isoformat())
    pools.create(engine,dict(pool_id='pool',quote_asset='USDT',opening_quote_cash='2500',max_reserved_quote='1000',allocations=[dict(account_id=name,instrument_id=instrument,opening_quote_cash='1000') for name in ['a','b']]))
    value=dict(version=pool_risk.VERSION,pool_id='pool',max_quantity_by_instrument={instrument:cap},max_active_requests=2,min_available_quote='0')
    return pool_risk.enroll(engine,value)


def test_pool_quantity_gate_is_atomic_and_replayable(database):
    engine,_,_=database;policy=risk_ready(engine);a,digest=proposed(engine,'a');b,other=proposed(engine,'b')
    accepted=admission.prepare(engine,'pool',a,digest);assert accepted['version']==admission.VERSION_V2
    assert admission.verify(accepted)==accepted and pool_risk.verify(accepted['risk_decision'])==accepted['risk_decision']
    assert admission.prepare(engine,'pool',a,digest)==accepted
    denied=admission.capture_preview(engine,'pool',b);assert not denied['capital_forecast_allowed'] and 'PROJECTED_QUANTITY_CAP' in denied['risk_decision']['reasons']
    assert admission.verify_preview(denied)==denied
    with pytest.raises(ValueError):admission.prepare(engine,'pool',b,other)
    assert journal.read(engine,'b')['revision']==0
    assert pool_risk.enroll(engine,policy)==policy
    with pytest.raises(ValueError):pool_risk.enroll(engine,{**policy,'min_available_quote':'1'})
    for stmt in ['DELETE FROM paper_capital_risk_policies',"UPDATE paper_capital_risk_policies SET payload_sha256='changed'"]:
        with pytest.raises(DBAPIError):
            with engine.begin() as db:db.execute(text(stmt))


def test_simultaneous_pool_quantity_reservations_are_serialized(database):
    engine,_,_=database;risk_ready(engine);values=[proposed(engine,name) for name in ['a','b']];barrier=Barrier(2)
    def commit(value):
        barrier.wait(timeout=5)
        try:return admission.prepare(engine,'pool',*value)
        except ValueError:return None
    with ThreadPoolExecutor(max_workers=2) as executor:results=list(executor.map(commit,values))
    assert sum(value is not None for value in results)==1
    with engine.connect() as db:assert db.scalar(text('SELECT count(*) FROM paper_capital_admissions'))==1


def test_risk_scope_and_drain_grants_precede_storage(monkeypatch):
    from fastapi.testclient import TestClient
    from apps.api.main import create_app
    from apps.api.settings import Settings
    from tests.test_requested_commands import configured,HEADERS
    def forbidden(*args,**kwargs):raise AssertionError('Denied request reached storage')
    monkeypatch.setattr(pool_risk,'enroll',forbidden);monkeypatch.setattr(inbox,'drain',forbidden)
    settings=Settings(database_url='postgresql+psycopg://invalid:invalid@127.0.0.1:1/no_db')
    with TestClient(create_app(configured(settings,actions=['READ_LOCAL_INBOX']))) as client:
        for path,body in [('/api/v1/paper-requested/inbox-drain-commands',drain_command()),('/api/v1/paper-requested/pool-risk-enrollment-commands',dict(pool_id='pool',max_quantity_by_instrument={'binance:spot:BTC-USDT':'5'},max_active_requests=2,min_available_quote='0'))]:
            assert client.post(path,json=body).status_code==401
            assert client.post(path,json=body,headers=HEADERS).status_code==403
