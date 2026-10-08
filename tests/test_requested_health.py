"""Real isolated PostgreSQL admission; generated market evidence is fixture-only."""
from copy import deepcopy
from datetime import datetime,timezone,timedelta
import json,subprocess,sys
from concurrent.futures import ThreadPoolExecutor
import pytest
from sqlalchemy import select,text,event as sql_event
from sqlalchemy.orm import Session
from sqlalchemy.exc import DBAPIError
from alembic import command as migration
from core.models import Instrument,Candle
from core.market_data.storage import save_instruments,save_candles
from core.paper import requested_health as health,requested_journal as journal,requested_controls as controls
from core.storage.models import RequestedPaperHealthGateRecord as Gate
from core.paper.fault_adapter import sha
from tests.test_market_quality import evidence,Cache
from tests.test_integration import database
from tests.test_requested_controls import LIMITS
from tests.test_requested_execution import BASE,STAMP,request,event,fill,cumulative


def ready(engine,account='account',enroll=True):
    value=evidence(datetime.now(timezone.utc));value['instrument']['min_notional']='1'
    save_instruments(engine,[Instrument.model_validate(value['instrument'])])
    save_candles(engine,[Candle.model_validate(bar) for bar in value['candles']])
    journal.create(engine,account,value['instrument']['instrument_id'],BASE,STAMP.isoformat())
    controls.enroll(engine,account,controls.policy(account,LIMITS),'enroll',STAMP.isoformat())
    controls.command(engine,account,'resume',1,'RESUME',STAMP.isoformat())
    if enroll:health.enroll(engine,account,2)
    return Cache([json.dumps(value['ticker']),json.dumps(value['book'])])


def gates(engine):
    with Session(engine) as db:return [deepcopy(g.payload) for g in db.scalars(select(Gate).order_by(Gate.phase))]


def test_health_prepare_submit_retry_and_late_fill_after_stop(database,tmp_path):
    engine,_,_=database;cache=ready(engine);req=request()
    initial=journal.prepare(engine,req,health_cache=cache);assert len(gates(engine))==1
    assert health.verify_gate(gates(engine)[0])==gates(engine)[0]
    cache.raw=[None,None]
    assert journal.prepare(engine,req)==initial
    before=journal.read(engine,'account')
    with pytest.raises(ValueError):journal.accept(engine,'account',req['request_id'],event(req,0,'SUBMIT'),health_cache=cache)
    assert journal.read(engine,'account')==before and len(gates(engine))==1
    value=evidence(datetime.now(timezone.utc));cache.raw=[json.dumps(value['ticker']),json.dumps(value['book'])]
    journal.accept(engine,'account',req['request_id'],event(req,0,'SUBMIT'),health_cache=cache)
    original=gates(engine);assert len(original)==2
    for value in original:assert health.verify_gate(value)==value
    path=tmp_path/'health.json';path.write_text(json.dumps(original[0]))
    assert subprocess.run([sys.executable,'-m','scripts.verify_requested_health_gate',str(path)],capture_output=True).returncode==0
    cache.raw=[None,None];controls.command(engine,'account','stop',2,'STOP',STAMP.isoformat())
    journal.accept(engine,'account',req['request_id'],event(req,0,'SUBMIT'))
    journal.accept(engine,'account',req['request_id'],fill(req,1,size='4',fee='3.60'))
    journal.accept(engine,'account',req['request_id'],cumulative(req,2,'4','3.60','360',seal=True))
    assert journal.read(engine,'account')['active_request_id'] is None and gates(engine)==original
    assert health.enroll(engine,'account',2)['enrolled_control_revision']==2


@pytest.mark.parametrize('failure',['no_provider','missing','stale','rules'])
def test_enrolled_prepare_denial_writes_no_request_gate_or_hold(database,failure):
    engine,_,_=database;cache=ready(engine);before=journal.read(engine,'account');req=request()
    if failure=='missing':cache.raw=[None,None]
    elif failure=='stale':
        value=json.loads(cache.raw[1]);value['event_time']=(datetime.now(timezone.utc)-timedelta(seconds=30)).isoformat();cache.raw[1]=json.dumps(value)
    elif failure=='rules':req=request(rules={**request()['rules'],'min_notional':'2'})
    with pytest.raises(ValueError):journal.prepare(engine,req,health_cache=None if failure=='no_provider' else cache)
    assert journal.read(engine,'account')==before and not gates(engine)
    with engine.connect() as db:
        assert db.scalar(text('SELECT count(*) FROM requested_paper_requests'))==0
        assert db.scalar(text('SELECT count(*) FROM requested_paper_gates'))==0

@pytest.mark.parametrize('committed',[False,True])
def test_sigkill_health_reservation_recovers_without_new_capture(database,committed):
    import os,signal
    from sqlalchemy import create_engine
    engine,_,_=database;cache=ready(engine)
    code='''import os,json,signal
from sqlalchemy import create_engine
from core.paper import requested_health as health,requested_journal as journal
from tests.test_requested_execution import request
from tests.test_market_quality import Cache
engine=create_engine(os.environ['FC666_HEALTH_DB'],hide_parameters=True)
if os.environ['FC666_HEALTH_COMMITTED']=='false':
    def die(*args):os.kill(os.getpid(),signal.SIGKILL)
    health.before_commit=die
journal.prepare(engine,request(),health_cache=Cache(json.loads(os.environ['FC666_HEALTH_CACHE'])))
os.kill(os.getpid(),signal.SIGKILL)
'''
    child=subprocess.run([sys.executable,'-c',code],capture_output=True,timeout=20,
        env={**os.environ,'FC666_HEALTH_DB':engine.url.render_as_string(hide_password=False),
             'FC666_HEALTH_CACHE':json.dumps(cache.raw),'FC666_HEALTH_COMMITTED':'true' if committed else 'false'})
    assert child.returncode==-signal.SIGKILL
    reopened=create_engine(engine.url)
    try:
        assert journal.read(reopened,'account')['revision']==int(committed)
        if committed:
            original=gates(reopened);journal.prepare(reopened,request());assert gates(reopened)==original
        else:journal.prepare(reopened,request(),health_cache=cache)
        assert len(gates(reopened))==1 and journal.read(reopened,'account')['revision']==1
    finally:reopened.dispose()

    with engine.connect() as db:
        assert db.scalar(text('SELECT count(*) FROM requested_paper_requests'))==1
        assert db.scalar(text('SELECT count(*) FROM requested_paper_gates'))==1


def test_health_insert_failure_rolls_back_all_financial_and_gate_facts(database):
    engine,_,_=database;cache=ready(engine);before=journal.read(engine,'account')
    def fail(conn,cursor,statement,*_):
        if statement.startswith('INSERT INTO requested_paper_health_gates'):raise RuntimeError('fault after health insert')
    sql_event.listen(engine,'after_cursor_execute',fail)
    try:
        with pytest.raises(RuntimeError):journal.prepare(engine,request(),health_cache=cache)
    finally:sql_event.remove(engine,'after_cursor_execute',fail)
    assert journal.read(engine,'account')==before and not gates(engine)


def test_expired_at_final_commit_checkpoint_rolls_back(database,monkeypatch):
    engine,_,_=database;cache=ready(engine);before=journal.read(engine,'account')
    original=health.before_commit
    def expire(db,value):
        # Advance only the final check, without waiting or fabricating persisted facts.
        from core.paper import requested_ownership
        real=requested_ownership.clock
        with monkeypatch.context() as shifted:
            shifted.setattr(requested_ownership,'clock',lambda db:real(db)+3_000_000)
            original(db,value)
    monkeypatch.setattr(health,'before_commit',expire)
    with pytest.raises(ValueError,match='stale'):journal.prepare(engine,request(),health_cache=cache)
    assert journal.read(engine,'account')==before and not gates(engine)


@pytest.mark.parametrize('target',['marker','policy','gate'])
def test_declared_evidence_is_immutable_and_missing_gate_blocks_read(database,target):
    engine,_,_=database;cache=ready(engine);journal.prepare(engine,request(),health_cache=cache)
    statement={'marker':"UPDATE requested_paper_accounts SET health_version=NULL",
               'policy':'DELETE FROM requested_paper_health_policies','gate':'DELETE FROM requested_paper_health_gates'}[target]
    with pytest.raises(DBAPIError):
        with engine.begin() as db:db.execute(text(statement))
    if target=='gate':
        with engine.begin() as db:
            db.execute(text('ALTER TABLE requested_paper_health_gates DISABLE TRIGGER requested_paper_health_gates_immutable'))
            db.execute(text('DELETE FROM requested_paper_health_gates'))
            db.execute(text('ALTER TABLE requested_paper_health_gates ENABLE TRIGGER requested_paper_health_gates_immutable'))
        with pytest.raises(ValueError,match='Incomplete'):journal.read(engine,'account')


def test_enrollment_checkpoint_legacy_compatibility_and_no_retrofit(database):
    engine,_,_=database;ready(engine,enroll=False)
    with pytest.raises(ValueError):health.enroll(engine,'account',True)
    with pytest.raises(ValueError):health.enroll(engine,'account',1)
    journal.prepare(engine,request())
    with pytest.raises(ValueError,match='untouched'):health.enroll(engine,'account',2)
    assert journal.read(engine,'account')['revision']==1


def test_empty_schema_roundtrip_and_populated_downgrade_refusal(database):
    engine,config,_=database;migration.downgrade(config,'0023');migration.upgrade(config,'head');migration.check(config)
    ready(engine)
    with pytest.raises(RuntimeError,match='persisted health'):migration.downgrade(config,'0023')
    assert journal.read(engine,'account')['revision']==0


def test_concurrent_retries_have_one_health_and_financial_reservation(database):
    engine,_,_=database;cache=ready(engine);req=request()
    with ThreadPoolExecutor(2) as pool:results=list(pool.map(lambda _:journal.prepare(engine,req,health_cache=cache),range(2)))
    assert results[0]==results[1] and len(gates(engine))==1 and journal.read(engine,'account')['revision']==1


def test_pool_health_gate_commits_with_pool_admission_and_never_bypasses(database):
    from core.paper import shared_capital_pool as pools,shared_capital_admission as admission,shared_capital as capital,requested_preview as preview
    engine,_,_=database;cache=ready(engine,'a');ready(engine,'b')
    identity=evidence()['instrument']['instrument_id']
    pools.create(engine,dict(pool_id='pool',quote_asset='USDT',opening_quote_cash='2000',max_reserved_quote='700',
        allocations=[dict(account_id=name,instrument_id=identity,opening_quote_cash='1000') for name in ['a','b']]))
    req=request(account_id='a');financial,controlled=controls.read(engine,'a')
    proposal={k:req[k] for k in capital.CANDIDATE_KEYS};proposal.update(expected_financial_revision=0,expected_control_revision=2)
    digest=preview.evaluate(financial,controlled,proposal)['sha256']
    with pytest.raises(ValueError,match='health capture'):admission.prepare(engine,'pool',proposal,digest)
    result=admission.prepare(engine,'pool',proposal,digest,health_cache=cache)
    assert len(gates(engine))==1
    assert admission.prepare(engine,'pool',proposal,digest)==result
    with engine.connect() as db:assert db.scalar(text('SELECT count(*) FROM paper_capital_admissions'))==1


@pytest.mark.parametrize('key,value',[('decision','ALLOW_REMOTE'),('external_submission_allowed',True),('checked_us',0)])
def test_resealed_false_health_gate_fails(database,key,value):
    engine,_,_=database;cache=ready(engine);journal.prepare(engine,request(),health_cache=cache)
    result=gates(engine)[0];result[key]=value;result['sha256']=sha({k:v for k,v in result.items() if k!='sha256'})
    with pytest.raises(ValueError):health.verify_gate(result)


def test_source_ownership_health_gate_source_and_dispatch_commit_together(database):
    from core.paper import requested_preview as preparation_preview,requested_preparation as preparation,requested_event_preview as preview,requested_ownership as ownership
    engine,_,_=database;cache=ready(engine);req=request()
    financial,controlled=controls.read(engine,'account')
    proposal={key:req[key] for key in preparation_preview.PROPOSAL_KEYS-{'expected_financial_revision','expected_control_revision'}}
    proposal.update(expected_financial_revision=0,expected_control_revision=2)
    digest=preparation_preview.evaluate(financial,controlled,proposal)['sha256']
    preparation.prepare(engine,proposal,digest,health_cache=cache)
    claim=ownership.claim(engine,'account',req['request_id'],'worker',ttl_seconds=30,
                          expected_financial_revision=1,expected_control_revision=2,expected_token=0)
    value={'account_id':'account','request_id':req['request_id'],'expected_financial_revision':1,'expected_control_revision':2,
           'source':{'kind':'LOCAL_PAPER_OPERATOR_INPUT','source_id':'fixture'},'event':event(req,0,'SUBMIT')}
    financial,controlled=controls.read(engine,'account');digest=preview.evaluate(financial,controlled,value)['sha256']
    with pytest.raises(ValueError,match='health capture'):ownership.deliver(engine,value,digest,'worker',claim['token'])
    assert journal.read(engine,'account')['total_events']==0 and len(gates(engine))==1
    result=ownership.deliver(engine,value,digest,'worker',claim['token'],health_cache=cache)
    assert ownership.deliver(engine,value,digest,'worker',claim['token'])==result
    with engine.connect() as db:
        for table in ['requested_paper_sources','requested_paper_events','requested_paper_dispatches']:
            assert db.scalar(text('SELECT count(*) FROM '+table))==1
    assert len(gates(engine))==2


def test_stored_health_budget_refusal_rolls_back_new_hold(database,monkeypatch):
    engine,_,_=database;cache=ready(engine);before=journal.read(engine,'account')
    monkeypatch.setattr(health,'MAX_STORED_BYTES',10)
    with pytest.raises(ValueError,match='Stored health evidence'):journal.prepare(engine,request(),health_cache=cache)
    assert journal.read(engine,'account')==before and not gates(engine)


def test_recent_report_cannot_hide_data_expiring_at_write_checkpoint():
    from core.market_data import quality
    value=evidence();stamp=(quality.clock(value['observed_at'])-timedelta(seconds=14.5)).isoformat()
    value['ticker']['received_at']=value['ticker']['event_time']=value['ticker']['payload']['event_time']=stamp
    report=quality.evaluate(value);assert report['status']=='healthy'
    checked=quality.clock(value['observed_at'])+timedelta(seconds=1)
    epoch=datetime(1970,1,1,tzinfo=timezone.utc);delta=checked-epoch
    checked_us=(delta.days*86400+delta.seconds)*1000000+delta.microseconds
    with pytest.raises(ValueError,match='Market data is unhealthy'):health.current_quality(report,checked_us)
