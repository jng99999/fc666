"""Controlled Paper accounts in isolated PostgreSQL fixtures only."""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from datetime import timedelta
from decimal import Decimal
import json
import subprocess
import sys
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import event as sql_event,text
from sqlalchemy.exc import DBAPIError
from apps.api.main import create_app
from core.paper import requested_controls as controls,requested_journal as journal,requested_inspection as inspection
from core.paper.fault_adapter import sha
from tests.test_integration import database
from tests.test_requested_execution import BASE,STAMP,request,event,fill,cumulative

LIMITS={'allowed_sides':['BUY','SELL'],'max_order_quantity':'10','max_request_notional':'1000',
        'max_buy_inventory_quantity':'10','max_fee_rate':'0.01','min_cash_after_buy':'0'}


def setup(engine,limits=None,base=None):
    journal.create(engine,'account','fixture:spot',base or BASE,STAMP.isoformat())
    policy=controls.policy('account',limits or LIMITS)
    controls.enroll(engine,'account',policy,'enroll',STAMP.isoformat())
    controls.command(engine,'account','resume',1,'RESUME',STAMP.isoformat())
    return policy


def command(engine,identity,action,seconds=0):
    _,view=controls.read(engine,'account')
    return controls.command(engine,'account',identity,view['revision'],action,(STAMP+timedelta(seconds=seconds)).isoformat())


def test_enrollment_pauses_and_never_changes_financial_revision(database):
    engine,_,_=database
    journal.create(engine,'account','fixture:spot',BASE,STAMP.isoformat());before=journal.read(engine,'account')
    policy=controls.policy('account',LIMITS)
    one=controls.enroll(engine,'account',policy,'enroll',STAMP.isoformat())
    assert one['state']=='PAUSED' and one['revision']==1
    assert journal.read(engine,'account')==before
    assert controls.enroll(engine,'account',policy,'enroll',STAMP.isoformat())==one
    with pytest.raises(ValueError):journal.prepare(engine,request())
    assert journal.read(engine,'account')==before
    command(engine,'resume','RESUME');journal.prepare(engine,request())
    view=controls.verify(controls.capture(engine,'account'))
    assert len(view['gates'])==1 and view['gates'][0]['phase']=='PREPARE'


@pytest.mark.parametrize('limit,value',[('max_order_quantity','3'),('max_request_notional','399'),('max_buy_inventory_quantity','3'),('max_fee_rate','0.009'),('min_cash_after_buy','597'),('allowed_sides',['SELL'])])
def test_full_requested_quantity_risk_denial_has_no_side_effect(database,limit,value):
    engine,_,_=database;setup(engine,{**LIMITS,limit:value})
    before=controls.capture(engine,'account')
    with pytest.raises(ValueError):journal.prepare(engine,request())
    assert controls.capture(engine,'account')==before


def test_pause_between_prepare_and_submit_denies_dispatch_but_exact_retries_work(database):
    engine,_,_=database;setup(engine);req=request();journal.prepare(engine,req)
    command(engine,'pause','PAUSE')
    before=controls.capture(engine,'account')
    with pytest.raises(ValueError):journal.accept(engine,'account',req['request_id'],event(req,0,'SUBMIT'))
    journal.prepare(engine,req)
    assert controls.capture(engine,'account')==before
    command(engine,'resume-again','RESUME')
    journal.accept(engine,'account',req['request_id'],event(req,0,'SUBMIT'))
    assert [g['phase'] for g in controls.verify(controls.capture(engine,'account'))['gates']]==['PREPARE','SUBMIT']


@pytest.mark.parametrize('action',['PAUSE','STOP'])
def test_pause_stop_cannot_block_late_fills_or_release_cancel_holds(database,action):
    engine,_,_=database;setup(engine);req=request();journal.prepare(engine,req)
    journal.accept(engine,'account',req['request_id'],event(req,0,'SUBMIT'))
    command(engine,'close',action)
    for value in [fill(req,1),event(req,2,'CANCEL_REQUEST'),event(req,3,'CANCEL_ACK'),fill(req,4,price='95',fee='0.95',identity='late')]:
        journal.accept(engine,'account',req['request_id'],value)
    view=journal.read(engine,'account')
    assert Decimal(view['account']['cash'])==Decimal('813.15')
    assert Decimal(view['requests'][0]['summary']['funding']['reserved_cash'])==202
    journal.accept(engine,'account',req['request_id'],cumulative(req,5,'2','1.85','185',seal=True))
    final=journal.read(engine,'account');assert final['active_request_id'] is None
    assert Decimal(final['requests'][0]['summary']['funding']['reserved_cash'])==0
    with pytest.raises(ValueError):journal.prepare(engine,request(client_request_id='new',base=final['account'],created_at=(STAMP+timedelta(seconds=6)).isoformat()))
    journal.accept(engine,'account',req['request_id'],event(req,0,'SUBMIT'))
    assert journal.read(engine,'account')==final
    if action=='STOP':
        with pytest.raises(ValueError):command(engine,'restart','RESUME',6)


def test_halt_denies_buys_but_allows_inventory_reducing_sell(database):
    engine,_,_=database;setup(engine,base={**BASE,'quantity':'4','cost_basis':'400'})
    command(engine,'halt','HALT')
    with pytest.raises(ValueError):journal.prepare(engine,request(base={**BASE,'quantity':'4','cost_basis':'400'}))
    req=request('SELL',quantity='4',price='100',base={**BASE,'quantity':'4','cost_basis':'400'})
    journal.prepare(engine,req);journal.accept(engine,'account',req['request_id'],event(req,0,'SUBMIT'))
    journal.accept(engine,'account',req['request_id'],fill(req,1,size='4',price='110',fee='4.4'))
    assert Decimal(journal.read(engine,'account')['account']['quantity'])==0


def test_concurrent_control_commands_fence_stale_revision(database):
    engine,_,_=database;setup(engine)
    def attempt(action):
        try:controls.command(engine,'account',action,2,action,STAMP.isoformat());return True
        except ValueError:return False
    with ThreadPoolExecutor(2) as pool:results=list(pool.map(attempt,['PAUSE','STOP']))
    assert sorted(results)==[False,True]
    assert controls.read(engine,'account')[1]['revision']==3


@pytest.mark.parametrize('phase',['enroll','command','prepare','submit'])
def test_failure_after_insert_rolls_back_marker_gate_and_effects(database,phase):
    engine,_,_=database
    journal.create(engine,'account','fixture:spot',BASE,STAMP.isoformat())
    if phase!='enroll':setup(engine)
    req=request()
    if phase=='submit':journal.prepare(engine,req)
    before=controls.capture(engine,'account')
    table={'enroll':'requested_paper_controls','command':'requested_paper_controls','prepare':'requested_paper_gates','submit':'requested_paper_gates'}[phase]
    def fail(conn,cursor,statement,parameters,context,executemany):
        if statement.startswith('INSERT INTO '+table):raise RuntimeError('Fixture failure after SQL insert')
    sql_event.listen(engine,'after_cursor_execute',fail)
    try:
        with pytest.raises(RuntimeError):
            if phase=='enroll':controls.enroll(engine,'account',controls.policy('account',LIMITS),'enroll',STAMP.isoformat())
            elif phase=='command':command(engine,'pause','PAUSE')
            elif phase=='prepare':journal.prepare(engine,req)
            else:journal.accept(engine,'account',req['request_id'],event(req,0,'SUBMIT'))
    finally:sql_event.remove(engine,'after_cursor_execute',fail)
    assert controls.capture(engine,'account')==before


def test_legacy_unmanaged_is_explicit_and_pending_history_cannot_be_enrolled(database):
    engine,_,_=database;journal.create(engine,'account','fixture:spot',BASE,STAMP.isoformat())
    view=controls.verify(controls.capture(engine,'account'))
    assert view['coverage']=='LEGACY_UNMANAGED' and view['state'] is None
    req=request();journal.prepare(engine,req)
    with pytest.raises(ValueError):controls.enroll(engine,'account',controls.policy('account',LIMITS),'enroll',STAMP.isoformat())
    assert inspection.verify(inspection.capture(engine,'account'))['revision']==1


@pytest.mark.parametrize('table',['requested_paper_policies','requested_paper_controls','requested_paper_gates'])
@pytest.mark.parametrize('action',['UPDATE','DELETE'])
def test_immutable_policy_control_and_gate_rows(database,table,action):
    engine,_,_=database;setup(engine);journal.prepare(engine,request())
    with engine.begin() as conn:
        with pytest.raises(DBAPIError):conn.execute(text(f'DELETE FROM {table}' if action=='DELETE' else f'UPDATE {table} SET payload_sha256=payload_sha256'))


def test_read_only_api_and_cli_verify_controls_without_changing_legacy_export(database,tmp_path,monkeypatch):
    engine,_,settings=database;setup(engine);req=request();journal.prepare(engine,req)
    legacy=inspection.capture(engine,'account')
    with TestClient(create_app(settings)) as client:
        path='/api/v1/paper-requested/controls'
        response=client.get(path,params={'account_id':'account'})
        assert response.status_code==200 and response.headers['cache-control']=='no-store'
        report=response.json();assert controls.verify(report)['state']=='ACTIVE'
        assert client.post(path,params={'account_id':'account'}).status_code==405
        assert client.get(path,params={'account_id':'absent'}).status_code==404
    assert inspection.capture(engine,'account')==legacy
    path=tmp_path/'controls.json';path.write_text(json.dumps(report))
    with monkeypatch.context() as patch:
        patch.setenv('DATABASE_URL','postgresql+psycopg://invalid:invalid@127.0.0.1:1/no_db')
        result=subprocess.run([sys.executable,'-m','scripts.verify_requested_controls',str(path)],capture_output=True,text=True,timeout=10)
    assert result.returncode==0 and 'ACTIVE' in result.stdout
    report['controls']['gates'][0]['decision']='DENY_LOCAL_PAPER'
    report['sha256']=sha({key:value for key,value in report.items() if key!='sha256'})
    with pytest.raises(ValueError):controls.verify(report)


def test_capacity_reserves_last_slot_for_stop_and_retries_still_work(database,monkeypatch):
    engine,_,_=database;setup(engine)
    monkeypatch.setattr(controls,'CONTROL_LIMIT',3)
    with pytest.raises(ValueError):command(engine,'pause','PAUSE')
    result=command(engine,'stop','STOP')
    assert result['state']=='STOPPED' and result['revision']==3
    assert controls.command(engine,'account','stop',2,'STOP',STAMP.isoformat())==result


@pytest.mark.parametrize('phase',['command','prepare'])
@pytest.mark.parametrize('committed',[False,True])
def test_actual_process_kill_preserves_atomic_control_or_admission(database,phase,committed):
    import signal
    engine,_,_=database;setup(engine);req=request()
    before=controls.capture(engine,'account')
    code='''
import json,os,signal,sys
from sqlalchemy import create_engine,event
from apps.api.settings import Settings
from core.paper import requested_controls as controls,requested_journal as journal
value=json.load(sys.stdin);engine=create_engine(Settings().database_url.get_secret_value())
if not value['committed']:
    def kill(conn,cursor,statement,parameters,context,executemany):
        table='requested_paper_controls' if value['phase']=='command' else 'requested_paper_gates'
        if statement.startswith('INSERT INTO '+table):os.kill(os.getpid(),signal.SIGKILL)
    event.listen(engine,'after_cursor_execute',kill)
if value['phase']=='command':controls.command(engine,'account','pause',2,'PAUSE',value['clock'])
else:journal.prepare(engine,value['request'])
os.kill(os.getpid(),signal.SIGKILL)
'''
    result=subprocess.run([sys.executable,'-c',code],input=json.dumps({'phase':phase,'committed':committed,'clock':STAMP.isoformat(),'request':req}),text=True,capture_output=True,timeout=30)
    assert result.returncode==-signal.SIGKILL,result.stderr
    engine.dispose();recovered=controls.capture(engine,'account')
    if not committed:assert recovered==before
    if phase=='command':
        controls.command(engine,'account','pause',2,'PAUSE',STAMP.isoformat())
        final=controls.verify(controls.capture(engine,'account'))
        assert final['revision']==3 and final['state']=='PAUSED'
        with pytest.raises(ValueError):journal.prepare(engine,req)
    else:
        journal.prepare(engine,req);final=controls.capture(engine,'account')
        assert final['journal']['journal']['revision']==1 and len(final['controls']['gates'])==1


def test_declared_missing_admission_blocks_read_submit_and_api(database):
    engine,_,settings=database;setup(engine);req=request();journal.prepare(engine,req)
    with engine.begin() as conn:
        conn.execute(text('ALTER TABLE requested_paper_gates DISABLE TRIGGER USER'))
        conn.execute(text('DELETE FROM requested_paper_gates'))
        conn.execute(text('ALTER TABLE requested_paper_gates ENABLE TRIGGER USER'))
    with pytest.raises(ValueError):journal.read(engine,'account')
    with pytest.raises(ValueError):journal.accept(engine,'account',req['request_id'],event(req,0,'SUBMIT'))
    with TestClient(create_app(settings)) as client:
        for path in ['/api/v1/paper-requested/journal','/api/v1/paper-requested/controls']:
            response=client.get(path,params={'account_id':'account'})
            assert response.status_code==409 and set(response.json())=={'detail'}


def test_control_clock_after_prepare_requires_fresh_submit_clock(database):
    engine,_,_=database;setup(engine);req=request();journal.prepare(engine,req)
    command(engine,'pause','PAUSE',1);command(engine,'resume-again','RESUME',2)
    with pytest.raises(ValueError):journal.accept(engine,'account',req['request_id'],event(req,0,'SUBMIT'))
    value=event(req,0,'SUBMIT');value['received_at']=(STAMP+timedelta(seconds=2)).isoformat()
    journal.accept(engine,'account',req['request_id'],value)
    assert controls.verify(controls.capture(engine,'account'))['gates'][-1]['control_revision']==4


def test_stop_before_submit_keeps_undispatched_hold_and_cannot_be_resumed(database):
    engine,_,_=database;setup(engine);req=request();journal.prepare(engine,req)
    before=journal.read(engine,'account')
    command(engine,'stop','STOP')
    with pytest.raises(ValueError):journal.accept(engine,'account',req['request_id'],event(req,0,'SUBMIT'))
    with pytest.raises(ValueError):command(engine,'resume-after-stop','RESUME')
    after=journal.read(engine,'account')
    assert after==before and Decimal(after['requests'][0]['summary']['funding']['reserved_cash'])==404
    assert controls.verify(controls.capture(engine,'account'))['state']=='STOPPED'


@pytest.mark.parametrize('update',[{'max_fee_rate':'1.1'},{'max_order_quantity':'0'},{'max_request_notional':'NaN'},
    {'max_buy_inventory_quantity':'-1'},{'min_cash_after_buy':1},{'allowed_sides':['BUY','BUY']},{'extra':'unexpected'}])
def test_invalid_explicit_policy_inputs_fail_before_storage(update):
    with pytest.raises((ValueError,ArithmeticError,TypeError)):controls.policy('account',{**LIMITS,**update})


def test_marker_and_policy_cannot_be_rewritten_or_reenrolled(database):
    engine,_,_=database;setup(engine)
    before=controls.capture(engine,'account')
    with pytest.raises(ValueError):controls.enroll(engine,'account',controls.policy('account',{**LIMITS,'max_order_quantity':'9'}),'enroll',STAMP.isoformat())
    with engine.begin() as conn:
        with pytest.raises(DBAPIError):conn.execute(text("UPDATE requested_paper_accounts SET control_version=NULL WHERE account_id='account'"))
    assert controls.capture(engine,'account')==before
