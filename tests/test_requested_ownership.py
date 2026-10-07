"""Real isolated PostgreSQL lease/fencing acceptance; no main fixture capital."""
from copy import deepcopy
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
import json, subprocess, sys, signal
import pytest
from sqlalchemy import text, event as sql_event
from sqlalchemy.exc import DBAPIError
from fastapi.testclient import TestClient
from apps.api.main import create_app
from core.paper import requested_ownership as own, requested_sources as sources, requested_journal as journal
from core.paper.fault_adapter import sha
from tests.test_integration import database
from tests.test_requested_sources import prepared, reviewed, accept
from tests.test_requested_execution import event, fill


def deliver(engine,value,owner,token):
    return own.deliver(engine,{k:v for k,v in value.items() if k!='preview_sha256'},value['preview_sha256'],owner,token)


def test_claim_retry_source_fencing_and_stable_receipt(database):
    engine,_,settings=database;req=prepared(engine);before=journal.read(engine,'account')
    lease=own.claim(engine,'account',req['request_id'],'first');assert lease['token']==1
    assert own.claim(engine,'account',req['request_id'],'first',60)==lease
    assert journal.read(engine,'account')==before
    value,_=reviewed(engine,req,event(req,0,'SUBMIT'))
    with pytest.raises(ValueError):accept(engine,value)
    with pytest.raises(ValueError):journal.accept(engine,'account',req['request_id'],value['event'])
    with pytest.raises(ValueError):deliver(engine,value,'wrong',1)
    result=deliver(engine,value,'first',1);assert deliver(engine,value,'first',1)==result
    with TestClient(create_app(settings)) as client:
        response=client.get('/api/v1/paper-requested/ownership',params={'account_id':'account'})
        assert response.status_code==200 and response.headers['cache-control']=='no-store'
        report=own.verify(response.json());assert report['event_tokens'][0]['token']==1
        assert report['leases'][0]['unexpired_at_observation']
        assert client.post('/api/v1/paper-requested/ownership',json={}).status_code==405


def test_expired_takeover_fences_old_owner_without_new_submission(database,monkeypatch):
    engine,_,_=database;req=prepared(engine);lease=own.claim(engine,'account',req['request_id'],'first')
    value,_=reviewed(engine,req,event(req,0,'SUBMIT'));result=deliver(engine,value,'first',1)
    monkeypatch.setattr(own,'clock',lambda db:lease['expires_us'])
    second=own.claim(engine,'account',req['request_id'],'second');assert second['token']==2
    with pytest.raises(ValueError):deliver(engine,value,'first',1)
    assert deliver(engine,value,'second',2)==result
    ack,_=reviewed(engine,req,event(req,1,'ACK'));deliver(engine,ack,'second',2)
    report=own.capture(engine,'account');assert own.verify(report)==report
    assert [v['token'] for v in report['event_tokens']]==[1,2] and len(report['claims'])==2
    assert journal.read(engine,'account')['revision']==3


@pytest.mark.parametrize('same',[False,True])
def test_concurrent_claims_have_one_durable_token(database,same):
    engine,_,_=database;req=prepared(engine);barrier=Barrier(2)
    def attempt(owner):
        barrier.wait(timeout=5)
        try:return own.claim(engine,'account',req['request_id'],owner)
        except ValueError:return None
    with ThreadPoolExecutor(2) as pool:results=list(pool.map(attempt,['first','first' if same else 'second']))
    assert sum(value is not None for value in results)==(2 if same else 1)
    assert len(own.capture(engine,'account')['claims'])==1


def test_expiry_during_event_processing_rolls_back_everything(database,monkeypatch):
    engine,_,_=database;req=prepared(engine);lease=own.claim(engine,'account',req['request_id'],'first')
    value,_=reviewed(engine,req,event(req,0,'SUBMIT'));before=sources.capture(engine,'account');count=0
    def now(db):
        nonlocal count
        count+=1
        return lease['acquired_us']+1 if count<=2 else lease['expires_us']
    monkeypatch.setattr(own,'clock',now)
    with pytest.raises(ValueError):deliver(engine,value,'first',1)
    assert sources.capture(engine,'account')==before


def test_cannot_enroll_old_submitted_history_or_other_account(database):
    engine,_,_=database;req=prepared(engine);value,_=reviewed(engine,req,event(req,0,'SUBMIT'));accept(engine,value)
    with pytest.raises(ValueError):own.claim(engine,'account',req['request_id'],'first')
    with pytest.raises(journal.MissingAccount):own.claim(engine,'missing',req['request_id'],'first')
    for owner,ttl in [('',30),('x',True),('x',0),('x',61)]:
        with pytest.raises(ValueError):own.claim(engine,'account',req['request_id'],owner,ttl)


@pytest.mark.parametrize('statement',["UPDATE requested_paper_claims SET token=token", "DELETE FROM requested_paper_claims", "UPDATE requested_paper_events SET ownership_token=NULL"])
def test_claims_and_accepted_fences_are_immutable(database,statement):
    engine,_,_=database;req=prepared(engine);own.claim(engine,'account',req['request_id'],'first')
    value,_=reviewed(engine,req,event(req,0,'SUBMIT'));deliver(engine,value,'first',1)
    with engine.begin() as conn:
        with pytest.raises(DBAPIError):conn.execute(text(statement))


@pytest.mark.parametrize('committed',[False,True])
def test_actual_sigkill_claim_before_commit_or_after_lost_response(database,committed):
    engine,_,_=database;req=prepared(engine)
    code='''
import json,sys,os,signal
from sqlalchemy import create_engine,event
from apps.api.settings import Settings
from core.paper import requested_ownership as own
value=json.load(sys.stdin);engine=create_engine(Settings().database_url.get_secret_value())
if not value['committed']:
 def kill(conn,cursor,statement,parameters,context,executemany):
  if statement.startswith('INSERT INTO requested_paper_claims'):os.kill(os.getpid(),signal.SIGKILL)
 event.listen(engine,'after_cursor_execute',kill)
own.claim(engine,'account',value['request_id'],'first',60)
os.kill(os.getpid(),signal.SIGKILL)
'''
    result=subprocess.run([sys.executable,'-c',code],input=json.dumps({'request_id':req['request_id'],'committed':committed}),capture_output=True,text=True,timeout=30)
    assert result.returncode==-signal.SIGKILL,result.stderr
    engine.dispose();report=own.capture(engine,'account');assert len(report['claims'])==int(committed)
    lease=own.claim(engine,'account',req['request_id'],'first',60);assert lease['token']==1
    assert len(own.capture(engine,'account')['claims'])==1


def test_missing_or_rehashed_claims_block_financial_reads(database):
    engine,_,_=database;req=prepared(engine);own.claim(engine,'account',req['request_id'],'first')
    value,_=reviewed(engine,req,event(req,0,'SUBMIT'));deliver(engine,value,'first',1)
    # Privileged isolated corruption bypasses the new dispatch->claim FK as well as immutability.
    with engine.begin() as conn:
        conn.execute(text('ALTER TABLE requested_paper_claims DISABLE TRIGGER ALL'))
        conn.execute(text('DELETE FROM requested_paper_claims'))
        conn.execute(text('ALTER TABLE requested_paper_claims ENABLE TRIGGER ALL'))
    with pytest.raises(ValueError):journal.read(engine,'account')


def test_offline_rehashed_tampering_and_cli(database,tmp_path,monkeypatch):
    engine,_,_=database;req=prepared(engine);own.claim(engine,'account',req['request_id'],'first')
    value,_=reviewed(engine,req,event(req,0,'SUBMIT'));deliver(engine,value,'first',1);report=own.capture(engine,'account')
    for change in [lambda r:r['claims'][0].update(token=2),lambda r:r['event_tokens'][0].update(token=True),lambda r:r['leases'][0].update(coverage='REMOTE_VERIFIED'),lambda r:r.update(resubmission_allowed=True),lambda r:r['event_tokens'][0].update(accepted_us=r['claims'][0]['expires_us']),lambda r:r['claims'][0].update(journal_revision=2)]:
        bad=deepcopy(report);change(bad);bad['sha256']=sha({k:v for k,v in bad.items() if k!='sha256'})
        with pytest.raises(ValueError):own.verify(bad)
    path=tmp_path/'ownership.json';path.write_text(json.dumps(report));monkeypatch.setenv('DATABASE_URL','postgresql+psycopg://invalid:invalid@127.0.0.1:1/no_db')
    result=subprocess.run([sys.executable,'-m','scripts.verify_requested_ownership',str(path)],capture_output=True,text=True,timeout=10)
    assert result.returncode==0,result.stderr


def test_owned_late_fill_after_stop_and_no_retroactive_control_bypass(database):
    from tests.test_requested_controls import command
    engine,_,_=database;req=prepared(engine);own.claim(engine,'account',req['request_id'],'first')
    value,_=reviewed(engine,req,event(req,0,'SUBMIT'));deliver(engine,value,'first',1)
    command(engine,'stop','STOP',2)
    value,_=reviewed(engine,req,fill(req,1));deliver(engine,value,'first',1)
    report=own.capture(engine,'account');assert own.verify(report)==report
    assert report['source_evidence']['journal']['journal']['account']['cash']=='909.10'
    assert report['source_evidence']['controls']['state']=='STOPPED'


def test_claim_insert_expiry_and_capacity_fail_without_financial_changes(database,monkeypatch):
    engine,_,_=database;req=prepared(engine);before=journal.read(engine,'account');times=iter([1000000,32000000])
    monkeypatch.setattr(own,'clock',lambda db:next(times))
    with pytest.raises(ValueError):own.claim(engine,'account',req['request_id'],'first')
    assert journal.read(engine,'account')==before
    with engine.connect() as conn:assert conn.scalar(text('SELECT count(*) FROM requested_paper_claims'))==0
    monkeypatch.setattr(own,'clock',lambda db:1000000);lease=own.claim(engine,'account',req['request_id'],'first')
    monkeypatch.setattr(own,'clock',lambda db:lease['expires_us']);monkeypatch.setattr(own,'CLAIM_LIMIT',1)
    with pytest.raises(ValueError):own.claim(engine,'account',req['request_id'],'second')
    assert journal.read(engine,'account')==before


def test_populated_ownership_downgrade_refused_and_history_preserved(database):
    from alembic import command
    from core.storage.schema import SCHEMA_REVISION
    engine,config,_=database;req=prepared(engine);own.claim(engine,'account',req['request_id'],'first')
    before=journal.read(engine,'account')
    with pytest.raises(RuntimeError,match='Cannot downgrade persisted ownership evidence'):command.downgrade(config,'0018')
    with engine.connect() as conn:assert conn.scalar(text('SELECT version_num FROM alembic_version'))==SCHEMA_REVISION
    assert journal.read(engine,'account')==before


@pytest.mark.parametrize('committed',[False,True])
def test_actual_sigkill_owned_delivery_before_commit_or_after_lost_ack(database,committed):
    engine,_,_=database;req=prepared(engine);own.claim(engine,'account',req['request_id'],'first',60)
    value,_=reviewed(engine,req,event(req,0,'SUBMIT'));before=sources.capture(engine,'account')
    code='''
import json,sys,os,signal
from sqlalchemy import create_engine,event
from apps.api.settings import Settings
from core.paper import requested_ownership as own
value=json.load(sys.stdin);engine=create_engine(Settings().database_url.get_secret_value())
if not value['committed']:
 def kill(conn,cursor,statement,parameters,context,executemany):
  if statement.startswith('INSERT INTO requested_paper_sources'):os.kill(os.getpid(),signal.SIGKILL)
 event.listen(engine,'after_cursor_execute',kill)
own.deliver(engine,value['proposal'],value['digest'],'first',1)
os.kill(os.getpid(),signal.SIGKILL)
'''
    result=subprocess.run([sys.executable,'-c',code],input=json.dumps({'proposal':{k:v for k,v in value.items() if k!='preview_sha256'},'digest':value['preview_sha256'],'committed':committed}),capture_output=True,text=True,timeout=30)
    assert result.returncode==-signal.SIGKILL,result.stderr
    engine.dispose()
    if not committed:assert sources.capture(engine,'account')==before
    one=deliver(engine,value,'first',1);assert deliver(engine,value,'first',1)==one
    report=own.capture(engine,'account');assert len(report['event_tokens'])==1 and own.verify(report)==report


@pytest.mark.parametrize('same',[False,True])
def test_concurrent_owned_submission_commits_one_event_and_fence(database,same):
    engine,_,_=database;req=prepared(engine);own.claim(engine,'account',req['request_id'],'first',60)
    one,_=reviewed(engine,req,event(req,0,'SUBMIT'));two=deepcopy(one)
    if not same:
        alternate=event(req,0,'SUBMIT');alternate['event_id']='different';two,_=reviewed(engine,req,alternate)
    barrier=Barrier(2)
    def attempt(value):
        barrier.wait(timeout=5)
        try:return deliver(engine,value,'first',1)
        except ValueError:return None
    with ThreadPoolExecutor(2) as pool:results=list(pool.map(attempt,[one,two]))
    assert sum(value is not None for value in results)==(2 if same else 1)
    report=own.capture(engine,'account');assert len(report['event_tokens'])==1
    assert journal.read(engine,'account')['revision']==2


def test_same_owner_reacquisition_still_fences_old_token_and_backward_clock(database,monkeypatch):
    engine,_,_=database;req=prepared(engine);first=own.claim(engine,'account',req['request_id'],'same')
    monkeypatch.setattr(own,'clock',lambda db:first['acquired_us']-1)
    with pytest.raises(ValueError):own.claim(engine,'account',req['request_id'],'same')
    monkeypatch.setattr(own,'clock',lambda db:first['expires_us'])
    second=own.claim(engine,'account',req['request_id'],'same');assert second['token']==2
    value,_=reviewed(engine,req,event(req,0,'SUBMIT'))
    with pytest.raises(ValueError):deliver(engine,value,'same',1)
    deliver(engine,value,'same',2)


def test_strict_delivery_and_no_control_override_or_post_void_work(database):
    from core.paper import requested_finalization as finalization
    from tests.test_requested_controls import command
    from tests.test_requested_finalization import time
    engine,_,_=database;req=prepared(engine);own.claim(engine,'account',req['request_id'],'first')
    value,_=reviewed(engine,req,event(req,0,'SUBMIT'))
    proposal={k:v for k,v in value.items() if k!='preview_sha256'}
    for changed,digest in [(dict(proposal,event=None),value['preview_sha256']),(proposal,True),(proposal,'wrong')]:
        with pytest.raises(ValueError):own.deliver(None,changed,digest,'first',1)
    command(engine,'stop','STOP',2)
    with pytest.raises(ValueError):deliver(engine,value,'first',1)
    finalization.finalize(engine,'account',req['request_id'],'void',1,time(2))
    with pytest.raises(ValueError):own.claim(engine,'account',req['request_id'],'first')
    with pytest.raises(ValueError):deliver(engine,value,'first',1)
    report=own.capture(engine,'account');assert own.verify(report)==report
    assert report['leases'][0]['active_request'] is False
    assert report['source_evidence']['journal']['journal']['requests'][0]['summary']['funding']['reserved_cash']=='0'
