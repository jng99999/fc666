"""Actual isolated process-loss and capacity acceptance for pooled commands."""
import json,os,signal,subprocess,sys
import pytest
from sqlalchemy import create_engine,text
from core.paper import shared_capital_admission as admission,shared_capital_pool as pools,requested_journal as journal
from tests.test_integration import database
from tests.test_shared_capital_admission import ready,proposed


@pytest.mark.parametrize('phase',['before_commit','after_commit'])
def test_sigkill_reopen_and_lost_reply_retry_reserve_once(database,phase):
    engine,_,_=database;ready(engine);proposal,digest=proposed(engine,'a')
    code='''import os,json,signal
from sqlalchemy import create_engine
from core.paper import shared_capital_admission as admission,requested_journal as journal
engine=create_engine(os.environ['FC666_POOL_FAULT_DB'])
if os.environ['FC666_POOL_FAULT_PHASE']=='before_commit':
    original=journal._prepare
    def die(*args,**kwargs):
        original(*args,**kwargs)
        os.kill(os.getpid(),signal.SIGKILL)
    journal._prepare=die
admission.prepare(engine,'pool',json.loads(os.environ['FC666_POOL_FAULT_PROPOSAL']),os.environ['FC666_POOL_FAULT_DIGEST'])
os.kill(os.getpid(),signal.SIGKILL)
'''
    environment={**os.environ,'FC666_POOL_FAULT_DB':engine.url.render_as_string(hide_password=False),'FC666_POOL_FAULT_PHASE':phase,
                 'FC666_POOL_FAULT_PROPOSAL':json.dumps(proposal),'FC666_POOL_FAULT_DIGEST':digest}
    child=subprocess.run([sys.executable,'-c',code],env=environment,capture_output=True,timeout=20)
    assert child.returncode==-signal.SIGKILL
    reopened=create_engine(engine.url)
    try:
        with reopened.connect() as db:
            expected=0 if phase=='before_commit' else 1
            for table in ('requested_paper_requests','requested_paper_gates','paper_capital_admissions'):
                assert db.scalar(text('SELECT count(*) FROM '+table))==expected
            original_receipt=db.scalar(text('SELECT payload FROM paper_capital_admissions'))
        capture=pools.capture(reopened,'pool')
        assert capture['preview']['reserved_quote'].startswith('0' if phase=='before_commit' else '404.')
        receipt=admission.prepare(reopened,'pool',proposal,digest)
        if original_receipt is not None:assert receipt==original_receipt
        assert admission.prepare(reopened,'pool',proposal,digest)==receipt
        assert admission.verify(receipt)==receipt
        assert journal.read(reopened,'a')['revision']==1
        with reopened.connect() as db:
            for table in ('requested_paper_requests','requested_paper_gates','paper_capital_admissions'):
                assert db.scalar(text('SELECT count(*) FROM '+table))==1
        assert pools.capture(reopened,'pool')['preview']['reserved_quote'].startswith('404.')
    finally:reopened.dispose()


def test_retention_cap_keeps_original_retries_without_evicting_evidence(database,monkeypatch):
    from datetime import timedelta
    from core.paper import requested_controls as controls,requested_preview as preview,requested_finalization as finalization,shared_capital as capital
    from tests.test_requested_execution import request,STAMP
    engine,_,_=database;ready(engine);monkeypatch.setattr(journal,'REQUEST_LIMIT',3)
    originals=[]
    for index in range(3):
        req=request(account_id='a',client_request_id='retained-'+str(index),requested_quantity='0.01',created_at=(STAMP+timedelta(seconds=index+1)).isoformat())
        financial,controlled=controls.read(engine,'a')
        proposal={key:req[key] for key in capital.CANDIDATE_KEYS};proposal.update(expected_financial_revision=financial['revision'],expected_control_revision=controlled['revision'])
        digest=preview.evaluate(financial,controlled,proposal)['sha256'];receipt=admission.prepare(engine,'pool',proposal,digest);originals.append((proposal,digest,receipt))
        current=journal.read(engine,'a')
        finalization.finalize(engine,'a',receipt['local_preview']['request']['request_id'],'void-'+str(index),current['revision'],req['created_at'],require_controlled=True)
    for proposal,digest,receipt in originals:assert admission.prepare(engine,'pool',proposal,digest)==receipt
    req=request(account_id='a',client_request_id='one-too-many',created_at=(STAMP+timedelta(seconds=4)).isoformat())
    proposal={key:req[key] for key in capital.CANDIDATE_KEYS};proposal.update(expected_financial_revision=6,expected_control_revision=2)
    with pytest.raises(ValueError):admission.prepare(engine,'pool',proposal,'0'*64)
    financial=journal.read(engine,'a');assert financial['revision']==6 and len(financial['requests'])==3
    assert financial['active_request_id'] is None
    assert pools.capture(engine,'pool')['preview']['reserved_quote']=='0'
    with engine.connect() as db:
        assert db.scalar(text('SELECT count(*) FROM paper_capital_admissions'))==3
        assert db.scalar(text('SELECT count(*) FROM requested_paper_requests'))==3


def test_revoked_whole_pool_scope_blocks_committed_receipt_retry(database):
    from fastapi.testclient import TestClient
    from apps.api.main import create_app
    from tests.test_shared_capital_api import configured,PREPARE
    from tests.test_requested_commands import HEADERS
    engine,_,settings=database;ready(engine);proposal,digest=proposed(engine,'a');receipt=admission.prepare(engine,'pool',proposal,digest)
    with TestClient(create_app(configured(settings,accounts=['a']))) as client:
        response=client.post(PREPARE,json={**proposal,'pool_id':'pool','local_preview_sha256':digest},headers=HEADERS)
        assert response.status_code==403 and response.json()=={'detail':'Explicit Paper pool scope denied'}
    assert admission.prepare(engine,'pool',proposal,digest)==receipt
    assert journal.read(engine,'a')['revision']==1


def test_stop_preserves_retry_and_hold_but_blocks_new_submit(database):
    from core.paper import requested_controls as controls
    from tests.test_requested_execution import STAMP,event
    engine,_,_=database;ready(engine);proposal,digest=proposed(engine,'a');receipt=admission.prepare(engine,'pool',proposal,digest)
    controls.command(engine,'a','stop',2,'STOP',STAMP.isoformat())
    assert admission.prepare(engine,'pool',proposal,digest)==receipt
    req=receipt['local_preview']['request']
    with pytest.raises(ValueError):journal.accept(engine,'a',req['request_id'],event(req,0,'SUBMIT'))
    assert pools.capture(engine,'pool')['preview']['reserved_quote'].startswith('404.')
    with engine.connect() as db:
        assert db.scalar(text('SELECT count(*) FROM requested_paper_events'))==0
        assert db.scalar(text('SELECT count(*) FROM paper_capital_admissions'))==1


def test_exhausted_source_history_cannot_create_an_unprocessable_hold(database,monkeypatch):
    from datetime import timedelta
    from core.paper import requested_controls as controls,requested_preview as preview,shared_capital as capital
    from tests.test_requested_execution import STAMP,request,event,fill,cumulative
    engine,_,_=database;ready(engine);monkeypatch.setattr(journal,'TOTAL_EVENTS',3)
    proposal,digest=proposed(engine,'a');receipt=admission.prepare(engine,'pool',proposal,digest);req=receipt['local_preview']['request']
    for ev in [event(req,0,'SUBMIT'),fill(req,1,size='4',fee='3.60'),cumulative(req,2,size='4',fees='3.60',notional='360',seal=True)]:journal.accept(engine,'a',req['request_id'],ev)
    financial,controlled=controls.read(engine,'a');assert financial['total_events']==3 and financial['active_request_id'] is None
    future=request(account_id='a',client_request_id='no-event-space',requested_quantity='0.01',base=financial['account'],created_at=(STAMP+timedelta(seconds=3)).isoformat())
    proposal={key:future[key] for key in capital.CANDIDATE_KEYS};proposal.update(expected_financial_revision=financial['revision'],expected_control_revision=controlled['revision'])
    with pytest.raises(ValueError,match='source history capacity'):preview.evaluate(financial,controlled,proposal)
    with pytest.raises(ValueError,match='source history capacity'):journal.prepare(engine,future)
    with pytest.raises(ValueError,match='source history capacity'):admission.prepare(engine,'pool',proposal,'0'*64)
    before=journal.read(engine,'a');assert before['revision']==4 and len(before['requests'])==1
    assert admission.prepare(engine,'pool',receipt['local_preview']['proposal'],digest)==receipt
    assert journal.read(engine,'a')==before
