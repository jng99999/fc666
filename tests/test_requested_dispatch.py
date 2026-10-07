"""Immutable unique local dispatch and recovery, isolated PostgreSQL only."""
from copy import deepcopy
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
import json,subprocess,sys,signal
import pytest
from sqlalchemy import event as sql_event,text
from sqlalchemy.exc import DBAPIError
from fastapi.testclient import TestClient
from apps.api.main import create_app
from apps.api.settings import Settings
from core.paper import requested_dispatch as dispatch,requested_ownership as own,requested_journal as journal,requested_sources as sources
from core.paper.fault_adapter import sha
from tests.test_integration import database
from tests.test_requested_sources import prepared,reviewed,accept
from tests.test_requested_ownership import deliver
from tests.test_requested_execution import event,fill,cumulative
from tests.test_requested_controls import command

PATH='/api/v1/paper-requested/dispatches'


def submitted(engine):
    req=prepared(engine);lease=own.claim(engine,'account',req['request_id'],'first',60)
    value,_=reviewed(engine,req,event(req,0,'SUBMIT'));one=deliver(engine,value,'first',1)
    return req,lease,value,one


def test_unique_submission_boundary_and_unknown_recovery_are_read_only(database,tmp_path,monkeypatch):
    engine,_,settings=database;req,lease,value,one=submitted(engine)
    report=dispatch.capture(engine,'account');saved=report['dispatches'][0]['dispatch']
    assert saved['original_owner']=='first' and saved['original_token']==1 and report['recovery'][0]['action']=='WAIT_LOCAL_RESULT'
    assert saved['client_id']==sha({'version':dispatch.VERSION,'request_id':req['request_id']})
    assert deliver(engine,value,'first',1)==one
    unknown,_=reviewed(engine,req,event(req,1,'UNKNOWN_SUBMISSION'));deliver(engine,unknown,'first',1)
    before=sources.capture(engine,'account')
    with TestClient(create_app(settings)) as client:
        response=client.get(PATH,params={'account_id':'account'});assert response.status_code==200 and response.headers['cache-control']=='no-store'
        captured=dispatch.verify(response.json());assert captured['dispatches'][0]['dispatch']==saved
        assert captured['recovery'][0]['funding']['reserved_cash'].startswith('404.')
        assert not captured['recovery'][0]['resubmission_allowed'] and not captured['recovery'][0]['external_query_supported']
        assert client.post(PATH,json={'action':'retry'}).status_code==405
    assert sources.capture(engine,'account')==before
    path=tmp_path/'dispatch.json';path.write_text(json.dumps(captured));monkeypatch.setenv('DATABASE_URL','postgresql+psycopg://invalid:invalid@127.0.0.1:1/no_db')
    result=subprocess.run([sys.executable,'-m','scripts.verify_requested_dispatch',str(path)],capture_output=True,text=True,timeout=10)
    assert result.returncode==0,result.stderr


def test_takeover_stop_and_late_fill_keep_original_dispatch(database,monkeypatch):
    engine,_,_=database;req,lease,value,one=submitted(engine);saved=dispatch.capture(engine,'account')['dispatches'][0]['dispatch']
    monkeypatch.setattr(own,'clock',lambda db:lease['expires_us'])
    assert own.claim(engine,'account',req['request_id'],'second',60)['token']==2
    assert deliver(engine,value,'second',2)==one
    command(engine,'stop','STOP',2)
    late,_=reviewed(engine,req,fill(req,1));deliver(engine,late,'second',2)
    report=dispatch.capture(engine,'account');assert report['dispatches'][0]['dispatch']==saved
    assert report['recovery'][0]['action']=='RECONCILE_LOCAL_PREFIX' and report['recovery'][0]['funding']['reserved_cash'].startswith('303.')
    assert dispatch.verify(report)==report


@pytest.mark.parametrize('same',[False,True])
def test_concurrent_local_submissions_have_one_dispatch(database,same):
    engine,_,_=database;req=prepared(engine);own.claim(engine,'account',req['request_id'],'first',60)
    one,_=reviewed(engine,req,event(req,0,'SUBMIT'));two=deepcopy(one)
    if not same:
        other=event(req,0,'SUBMIT');other['event_id']='different';two,_=reviewed(engine,req,other)
    barrier=Barrier(2)
    def attempt(value):
        barrier.wait(timeout=5)
        try:return deliver(engine,value,'first',1)
        except ValueError:return None
    with ThreadPoolExecutor(2) as pool:results=list(pool.map(attempt,[one,two]))
    assert sum(value is not None for value in results)==(2 if same else 1)
    report=dispatch.capture(engine,'account');assert len(report['dispatches'])==1 and report['dispatches'][0]['dispatch'] is not None
    assert journal.read(engine,'account')['revision']==2


def test_exception_after_dispatch_insert_rolls_back_all_evidence(database):
    engine,_,_=database;req=prepared(engine);own.claim(engine,'account',req['request_id'],'first',60)
    value,_=reviewed(engine,req,event(req,0,'SUBMIT'));before=sources.capture(engine,'account')
    def fail(conn,cursor,statement,parameters,context,executemany):
        if statement.startswith('INSERT INTO requested_paper_dispatches'):raise RuntimeError('fixture dispatch failure')
    sql_event.listen(engine,'after_cursor_execute',fail)
    try:
        with pytest.raises(RuntimeError):deliver(engine,value,'first',1)
    finally:sql_event.remove(engine,'after_cursor_execute',fail)
    assert sources.capture(engine,'account')==before
    with engine.connect() as conn:assert conn.scalar(text('SELECT count(*) FROM requested_paper_dispatches'))==0
    deliver(engine,value,'first',1)


@pytest.mark.parametrize('committed',[False,True])
def test_actual_sigkill_before_dispatch_commit_or_after_lost_reply(database,committed):
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
  if statement.startswith('INSERT INTO requested_paper_dispatches'):os.kill(os.getpid(),signal.SIGKILL)
 event.listen(engine,'after_cursor_execute',kill)
own.deliver(engine,value['proposal'],value['digest'],'first',1)
os.kill(os.getpid(),signal.SIGKILL)
'''
    result=subprocess.run([sys.executable,'-c',code],input=json.dumps({'proposal':{k:v for k,v in value.items() if k!='preview_sha256'},'digest':value['preview_sha256'],'committed':committed}),capture_output=True,text=True,timeout=30)
    assert result.returncode==-signal.SIGKILL,result.stderr
    engine.dispose()
    if not committed:assert sources.capture(engine,'account')==before
    one=deliver(engine,value,'first',1);assert deliver(engine,value,'first',1)==one
    report=dispatch.capture(engine,'account');assert len(report['dispatches'])==1 and dispatch.verify(report)==report


@pytest.mark.parametrize('statement',["UPDATE requested_paper_dispatches SET payload_sha256=payload_sha256","DELETE FROM requested_paper_dispatches","UPDATE requested_paper_events SET dispatch_version=NULL"])
def test_dispatch_and_marker_are_immutable(database,statement):
    engine,_,_=database;submitted(engine)
    with engine.begin() as conn:
        with pytest.raises(DBAPIError):conn.execute(text(statement))


def test_missing_declared_dispatch_blocks_reads_and_no_repair(database):
    engine,_,settings=database;req,_,value,_=submitted(engine)
    with engine.begin() as conn:
        conn.execute(text('ALTER TABLE requested_paper_dispatches DISABLE TRIGGER USER'));conn.execute(text('DELETE FROM requested_paper_dispatches'));conn.execute(text('ALTER TABLE requested_paper_dispatches ENABLE TRIGGER USER'))
    with pytest.raises(ValueError):deliver(engine,value,'first',1)
    with TestClient(create_app(settings)) as client:
        for path in ['journal','sources','ownership','recovery','dispatches']:
            response=client.get('/api/v1/paper-requested/'+path,params={'account_id':'account'})
            assert response.status_code==409 and set(response.json())=={'detail'}


def test_legacy_owned_and_unowned_inputs_never_gain_dispatch_retroactively(database,monkeypatch):
    engine,_,_=database;req=prepared(engine);own.claim(engine,'account',req['request_id'],'first',60)
    value,_=reviewed(engine,req,event(req,0,'SUBMIT'))
    # Simulate the pre0020 immutable NULL declaration; there is no user bypass switch.
    monkeypatch.setattr(dispatch,'declare',lambda event,token:None)
    one=deliver(engine,value,'first',1);assert deliver(engine,value,'first',1)==one
    report=dispatch.capture(engine,'account');assert report['dispatches'][0]['dispatch'] is None
    assert report['recovery'][0]['action']=='DISPATCH_PROVENANCE_UNAVAILABLE' and dispatch.verify(report)==report


def test_rehashed_dispatch_tampering_is_rejected(database):
    engine,_,_=database;submitted(engine);report=dispatch.capture(engine,'account')
    for change in [lambda r:r['dispatches'][0]['dispatch'].update(client_id='0'*64),lambda r:r['dispatches'][0]['dispatch'].update(original_token=True),lambda r:r['dispatches'][0].update(dispatch=None),lambda r:r['dispatches'][0]['dispatch'].update(recorded_us=1),lambda r:r['dispatches'][0]['dispatch'].update(original_owner='fake'),lambda r:r['recovery'][0].update(resubmission_allowed=True),lambda r:r.update(dispatches=[]),lambda r:r['dispatches'][0]['dispatch'].update(mode='EXCHANGE_VERIFIED')]:
        bad=deepcopy(report);change(bad);bad['sha256']=sha({k:v for k,v in bad.items() if k!='sha256'})
        with pytest.raises(ValueError):dispatch.verify(bad)


def test_populated_dispatch_downgrade_refused(database):
    from alembic import command as alembic_command
    engine,config,_=database;submitted(engine);before=journal.read(engine,'account')
    with pytest.raises(RuntimeError,match='Cannot downgrade persisted local dispatch evidence'):alembic_command.downgrade(config,'0019')
    assert journal.read(engine,'account')==before


def test_dispatch_missing_validation_and_unavailable_responses_are_generic(database):
    engine,_,settings=database
    with TestClient(create_app(settings)) as client:
        response=client.get(PATH,params={'account_id':'missing'});assert response.status_code==404 and response.headers['cache-control']=='no-store'
        for params in [{},{'account_id':''},{'account_id':'x'*129}]:assert client.get(PATH,params=params).status_code==422
    with TestClient(create_app(Settings(database_url='postgresql+psycopg://invalid:invalid@127.0.0.1:1/no_db'))) as client:
        response=client.get(PATH,params={'account_id':'account'});assert response.status_code==503 and set(response.json())=={'detail'}


def test_full_fill_seal_and_later_request_preserve_original_client_identity(database):
    from tests.test_requested_execution import request
    engine,_,_=database;req,_,original,one=submitted(engine);saved=dispatch.capture(engine,'account')['dispatches'][0]['dispatch']
    for e in [fill(req,1,size='4',fee='3.60'),cumulative(req,2,'4','3.60','360',seal=True)]:
        value,_=reviewed(engine,req,e);deliver(engine,value,'first',1)
    financial=journal.read(engine,'account');next_req=request(client_request_id='next',base=financial['account'],created_at=financial['last_clock'])
    journal.prepare(engine,next_req);own.claim(engine,'account',next_req['request_id'],'second',60)
    next_event=event(next_req,0,'SUBMIT');next_event['received_at']=next_req['created_at']
    value,_=reviewed(engine,next_req,next_event);deliver(engine,value,'second',1)
    before=journal.read(engine,'account');assert deliver(engine,original,'first',1)==one
    report=dispatch.capture(engine,'account');assert dispatch.verify(report)==report
    assert report['dispatches'][0]['dispatch']==saved and report['dispatches'][1]['dispatch']['client_id']!=saved['client_id']
    assert report['recovery'][0]['action']=='LOCAL_SEALED' and report['recovery'][1]['action']=='WAIT_LOCAL_RESULT'
    assert journal.read(engine,'account')==before and before['active_request_id']==next_req['request_id']


def test_empty_pending_void_and_unowned_history_are_explicit(database):
    from core.paper import requested_finalization as finalization
    from tests.test_requested_finalization import time
    engine,_,_=database;req=prepared(engine)
    report=dispatch.capture(engine,'account');assert report['recovery'][0]['action']=='NOT_DISPATCHED_LOCAL'
    finalization.finalize(engine,'account',req['request_id'],'void',1,time(1))
    assert dispatch.capture(engine,'account')['recovery'][0]['action']=='LOCAL_VOIDED'
    journal.create(engine,'empty','fixture:spot',req['base'],time(0));assert dispatch.verify(dispatch.capture(engine,'empty'))['dispatches']==[]


def test_rehashed_persisted_dispatch_corruption_fails_and_bounds_are_complete(database,monkeypatch):
    engine,_,settings=database;submitted(engine);report=dispatch.capture(engine,'account')
    bad=deepcopy(report['dispatches'][0]['dispatch']);bad['original_owner']='changed'
    with engine.begin() as conn:
        conn.execute(text('ALTER TABLE requested_paper_dispatches DISABLE TRIGGER USER'))
        conn.execute(text('UPDATE requested_paper_dispatches SET payload=CAST(:payload AS JSON),payload_sha256=:hash'),{'payload':json.dumps(bad),'hash':sha(bad)})
        conn.execute(text('ALTER TABLE requested_paper_dispatches ENABLE TRIGGER USER'))
    with pytest.raises(ValueError):journal.read(engine,'account')
    with TestClient(create_app(settings)) as client:assert client.get(PATH,params={'account_id':'account'}).status_code==409
    monkeypatch.setattr(dispatch.sources.contract,'MAX_BYTES',100)
    with pytest.raises(ValueError):dispatch.verify(report)


def test_unowned_source_does_not_manufacture_dispatch_or_allow_retry(database):
    engine,_,_=database;req=prepared(engine);value,_=reviewed(engine,req,event(req,0,'SUBMIT'));accept(engine,value)
    report=dispatch.capture(engine,'account');assert report['dispatches'][0]['dispatch'] is None
    assert report['recovery'][0]['action']=='DISPATCH_PROVENANCE_UNAVAILABLE'
    assert not report['recovery'][0]['resubmission_allowed'] and dispatch.verify(report)==report


def test_dispatch_cli_rejects_duplicate_json_and_locked_export_is_generic(database,tmp_path):
    engine,_,settings=database;submitted(engine);report=dispatch.capture(engine,'account');path=tmp_path/'bad.json'
    path.write_text('{"version":"duplicate",'+json.dumps(report)[1:])
    result=subprocess.run([sys.executable,'-m','scripts.verify_requested_dispatch',str(path)],capture_output=True,text=True,timeout=10)
    assert result.returncode!=0
    before=journal.read(engine,'account')
    with engine.connect() as conn:
        tx=conn.begin();conn.execute(text("SELECT account_id FROM requested_paper_accounts WHERE account_id='account' FOR UPDATE"))
        with TestClient(create_app(settings)) as client:
            response=client.get(PATH,params={'account_id':'account'});assert response.status_code==503 and response.headers['cache-control']=='no-store'
        tx.rollback()
    assert journal.read(engine,'account')==before
