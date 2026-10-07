"""Isolated PostgreSQL Paper fixtures; no production seed accounts."""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from datetime import timedelta
from decimal import Decimal
import pytest
from sqlalchemy import event as sql_event, text
from sqlalchemy.exc import DBAPIError
from core.paper import requested_journal as journal, requested_execution as contract
from tests.test_integration import database
from tests.test_requested_execution import BASE, STAMP, RULES, request, event, fill, cumulative


def setup(engine):
    journal.create(engine,'account','fixture:spot',BASE,STAMP.isoformat())
    req=request();journal.prepare(engine,req)
    return req


def test_persisted_partial_late_fill_and_cancel_seal_are_exact(database):
    engine,_,_=database;req=setup(engine)
    before=journal.read(engine,'account')
    assert before['revision']==1 and Decimal(before['requests'][0]['summary']['funding']['reserved_cash'])==404
    events=[event(req,0,'SUBMIT'),fill(req,1),event(req,2,'CANCEL_REQUEST'),event(req,3,'CANCEL_ACK'),fill(req,4,price='95',fee='0.95',identity='f2'),cumulative(req,5,'2','1.85','185',seal=True)]
    for value in events:journal.accept(engine,'account',req['request_id'],value)
    view=journal.read(engine,'account')
    assert view['revision']==7 and view['active_request_id'] is None
    assert Decimal(view['account']['cash'])==Decimal('813.15')
    assert view['requests'][0]['summary']==contract.reduce(req,events)
    assert Decimal(view['requests'][0]['summary']['funding']['reserved_cash'])==0
    for value in events:journal.accept(engine,'account',req['request_id'],value)
    assert journal.read(engine,'account')==view
    next_req=request(client_request_id='next',base=view['account'],created_at=(STAMP+timedelta(seconds=6)).isoformat())
    journal.prepare(engine,next_req)
    assert journal.read(engine,'account')['revision']==8
    assert journal.prepare(engine,req)==view['requests'][0]['summary']


def test_concurrent_creation_and_duplicate_dispatch_fill_have_one_effect(database):
    engine,_,_=database
    with ThreadPoolExecutor(2) as pool:
        results=list(pool.map(lambda _:journal.create(engine,'account','fixture:spot',BASE,STAMP.isoformat()),range(2)))
    assert results[0]==results[1]
    req=request()
    with ThreadPoolExecutor(2) as pool:list(pool.map(lambda _:journal.prepare(engine,req),range(2)))
    for value in [event(req,0,'SUBMIT'),fill(req,1)]:
        with ThreadPoolExecutor(2) as pool:list(pool.map(lambda _:journal.accept(engine,'account',req['request_id'],value),range(2)))
    view=journal.read(engine,'account')
    assert view['revision']==3 and view['total_events']==2
    assert Decimal(view['account']['cash'])==Decimal('909.10')


def test_concurrent_distinct_requests_cannot_double_reserve(database):
    engine,_,_=database
    journal.create(engine,'account','fixture:spot',BASE,STAMP.isoformat())
    def attempt(identity):
        try:journal.prepare(engine,request(quantity='9',client_request_id=identity));return True
        except ValueError:return False
    with ThreadPoolExecutor(2) as pool:results=list(pool.map(attempt,['a','b']))
    assert sorted(results)==[False,True]
    assert journal.read(engine,'account')['revision']==1


@pytest.mark.parametrize('phase',['request','event'])
def test_failure_after_insert_rolls_back_reservation_and_balance(database,phase):
    engine,_,_=database
    journal.create(engine,'account','fixture:spot',BASE,STAMP.isoformat())
    req=request()
    if phase=='event':
        journal.prepare(engine,req);journal.accept(engine,'account',req['request_id'],event(req,0,'SUBMIT'))
    before=journal.read(engine,'account')
    table='requested_paper_requests' if phase=='request' else 'requested_paper_events'
    def fail(conn,cursor,statement,parameters,context,executemany):
        if statement.startswith('INSERT INTO '+table):raise RuntimeError('After immutable insert, before commit')
    sql_event.listen(engine,'after_cursor_execute',fail)
    try:
        with pytest.raises(RuntimeError):
            if phase=='request':journal.prepare(engine,req)
            else:journal.accept(engine,'account',req['request_id'],fill(req,1))
    finally:sql_event.remove(engine,'after_cursor_execute',fail)
    assert journal.read(engine,'account')==before
    if phase=='request':journal.prepare(engine,req)
    else:journal.accept(engine,'account',req['request_id'],fill(req,1))
    engine.dispose()
    assert journal.read(engine,'account')['revision']==before['revision']+1


def test_conflicting_retries_stale_base_and_cross_account_events_fail_without_effect(database):
    engine,_,_=database;req=setup(engine)
    before=journal.read(engine,'account')
    with pytest.raises(ValueError):journal.create(engine,'account','other',BASE,STAMP.isoformat())
    with pytest.raises(ValueError):journal.prepare(engine,request(quantity='3'))
    with pytest.raises(ValueError):journal.accept(engine,'account','unknown',event(req,0,'SUBMIT'))
    assert journal.read(engine,'account')==before
    journal.accept(engine,'account',req['request_id'],event(req,0,'SUBMIT'))
    journal.accept(engine,'account',req['request_id'],event(req,1,'CANCEL_REQUEST'))
    journal.accept(engine,'account',req['request_id'],event(req,2,'CANCEL_ACK'))
    journal.accept(engine,'account',req['request_id'],cumulative(req,3,'0','0','0',seal=True))
    with pytest.raises(ValueError):journal.prepare(engine,request(client_request_id='stale',base={**BASE,'cash':'999'},created_at=(STAMP+timedelta(seconds=4)).isoformat()))
    with pytest.raises(ValueError):journal.prepare(engine,request(client_request_id='old-clock'))
    journal.create(engine,'other','fixture:spot',BASE,STAMP.isoformat())
    with pytest.raises(ValueError):journal.accept(engine,'other',req['request_id'],event(req,0,'SUBMIT'))


@pytest.mark.parametrize('table',['requested_paper_requests','requested_paper_events'])
@pytest.mark.parametrize('action',['UPDATE','DELETE'])
def test_immutable_journal_rows(database,table,action):
    engine,_,_=database;req=setup(engine)
    journal.accept(engine,'account',req['request_id'],event(req,0,'SUBMIT'))
    with engine.begin() as conn:
        with pytest.raises(DBAPIError):conn.execute(text(f'DELETE FROM {table}' if action=='DELETE' else f'UPDATE {table} SET payload_sha256=payload_sha256'))


def test_corrupt_cache_blocks_reads_and_new_economic_effects(database):
    engine,_,_=database;req=setup(engine)
    with engine.begin() as conn:
        conn.execute(text("UPDATE requested_paper_accounts SET current=jsonb_set(current::jsonb,'{cash}','\"999\"')::json, revision=revision+1 WHERE account_id='account'"))
    with pytest.raises(ValueError):journal.read(engine,'account')
    with pytest.raises(ValueError):journal.accept(engine,'account',req['request_id'],event(req,0,'SUBMIT'))


def test_conflicting_or_incomplete_event_fails_atomically(database):
    engine,_,_=database;req=setup(engine)
    before=journal.read(engine,'account')
    with pytest.raises(ValueError):journal.accept(engine,'account',req['request_id'],fill(req,1))
    assert journal.read(engine,'account')==before
    original=event(req,0,'SUBMIT');journal.accept(engine,'account',req['request_id'],original)
    changed=deepcopy(original);changed['kind']='ACK'
    with pytest.raises(ValueError):journal.accept(engine,'account',req['request_id'],changed)
    assert journal.read(engine,'account')['revision']==2


@pytest.mark.parametrize('phase',['request','fill'])
@pytest.mark.parametrize('committed',[False,True])
def test_actual_process_kill_before_commit_or_after_lost_reply(database,phase,committed):
    import json
    import signal
    import subprocess
    import sys
    engine,_,_=database
    journal.create(engine,'account','fixture:spot',BASE,STAMP.isoformat())
    req=request()
    if phase=='fill':
        journal.prepare(engine,req);journal.accept(engine,'account',req['request_id'],event(req,0,'SUBMIT'))
    before=journal.read(engine,'account')
    code='''
import json, os, signal, sys
from sqlalchemy import create_engine, event
from apps.api.settings import Settings
from core.paper import requested_journal as journal
value=json.load(sys.stdin)
engine=create_engine(Settings().database_url.get_secret_value())
if not value['committed']:
    def kill(conn,cursor,statement,parameters,context,executemany):
        table='requested_paper_requests' if value['phase']=='request' else 'requested_paper_events'
        if statement.startswith('INSERT INTO '+table):os.kill(os.getpid(),signal.SIGKILL)
    event.listen(engine,'after_cursor_execute',kill)
if value['phase']=='request':journal.prepare(engine,value['request'])
else:journal.accept(engine,'account',value['request']['request_id'],value['event'])
os.kill(os.getpid(),signal.SIGKILL)
'''
    result=subprocess.run([sys.executable,'-c',code],input=json.dumps({'phase':phase,'committed':committed,'request':req,'event':fill(req,1)}),text=True,capture_output=True,timeout=30)
    assert result.returncode==-signal.SIGKILL, result.stderr
    engine.dispose()
    recovered=journal.read(engine,'account')
    assert recovered['revision']==before['revision']+(1 if committed else 0)
    if not committed:assert recovered==before
    if phase=='request':journal.prepare(engine,req)
    else:journal.accept(engine,'account',req['request_id'],fill(req,1))
    final=journal.read(engine,'account')
    assert final['revision']==before['revision']+1
    if phase=='fill':assert Decimal(final['account']['cash'])==Decimal('909.10')


def test_history_capacity_denies_new_events_but_preserves_exact_redelivery(database,monkeypatch):
    engine,_,_=database;req=setup(engine)
    first=event(req,0,'SUBMIT');journal.accept(engine,'account',req['request_id'],first)
    monkeypatch.setattr(journal,'TOTAL_EVENTS',1)
    before=journal.read(engine,'account')
    with pytest.raises(ValueError):journal.accept(engine,'account',req['request_id'],fill(req,1))
    journal.accept(engine,'account',req['request_id'],first)
    assert journal.read(engine,'account')==before


def test_persisted_sell_basis_and_following_request_use_settled_balance(database):
    engine,_,_=database
    base={**BASE,'cash':'100','quantity':'10','cost_basis':'100'}
    journal.create(engine,'account','fixture:spot',base,STAMP.isoformat())
    req=request('SELL',price='12',base=base);journal.prepare(engine,req)
    values=[event(req,0,'SUBMIT'),fill(req,1,price='15',fee='0.15'),event(req,2,'CANCEL_REQUEST'),event(req,3,'CANCEL_ACK')]
    for value in values:journal.accept(engine,'account',req['request_id'],value)
    before=journal.read(engine,'account')
    assert Decimal(before['requests'][0]['summary']['funding']['reserved_quantity'])==3
    assert Decimal(before['account']['cost_basis'])==90
    assert Decimal(before['account']['realized_pnl'])==Decimal('4.85')
    journal.accept(engine,'account',req['request_id'],cumulative(req,4,'1','0.15','15',seal=True))
    view=journal.read(engine,'account')
    next_req=request('SELL',quantity='9',price='12',base=view['account'],client_request_id='sell-rest',created_at=(STAMP+timedelta(seconds=5)).isoformat())
    journal.prepare(engine,next_req)
    def later(value):
        value['received_at']=(STAMP+timedelta(seconds=value['sequence']+5)).isoformat();return value
    journal.accept(engine,'account',next_req['request_id'],later(event(next_req,0,'SUBMIT')))
    journal.accept(engine,'account',next_req['request_id'],later(fill(next_req,1,size='9',price='12',fee='1.08',executed_seconds=6)))
    final=journal.read(engine,'account')
    assert Decimal(final['account']['quantity'])==Decimal(final['account']['cost_basis'])==0
    assert Decimal(final['account']['cash'])==Decimal('221.77')
    assert Decimal(final['account']['realized_pnl'])==Decimal('21.77')
    assert Decimal(final['account']['fees'])==Decimal('1.23')


def test_resealed_wrong_settlement_evidence_still_fails_recomputation(database):
    from core.paper.fault_adapter import sha
    from core.storage.models import RequestedPaperEventRecord
    from sqlalchemy.orm import Session
    engine,_,_=database;req=setup(engine)
    journal.accept(engine,'account',req['request_id'],event(req,0,'SUBMIT'))
    with Session(engine) as db,db.begin():
        record=db.get(RequestedPaperEventRecord,(req['request_id'],0))
        wrong=deepcopy(record.summary);wrong['account']['cash']='999'
        db.execute(text('ALTER TABLE requested_paper_events DISABLE TRIGGER USER'))
        record.summary=wrong;record.summary_sha256=sha(wrong);db.flush()
        db.execute(text('ALTER TABLE requested_paper_events ENABLE TRIGGER USER'))
    with pytest.raises(ValueError,match='settlement evidence'):journal.read(engine,'account')
    with pytest.raises(ValueError):journal.accept(engine,'account',req['request_id'],fill(req,1))


def test_non_postgresql_is_rejected_before_any_storage_access():
    from sqlalchemy import create_engine
    engine=create_engine('sqlite://')
    try:
        with pytest.raises(ValueError,match='PostgreSQL required'):
            journal.create(engine,'account','fixture:spot',BASE,STAMP.isoformat())
        with pytest.raises(ValueError,match='PostgreSQL required'):journal.read(engine,'account')
    finally:engine.dispose()


def test_autocommit_engine_is_rejected_before_mutation(database):
    engine,_,_=database;req=setup(engine)
    before=journal.read(engine,'account')
    unsafe=engine.execution_options(isolation_level='AUTOCOMMIT')
    with pytest.raises(ValueError,match='Autocommit'):
        journal.accept(unsafe,'account',req['request_id'],event(req,0,'SUBMIT'))
    with pytest.raises(ValueError,match='Autocommit'):
        journal.create(unsafe,'other','fixture:spot',BASE,STAMP.isoformat())
    assert journal.read(engine,'account')==before
    with pytest.raises(ValueError,match='Unknown'):journal.read(engine,'other')
