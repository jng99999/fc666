"""No fake submission evidence; isolated local Paper finalization fixtures."""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from datetime import timedelta
from decimal import Decimal
import json
import subprocess
import sys
from threading import Barrier
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import event as sql_event,text
from sqlalchemy.exc import DBAPIError
from core.paper import requested_finalization as finalization,requested_journal as journal,requested_inspection as inspection,requested_controls as controls
from core.paper.fault_adapter import sha
from apps.api.main import create_app
from tests.test_integration import database
from tests.test_requested_execution import BASE,STAMP,request,event
from tests.test_requested_controls import setup,command


def time(seconds):return (STAMP+timedelta(seconds=seconds)).isoformat()


def prepared(engine,side='BUY',base=None):
    setup(engine,base=base);req=request(side,base=base)
    journal.prepare(engine,req);return req


def close(engine,req,identity='void',revision=1,stamp=None):
    return finalization.finalize(engine,'account',req['request_id'],identity,revision,stamp or time(1))


def pure_view():
    seed=journal.opening('account','fixture:spot',BASE,time(0));req=request()
    void=finalization.record(req,'void',1,time(1));summary=finalization.summary(req,void,1,[])
    return inspection.replay(seed,[{'request':req,'events':[],'summary':summary,'void':void}],version=journal.VERSION_V2)


def test_local_void_summary_has_no_source_receipts_or_economic_credit():
    view=pure_view();summary=view['requests'][0]['summary']
    assert view['version']==journal.VERSION_V2 and view['revision']==2 and view['total_events']==0 and view['total_finalizations']==1
    assert view['account']==BASE and view['active_request_id'] is None
    assert summary['state']=='VOID_UNSUBMITTED' and summary['filled_quantity']=='0' and summary['unfilled_quantity']=='4'
    assert summary['local_unsubmitted_finalized'] and not summary['local_source_sealed'] and not summary['receipt_confirmed']
    assert summary['cancelled_quantity'] is None and summary['active_unfilled_quantity']=='0'
    assert summary['funding']=={'reserved_cash':'0','reserved_quantity':'0','available_cash':'1000','available_quantity':'0'}
    report=inspection.seal(view);assert report['version']==inspection.VERSION_V2 and inspection.verify(report)==view


@pytest.mark.parametrize('side',['BUY','SELL'])
def test_stopped_account_releases_only_undispatched_hold_and_keeps_state(database,side):
    engine,_,_=database;base=BASE if side=='BUY' else {**BASE,'quantity':'10','cost_basis':'100','realized_pnl':'-5','fees':'3'}
    req=prepared(engine,side,base);old=inspection.capture(engine,'account')
    command(engine,'stop','STOP',1)
    result=close(engine,req,stamp=time(2));view=journal.read(engine,'account')
    assert result['state']=='VOID_UNSUBMITTED' and view['account']==base
    assert view['revision']==2 and view['total_events']==0 and view['total_finalizations']==1
    assert view['requests'][0]['events']==[]
    assert controls.verify(controls.capture(engine,'account'))['state']=='STOPPED'
    assert controls.capture(engine,'account')['version']==controls.EXPORT_VERSION_V2
    assert inspection.verify(old)['version']==journal.VERSION and old['version']==inspection.VERSION
    with pytest.raises(ValueError):journal.accept(engine,'account',req['request_id'],event(req,0,'SUBMIT'))
    with pytest.raises(ValueError):journal.prepare(engine,request(client_request_id='new',base=base,created_at=time(3)))


def test_retry_cannot_release_a_subsequent_request_reservation(database):
    engine,_,_=database;req=prepared(engine);one=close(engine,req)
    next_req=request(client_request_id='next',created_at=time(2));journal.prepare(engine,next_req)
    before=journal.read(engine,'account')
    assert close(engine,req)==one and journal.prepare(engine,req)==one
    assert journal.read(engine,'account')==before
    assert before['revision']==3 and before['active_request_id']==next_req['request_id']
    assert Decimal(before['requests'][-1]['summary']['funding']['reserved_cash'])==404
    with pytest.raises(ValueError):close(engine,next_req,revision=3)
    assert journal.read(engine,'account')==before


@pytest.mark.parametrize('revision,stamp,identity',[(2,None,'void'),(True,None,'void'),(1,time(2),'void'),(1,None,'changed')])
def test_conflicting_finalization_retry_is_rejected(database,revision,stamp,identity):
    engine,_,_=database;req=prepared(engine);close(engine,req)
    before=journal.read(engine,'account')
    with pytest.raises(ValueError):close(engine,req,identity,revision,stamp)
    assert journal.read(engine,'account')==before


@pytest.mark.parametrize('unknown',[False,True])
def test_durable_submission_or_unknown_outcome_never_releases_hold(database,unknown):
    engine,_,_=database;req=prepared(engine)
    journal.accept(engine,'account',req['request_id'],event(req,0,'SUBMIT'))
    if unknown:journal.accept(engine,'account',req['request_id'],event(req,1,'UNKNOWN_SUBMISSION'))
    before=controls.capture(engine,'account');revision=before['journal']['journal']['revision']
    with pytest.raises(ValueError):close(engine,req,revision=revision,stamp=time(2))
    assert controls.capture(engine,'account')==before
    assert Decimal(before['journal']['journal']['requests'][0]['summary']['funding']['reserved_cash'])==404


def test_submit_and_finalization_race_has_exactly_one_durable_winner(database):
    engine,_,_=database;req=prepared(engine);barrier=Barrier(2)
    def attempt(kind):
        barrier.wait(timeout=5)
        try:
            if kind=='void':close(engine,req)
            else:journal.accept(engine,'account',req['request_id'],event(req,0,'SUBMIT'))
            return True
        except ValueError:return False
    with ThreadPoolExecutor(2) as pool:results=list(pool.map(attempt,['void','submit']))
    assert sorted(results)==[False,True]
    view=journal.read(engine,'account');assert view['revision']==2 and view['account']==BASE
    assert view['total_events']+view.get('total_finalizations',0)==1
    if view.get('total_finalizations'):
        assert view['active_request_id'] is None
    else:
        assert view['active_request_id']==req['request_id'] and view['requests'][0]['summary']['state']=='SUBMITTED'


def test_failure_after_void_insert_rolls_back_release_and_counter(database):
    engine,_,_=database;req=prepared(engine);before=controls.capture(engine,'account')
    def fail(conn,cursor,statement,parameters,context,executemany):
        if statement.startswith('INSERT INTO requested_paper_voids'):raise RuntimeError('After local evidence insert before commit')
    sql_event.listen(engine,'after_cursor_execute',fail)
    try:
        with pytest.raises(RuntimeError):close(engine,req)
    finally:sql_event.remove(engine,'after_cursor_execute',fail)
    assert controls.capture(engine,'account')==before
    assert close(engine,req)['state']=='VOID_UNSUBMITTED'


@pytest.mark.parametrize('committed',[False,True])
def test_actual_process_kill_before_or_after_void_commit_recovers_once(database,committed):
    import signal
    engine,_,_=database;req=prepared(engine);before=controls.capture(engine,'account')
    code='''
import json,os,signal,sys
from sqlalchemy import create_engine,event
from apps.api.settings import Settings
from core.paper import requested_finalization as finalization
value=json.load(sys.stdin);engine=create_engine(Settings().database_url.get_secret_value())
if not value['committed']:
    def kill(conn,cursor,statement,parameters,context,executemany):
        if statement.startswith('INSERT INTO requested_paper_voids'):os.kill(os.getpid(),signal.SIGKILL)
    event.listen(engine,'after_cursor_execute',kill)
finalization.finalize(engine,'account',value['request_id'],'void',1,value['clock'])
os.kill(os.getpid(),signal.SIGKILL)
'''
    result=subprocess.run([sys.executable,'-c',code],input=json.dumps({'committed':committed,'request_id':req['request_id'],'clock':time(1)}),capture_output=True,text=True,timeout=30)
    assert result.returncode==-signal.SIGKILL,result.stderr
    engine.dispose();recovered=controls.capture(engine,'account')
    if not committed:assert recovered==before
    close(engine,req);view=journal.read(engine,'account')
    assert view['revision']==2 and view['total_finalizations']==1 and view['account']==BASE


@pytest.mark.parametrize('action',['UPDATE','DELETE'])
def test_void_records_are_immutable(database,action):
    engine,_,_=database;req=prepared(engine);close(engine,req)
    with engine.begin() as conn:
        with pytest.raises(DBAPIError):conn.execute(text('DELETE FROM requested_paper_voids' if action=='DELETE' else 'UPDATE requested_paper_voids SET payload_sha256=payload_sha256'))


def test_missing_finalization_evidence_blocks_both_exports(database):
    engine,_,settings=database;req=prepared(engine);close(engine,req)
    with engine.begin() as conn:
        conn.execute(text('ALTER TABLE requested_paper_voids DISABLE TRIGGER USER'));conn.execute(text('DELETE FROM requested_paper_voids'));conn.execute(text('ALTER TABLE requested_paper_voids ENABLE TRIGGER USER'))
    with pytest.raises(ValueError):journal.read(engine,'account')
    with TestClient(create_app(settings)) as client:
        for path in ['/api/v1/paper-requested/journal','/api/v1/paper-requested/controls']:
            response=client.get(path,params={'account_id':'account'});assert response.status_code==409 and set(response.json())=={'detail'}


def test_api_and_offline_cli_dispatch_new_versions_without_mutation(database,tmp_path,monkeypatch):
    engine,_,settings=database;req=prepared(engine);close(engine,req);before=journal.read(engine,'account')
    with TestClient(create_app(settings)) as client:
        financial=client.get('/api/v1/paper-requested/journal',params={'account_id':'account'}).json()
        controlled=client.get('/api/v1/paper-requested/controls',params={'account_id':'account'}).json()
        assert inspection.verify(financial)==before and controls.verify(controlled)['state']=='ACTIVE'
        assert client.post('/api/v1/paper-requested/journal',params={'account_id':'account'}).status_code==405
    assert journal.read(engine,'account')==before
    with monkeypatch.context() as patch:
        patch.setenv('DATABASE_URL','postgresql+psycopg://invalid:invalid@127.0.0.1:1/no_db')
        for report,module in [(financial,'scripts.verify_requested_journal'),(controlled,'scripts.verify_requested_controls')]:
            path=tmp_path/(module+'.json');path.write_text(json.dumps(report))
            result=subprocess.run([sys.executable,'-m',module,str(path)],capture_output=True,text=True,timeout=10)
            assert result.returncode==0,result.stderr


@pytest.mark.parametrize('mutation',[
    lambda v:v.update(total_finalizations=0),lambda v:v['requests'][0]['void'].update(expected_revision=2),
    lambda v:v['requests'][0]['void'].update(source_events=False),lambda v:v['requests'][0]['void'].update(proof='EXCHANGE_CANCELLED'),
    lambda v:v['requests'][0]['summary']['account'].update(cash='1404'),lambda v:v['requests'][0].update(void=None),
    lambda v:v['requests'][0]['events'].append(event(v['requests'][0]['request'],0,'SUBMIT')),
    lambda v:v['requests'][0]['void'].update(created_at=time(-1)),
])
def test_rehashed_void_tampering_cannot_bypass_offline_replay(mutation):
    report=inspection.seal(pure_view());mutation(report['journal'])
    report['sha256']=sha({key:value for key,value in report.items() if key!='sha256'})
    with pytest.raises((ValueError,TypeError,KeyError)):inspection.verify(report)


def test_v1_export_cannot_carry_v2_facts_or_use_wrong_header():
    report=inspection.seal(pure_view());report['version']=inspection.VERSION
    report['sha256']=sha({key:value for key,value in report.items() if key!='sha256'})
    with pytest.raises(ValueError):inspection.verify(report)
    view=pure_view();view['version']=journal.VERSION;view.pop('total_finalizations')
    with pytest.raises(ValueError):inspection.seal(view)


def test_legacy_enrollment_after_void_uses_new_financial_checkpoint(database):
    from tests.test_requested_controls import LIMITS
    engine,_,_=database
    journal.create(engine,'account','fixture:spot',BASE,time(0))
    req=request();journal.prepare(engine,req);close(engine,req)
    controls.enroll(engine,'account',controls.policy('account',LIMITS),'enroll',time(2))
    command(engine,'resume','RESUME',2)
    next_req=request(client_request_id='next',created_at=time(3));journal.prepare(engine,next_req)
    source=event(next_req,0,'SUBMIT');source['received_at']=time(3)
    journal.accept(engine,'account',next_req['request_id'],source)
    financial=inspection.verify(inspection.capture(engine,'account'))
    controlled=controls.verify(controls.capture(engine,'account'))
    assert financial['revision']==4 and financial['total_events']==1 and financial['total_finalizations']==1
    assert financial['requests'][1]['void'] is None
    assert len(controlled['gates'])==2 and controlled['state']=='ACTIVE'


def test_finalization_clock_cannot_precede_latest_control(database):
    engine,_,_=database;req=prepared(engine)
    old=controls.capture(engine,'account');command(engine,'pause','PAUSE',3)
    before=controls.capture(engine,'account')
    with pytest.raises(ValueError):close(engine,req,stamp=time(2))
    assert controls.capture(engine,'account')==before
    close(engine,req,stamp=time(3))
    assert controls.verify(controls.capture(engine,'account'))['state']=='PAUSED'
    assert controls.verify(old)['state']=='ACTIVE' and old['version']==controls.EXPORT_VERSION


def test_concurrent_exact_finalization_retries_have_one_effect(database):
    engine,_,_=database;req=prepared(engine);barrier=Barrier(2)
    def attempt(_):
        barrier.wait(timeout=5);return close(engine,req)
    with ThreadPoolExecutor(2) as pool:results=list(pool.map(attempt,range(2)))
    assert results[0]==results[1]
    view=journal.read(engine,'account')
    assert view['revision']==2 and view['total_finalizations']==1 and view['total_events']==0
