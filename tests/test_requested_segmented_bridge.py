from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from datetime import timedelta
from decimal import Decimal
import selectors
import signal
import subprocess
import sys
import pytest
from sqlalchemy import event as sql_event
from core.paper import requested_segmented_bridge as bridge, requested_segmented_account as accounts
from core.paper import requested_segmented_execution as segments, requested_journal as journal
from core.paper.fault_adapter import encoded
from tests.test_integration import database
from tests.test_requested_execution import BASE,STAMP,request,event,fill,cumulative


def setup(engine):
    journal.create(engine,'account','fixture:spot',BASE,STAMP.isoformat())
    req=request();journal.prepare(engine,req)
    return req,accounts.capture(engine,'account')


def proposal(before,batch):
    entries=before['requests'];reports=[entry['report'] for entry in entries]
    reports[-1]=segments.append(reports[-1],batch)
    voids={entry['report']['request']['request_id']:entry['void'] for entry in entries if entry['void'] is not None}
    return accounts.replay(before['opening'],reports,finalizations=voids)


def apply(engine,before,candidate):return bridge.apply(engine,'account',before['revision'],before['sha256'],candidate)


def test_atomic_fills_cancel_late_settlement_and_lost_reply_retry(database):
    engine,_,_=database;req,before=setup(engine)
    candidate=proposal(before,[event(req,0,'SUBMIT'),fill(req,1),event(req,2,'CANCEL_REQUEST'),event(req,3,'CANCEL_ACK')])
    assert apply(engine,before,candidate)==candidate
    persisted=journal.read(engine,'account')
    assert persisted['revision']==5 and Decimal(persisted['account']['cash'])==Decimal('909.1')
    assert Decimal(persisted['requests'][0]['summary']['funding']['reserved_cash'])==303
    assert apply(engine,before,candidate)==candidate
    assert journal.read(engine,'account')==persisted
    current=accounts.capture(engine,'account')
    final=proposal(current,[fill(req,4,price='95',fee='0.95',identity='late',executed_seconds=3),cumulative(req,5,'2','1.85','185',seal=True)])
    apply(engine,current,final)
    after=journal.read(engine,'account');engine.dispose()
    assert journal.read(engine,'account')==after
    assert Decimal(after['account']['cash'])==Decimal('813.15')
    assert after['active_request_id'] is None
    assert Decimal(after['requests'][0]['summary']['funding']['reserved_cash'])==0
    # Original lost-reply retry after later settlement acknowledges that prefix only.
    assert apply(engine,before,candidate)==candidate
    assert journal.read(engine,'account')==after
    next_req=request(client_request_id='next',base=after['account'],created_at=(STAMP+timedelta(seconds=6)).isoformat())
    journal.prepare(engine,next_req)
    later=journal.read(engine,'account')
    assert Decimal(later['requests'][-1]['summary']['funding']['reserved_cash'])==404
    assert apply(engine,before,candidate)==candidate
    assert journal.read(engine,'account')==later


def test_concurrent_identical_batches_have_one_effect(database):
    engine,_,_=database;req,before=setup(engine)
    candidate=proposal(before,[event(req,0,'SUBMIT'),fill(req,1)])
    with ThreadPoolExecutor(2) as pool:results=list(pool.map(lambda _:apply(engine,before,candidate),range(2)))
    assert results==[candidate,candidate]
    assert journal.read(engine,'account')['revision']==3
    assert Decimal(journal.read(engine,'account')['account']['cash'])==Decimal('909.1')


def test_concurrent_conflicting_batches_one_wins_without_mixed_fill(database):
    engine,_,_=database;req,before=setup(engine)
    candidates=[proposal(before,[event(req,0,'SUBMIT'),fill(req,1)]),proposal(before,[event(req,0,'SUBMIT'),fill(req,1,price='95',fee='0.95')])]
    def attempt(candidate):
        try:apply(engine,before,candidate);return True
        except ValueError:return False
    with ThreadPoolExecutor(2) as pool:results=list(pool.map(attempt,candidates))
    assert sorted(results)==[False,True]
    view=journal.read(engine,'account')
    assert view['revision']==3 and view['total_events']==2
    assert Decimal(view['account']['cash']) in (Decimal('909.1'),Decimal('904.05'))


def test_failure_after_second_insert_rolls_back_entire_batch(database):
    engine,_,_=database;req,before=setup(engine)
    candidate=proposal(before,[event(req,0,'SUBMIT'),fill(req,1)])
    original=journal.read(engine,'account');inserts=0
    def fail(conn,cursor,statement,parameters,context,executemany):
        nonlocal inserts
        if statement.startswith('INSERT INTO requested_paper_events'):
            inserts+=1
            if inserts==2:raise RuntimeError('Injected commit failure')
    sql_event.listen(engine,'after_cursor_execute',fail)
    try:
        with pytest.raises(RuntimeError):apply(engine,before,candidate)
    finally:sql_event.remove(engine,'after_cursor_execute',fail)
    assert inserts==2 and journal.read(engine,'account')==original
    apply(engine,before,candidate)
    assert journal.read(engine,'account')['revision']==3


def test_partial_independent_delivery_and_wrong_checkpoint_cannot_resume_candidate(database):
    engine,_,_=database;req,before=setup(engine)
    candidate=proposal(before,[event(req,0,'SUBMIT'),fill(req,1)])
    journal.accept(engine,'account',req['request_id'],event(req,0,'SUBMIT'))
    original=journal.read(engine,'account')
    with pytest.raises(ValueError,match='Stale'):apply(engine,before,candidate)
    with pytest.raises(ValueError):bridge.apply(engine,'account',before['revision'],'0'*64,candidate)
    assert journal.read(engine,'account')==original


def test_candidate_must_not_create_requests_rewrite_base_or_expand_frozen_capacity(database,monkeypatch):
    engine,_,_=database;req,before=setup(engine)
    candidate=proposal(before,[event(req,0,'SUBMIT'),fill(req,1)])
    original=journal.read(engine,'account')
    tampered=deepcopy(candidate);tampered['account']['cash']='999'
    with pytest.raises(ValueError):apply(engine,before,tampered)
    with monkeypatch.context() as bounded:
        bounded.setattr(journal,'TOTAL_EVENTS',1)
        with pytest.raises(ValueError,match='Frozen database'):apply(engine,before,candidate)
    big=proposal(before,[event(req,i,'ACK' if i else 'SUBMIT') for i in range(17)])
    with pytest.raises(ValueError,match='batch capacity'):apply(engine,before,big)
    with pytest.raises(ValueError):bridge.apply(engine,'another',before['revision'],before['sha256'],candidate)
    assert journal.read(engine,'account')==original


def test_controlled_account_cannot_bypass_source_ownership_binding(database):
    from tests.test_requested_controls import setup as controlled_setup
    engine,_,_=database;controlled_setup(engine)
    req=request();journal.prepare(engine,req)
    before=accounts.capture(engine,'account');original=journal.read(engine,'account')
    candidate=proposal(before,[event(req,0,'SUBMIT'),fill(req,1)])
    with pytest.raises(ValueError,match='Controlled accounts'):apply(engine,before,candidate)
    assert journal.read(engine,'account')==original


@pytest.mark.parametrize('phase',['before','after'])
def test_process_kill_commit_boundary_preserves_funding_and_retry(database,tmp_path,phase):
    engine,_,_=database;req,before=setup(engine)
    candidate=proposal(before,[event(req,0,'SUBMIT'),fill(req,1)])
    original=journal.read(engine,'account')
    path=tmp_path/'input.json';path.write_text(encoded({'before':before,'candidate':candidate}))
    code='''import json,os,signal,sys
from sqlalchemy import create_engine,event
from sqlalchemy.orm import Session
from apps.api.settings import Settings
from core.paper.requested_segmented_bridge import apply
engine=create_engine(Settings().database_url.get_secret_value(),hide_parameters=True)
def pause(conn,cursor,statement,parameters,context,executemany):
 if statement.startswith('INSERT INTO requested_paper_events'):
  print('boundary',flush=True)
  os.kill(os.getpid(),signal.SIGSTOP)
def committed(session):
 print('boundary',flush=True)
 os.kill(os.getpid(),signal.SIGSTOP)
if sys.argv[2]=='before':event.listen(engine,'after_cursor_execute',pause)
else:event.listen(Session,'after_commit',committed)
value=json.load(open(sys.argv[1]));before=value['before']
apply(engine,'account',before['revision'],before['sha256'],value['candidate'])
'''
    process=subprocess.Popen([sys.executable,'-c',code,str(path),phase],stdout=subprocess.PIPE,stderr=subprocess.PIPE)
    try:
        with selectors.DefaultSelector() as selector:
            selector.register(process.stdout,selectors.EVENT_READ)
            assert selector.select(timeout=10),'Child did not reach uncommitted insert'
        assert process.stdout.readline().strip()==b'boundary'
        process.kill();process.wait(timeout=5)
        assert process.returncode==-signal.SIGKILL
    finally:
        if process.poll() is None:process.kill();process.wait(timeout=5)
    if phase=='before':assert journal.read(engine,'account')==original
    else:
        persisted=journal.read(engine,'account')
        assert persisted['revision']==3 and Decimal(persisted['account']['cash'])==Decimal('909.1')
    apply(engine,before,candidate)
    assert journal.read(engine,'account')['revision']==3


def test_pool_account_requires_whole_pool_binding(database):
    from tests.test_shared_capital_admission import ready,proposed
    from core.paper import shared_capital_admission as admission
    engine,_,_=database;ready(engine)
    value,digest=proposed(engine,'a');admission.prepare(engine,'pool',value,digest)
    before=accounts.capture(engine,'a');req=before['requests'][-1]['report']['request']
    candidate=proposal(before,[event(req,0,'SUBMIT'),fill(req,1)])
    original=journal.read(engine,'a')
    with pytest.raises(ValueError,match='Pool accounts'):
        bridge.apply(engine,'a',before['revision'],before['sha256'],candidate)
    assert journal.read(engine,'a')==original
