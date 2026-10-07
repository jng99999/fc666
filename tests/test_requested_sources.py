"""Immutable local receipts in isolated PostgreSQL; no product event seed."""
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from copy import deepcopy
import json,subprocess,sys,signal
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import event as sql_event,text
from sqlalchemy.exc import SQLAlchemyError,DBAPIError
from apps.api.main import create_app
from apps.api.settings import Settings
from core.paper import requested_sources as sources,requested_event_preview as preview,requested_journal as journal,requested_controls as controls,requested_finalization as finalization
from core.paper.fault_adapter import sha
from tests.test_integration import database
from tests.test_requested_controls import setup,command
from tests.test_requested_commands import configured,HEADERS
from tests.test_requested_execution import request,event,fill,cumulative,BASE
from tests.test_requested_event_preview_api import body
from tests.test_requested_finalization import time

PATH='/api/v1/paper-requested/source-commands'


def prepared(engine):setup(engine);req=request();journal.prepare(engine,req);return req


def reviewed(engine,req,value):
    proposal=body(engine,req,value);report=preview.capture(engine,proposal);return {**proposal,'preview_sha256':report['sha256']},report


def accept(engine,value):return sources.accept(engine,{key:item for key,item in value.items() if key!='preview_sha256'},value['preview_sha256'])


def test_authentication_and_separate_ingestion_grant_precede_storage(monkeypatch):
    settings=Settings(database_url='postgresql+psycopg://invalid:invalid@127.0.0.1:1/no_db');req=request()
    value={'account_id':'account','request_id':req['request_id'],'expected_financial_revision':1,'expected_control_revision':2,'source':{'kind':'LOCAL_PAPER_OPERATOR_INPUT','source_id':'fixture'},'event':event(req,0,'SUBMIT'),'preview_sha256':'0'*64}
    def forbidden(*args,**kwargs):raise AssertionError('Denied request reached storage')
    monkeypatch.setattr(sources,'accept',forbidden)
    with TestClient(create_app(settings)) as client:assert client.post(PATH,json=value).status_code==503
    with TestClient(create_app(configured(settings,actions=['PREVIEW_EVENT']))) as client:
        assert client.post(PATH,json=value).status_code==401
        assert client.post(PATH,json=value,headers=HEADERS).status_code==403
    with TestClient(create_app(configured(settings,actions=['INGEST_EVENT']))) as client:
        assert client.post(PATH,json={**value,'account_id':'other'},headers=HEADERS).status_code==403
        for update in [{'expected_control_revision':True},{'preview_sha256':'wrong'},{'source':{'kind':'EXCHANGE_VERIFIED','source_id':'fixture'}}]:
            assert client.post(PATH,json={**value,**update},headers=HEADERS).status_code==422


def test_receipt_and_source_settlement_commit_once_and_export_offline(database,tmp_path,monkeypatch):
    engine,_,settings=database;req=prepared(engine);value,report=reviewed(engine,req,event(req,0,'SUBMIT'))
    with TestClient(create_app(configured(settings,actions=['INGEST_EVENT']))) as client:
        one=client.post(PATH,json=value,headers=HEADERS);assert one.status_code==200 and one.headers['cache-control']=='no-store'
        assert one.json()['accepted_receipt']==sources.receipt(report) and one.json()['event_committed']
        assert client.post(PATH,json=value,headers=HEADERS).json()==one.json()
        captured=client.get('/api/v1/paper-requested/sources',params={'account_id':'account'})
        assert captured.status_code==200 and sources.verify(captured.json())['sources'][0]['source_version']==sources.VERSION
    financial=journal.read(engine,'account');assert financial['revision']==2 and financial['account']==BASE and financial['version']==journal.VERSION
    assert set(financial['requests'][0])=={'request','events','summary'}
    path=tmp_path/'sources.json';path.write_text(json.dumps(captured.json()));monkeypatch.setenv('DATABASE_URL','postgresql+psycopg://invalid:invalid@127.0.0.1:1/no_db')
    result=subprocess.run([sys.executable,'-m','scripts.verify_requested_sources',str(path)],capture_output=True,text=True,timeout=10)
    assert result.returncode==0 and "'labeled': 1" in result.stdout,result.stderr


def test_unknown_hold_late_fill_after_stop_and_stable_old_ack(database):
    engine,_,settings=database;req=prepared(engine);submit,_=reviewed(engine,req,event(req,0,'SUBMIT'));one=accept(engine,submit)
    unknown,_=reviewed(engine,req,event(req,1,'UNKNOWN_SUBMISSION'));accept(engine,unknown)
    from decimal import Decimal
    assert Decimal(journal.read(engine,'account')['requests'][0]['summary']['funding']['reserved_cash'])==404
    command(engine,'stop','STOP',2);late,_=reviewed(engine,req,fill(req,2));accept(engine,late)
    before=sources.capture(engine,'account');assert accept(engine,submit)==one and sources.capture(engine,'account')==before
    view=journal.read(engine,'account');assert Decimal(view['account']['cash'])==Decimal('909.1') and Decimal(view['requests'][0]['summary']['funding']['reserved_cash'])==303
    assert sources.verify(before)['controls']['state']=='STOPPED' and len(before['sources'])==3


def test_changed_source_event_digest_or_checkpoint_has_no_side_effect(database):
    engine,_,settings=database;req=prepared(engine);value,_=reviewed(engine,req,event(req,0,'SUBMIT'));before=sources.capture(engine,'account')
    with TestClient(create_app(configured(settings,actions=['INGEST_EVENT']))) as client:
        for update in [{'preview_sha256':'0'*64},{'expected_financial_revision':2},{'expected_control_revision':3}]:
            assert client.post(PATH,json={**value,**update},headers=HEADERS).status_code==409
        assert sources.capture(engine,'account')==before
        assert client.post(PATH,json=value,headers=HEADERS).status_code==200
        before=sources.capture(engine,'account')
        changed=deepcopy(value);changed['source']['source_id']='different'
        changed_event=deepcopy(value);changed_event['event']['received_at']=time(1)
        for changed_value in [changed,changed_event,{**value,'preview_sha256':'0'*64},{**value,'expected_financial_revision':2}]:
            assert client.post(PATH,json=changed_value,headers=HEADERS).status_code==409
        assert sources.capture(engine,'account')==before


def test_unlabeled_internal_events_are_preserved_and_cannot_be_retagged(database):
    engine,_,settings=database;req=prepared(engine);value,_=reviewed(engine,req,event(req,0,'SUBMIT'));journal.accept(engine,'account',req['request_id'],value['event'])
    report=sources.capture(engine,'account');assert report['sources'][0]['source_version'] is None and report['sources'][0]['receipt'] is None
    assert sources.verify(report)['sources']==report['sources']
    with TestClient(create_app(configured(settings,actions=['INGEST_EVENT']))) as client:assert client.post(PATH,json=value,headers=HEADERS).status_code==409
    assert sources.capture(engine,'account')==report
    following,_=reviewed(engine,req,event(req,1,'ACK'));accept(engine,following)
    assert sources.verify(sources.capture(engine,'account'))['sources'][1]['receipt'] is not None


@pytest.mark.parametrize('same',[False,True])
def test_concurrent_source_inputs_have_one_economic_effect(database,same):
    engine,_,settings=database;req=prepared(engine);one,_=reviewed(engine,req,event(req,0,'SUBMIT'))
    second=event(req,0,'SUBMIT');second['event_id']='other';two=one if same else reviewed(engine,req,second)[0];barrier=Barrier(2)
    with TestClient(create_app(configured(settings,actions=['INGEST_EVENT']))) as client:
        def attempt(value):barrier.wait(timeout=5);return client.post(PATH,json=value,headers=HEADERS).status_code
        with ThreadPoolExecutor(2) as pool:results=list(pool.map(attempt,[one,two]))
    assert sorted(results)==([200,200] if same else [200,409])
    assert journal.read(engine,'account')['revision']==2 and len(sources.capture(engine,'account')['sources'])==1


def test_failure_after_receipt_insert_rolls_back_event_gate_and_money(database):
    engine,_,settings=database;req=prepared(engine);value,_=reviewed(engine,req,event(req,0,'SUBMIT'));before=sources.capture(engine,'account')
    def fail(conn,cursor,statement,parameters,context,executemany):
        if statement.startswith('INSERT INTO requested_paper_sources'):raise SQLAlchemyError('Injected post-source fault')
    sql_event.listen(engine,'after_cursor_execute',fail)
    try:
        with pytest.raises(SQLAlchemyError):accept(engine,value)
    finally:sql_event.remove(engine,'after_cursor_execute',fail)
    assert sources.capture(engine,'account')==before and journal.read(engine,'account')['revision']==1
    accept(engine,value)


@pytest.mark.parametrize('committed',[False,True])
def test_actual_sigkill_before_source_commit_or_after_lost_ack_recovers_once(database,committed):
    engine,_,settings=database;req=prepared(engine);value,_=reviewed(engine,req,event(req,0,'SUBMIT'));before=sources.capture(engine,'account')
    code='''
import json,os,signal,sys
from sqlalchemy import create_engine,event
from apps.api.settings import Settings
from core.paper import requested_sources as sources
value=json.load(sys.stdin);engine=create_engine(Settings().database_url.get_secret_value())
if not value['committed']:
 def kill(conn,cursor,statement,parameters,context,executemany):
  if statement.startswith('INSERT INTO requested_paper_sources'):os.kill(os.getpid(),signal.SIGKILL)
 event.listen(engine,'after_cursor_execute',kill)
sources.accept(engine,value['proposal'],value['digest'])
os.kill(os.getpid(),signal.SIGKILL)
'''
    result=subprocess.run([sys.executable,'-c',code],input=json.dumps({'proposal':{key:item for key,item in value.items() if key!='preview_sha256'},'digest':value['preview_sha256'],'committed':committed}),text=True,capture_output=True,timeout=30)
    assert result.returncode==-signal.SIGKILL,result.stderr
    engine.dispose()
    if not committed:assert sources.capture(engine,'account')==before
    one=accept(engine,value);assert accept(engine,value)==one and journal.read(engine,'account')['revision']==2


@pytest.mark.parametrize('statement',["UPDATE requested_paper_sources SET payload_sha256=payload_sha256","DELETE FROM requested_paper_sources","UPDATE requested_paper_events SET source_version=NULL"])
def test_receipts_and_event_declarations_are_immutable(database,statement):
    engine,_,settings=database;req=prepared(engine);value,_=reviewed(engine,req,event(req,0,'SUBMIT'));accept(engine,value)
    with engine.begin() as conn:
        with pytest.raises(DBAPIError):conn.execute(text(statement))


def test_missing_declared_receipt_blocks_all_exports_and_further_acceptance(database):
    engine,_,settings=database;req=prepared(engine);value,_=reviewed(engine,req,event(req,0,'SUBMIT'));accept(engine,value)
    with engine.begin() as conn:
        conn.execute(text('ALTER TABLE requested_paper_sources DISABLE TRIGGER USER'));conn.execute(text('DELETE FROM requested_paper_sources'));conn.execute(text('ALTER TABLE requested_paper_sources ENABLE TRIGGER USER'))
    with pytest.raises(ValueError):journal.read(engine,'account')
    with pytest.raises(ValueError):journal.accept(engine,'account',req['request_id'],event(req,1,'ACK'))
    with TestClient(create_app(settings)) as client:
        for path in ['journal','controls','sources']:
            response=client.get('/api/v1/paper-requested/'+path,params={'account_id':'account'});assert response.status_code==409 and set(response.json())=={'detail'}


def test_rehashed_source_export_tampering_cannot_override_historical_checkpoints(database):
    engine,_,settings=database;req=prepared(engine);value,_=reviewed(engine,req,event(req,0,'SUBMIT'));accept(engine,value);original=sources.capture(engine,'account')
    for change in [lambda r:r['sources'][0]['receipt'].update(expected_financial_revision=2),lambda r:r['sources'][0]['receipt'].update(expected_control_revision=True),
                   lambda r:r['sources'][0]['receipt'].update(source={'kind':'EXCHANGE_VERIFIED','source_id':'fixture'}),lambda r:r['sources'][0].update(receipt=None),
                   lambda r:r['sources'][0].update(source_version=None),lambda r:r['sources'][0]['receipt'].update(summary_sha256='0'*64)]:
        report=deepcopy(original);change(report);report['sha256']=sha({key:item for key,item in report.items() if key!='sha256'})
        with pytest.raises((ValueError,TypeError)):sources.verify(report)


def test_v2_void_prefix_and_completed_fill_receipts_preserve_old_ack_after_new_hold(database):
    engine,_,settings=database;old=prepared(engine);finalization.finalize(engine,'account',old['request_id'],'void',1,time(0))
    req=request(client_request_id='new');journal.prepare(engine,req)
    submit,_=reviewed(engine,req,event(req,0,'SUBMIT'));one=accept(engine,submit)
    for source in [fill(req,1,size='4',price='90',fee='3.60'),cumulative(req,2,'4','3.60','360',seal=True)]:
        value,_=reviewed(engine,req,source);accept(engine,value)
    next_req=request(client_request_id='next',base=journal.read(engine,'account')['account'],created_at=time(3));journal.prepare(engine,next_req)
    before=sources.capture(engine,'account');assert accept(engine,submit)==one and sources.capture(engine,'account')==before
    assert sources.verify(before)['journal']['journal']['version']==journal.VERSION_V2


def test_locked_missing_and_unavailable_source_routes_are_generic(database):
    engine,_,settings=database;req=prepared(engine);value,_=reviewed(engine,req,event(req,0,'SUBMIT'));before=sources.capture(engine,'account')
    with TestClient(create_app(configured(settings,accounts=['account','missing'],actions=['INGEST_EVENT']))) as client:
        assert client.post(PATH,json={**value,'account_id':'missing'},headers=HEADERS).status_code==404
        with engine.begin() as conn:
            conn.execute(text("SELECT account_id FROM requested_paper_accounts WHERE account_id='account' FOR UPDATE"))
            response=client.post(PATH,json=value,headers=HEADERS);assert response.status_code==503 and response.headers['cache-control']=='no-store'
    assert sources.capture(engine,'account')==before
    unavailable=Settings(database_url='postgresql+psycopg://invalid:invalid@127.0.0.1:1/no_db')
    with TestClient(create_app(configured(unavailable,actions=['INGEST_EVENT']))) as client:
        assert client.post(PATH,json=value,headers=HEADERS).status_code==503
        assert client.get('/api/v1/paper-requested/sources',params={'account_id':'account'}).status_code==503


def test_rehashed_persisted_receipt_corruption_blocks_financial_inspection(database):
    engine,_,settings=database;req=prepared(engine);value,_=reviewed(engine,req,event(req,0,'SUBMIT'));accept(engine,value)
    with engine.begin() as conn:
        payload=conn.execute(text('SELECT payload FROM requested_paper_sources')).scalar_one();payload['expected_control_revision']=99
        conn.execute(text('ALTER TABLE requested_paper_sources DISABLE TRIGGER USER'))
        conn.execute(text('UPDATE requested_paper_sources SET payload=CAST(:payload AS json),payload_sha256=:digest'),{'payload':json.dumps(payload),'digest':sha(payload)})
        conn.execute(text('ALTER TABLE requested_paper_sources ENABLE TRIGGER USER'))
    with pytest.raises(ValueError):journal.read(engine,'account')
    with TestClient(create_app(settings)) as client:
        assert client.get('/api/v1/paper-requested/journal',params={'account_id':'account'}).status_code==409


def test_schema_downgrade_refuses_to_discard_immutable_source_evidence(database):
    from alembic import command as alembic_command
    engine,config,settings=database;req=prepared(engine);value,_=reviewed(engine,req,event(req,0,'SUBMIT'));accept(engine,value);before=sources.capture(engine,'account')
    with pytest.raises(RuntimeError,match='Cannot downgrade persisted local source evidence'):alembic_command.downgrade(config,'0017')
    assert sources.capture(engine,'account')==before
    with engine.connect() as conn:assert conn.scalar(text('SELECT version_num FROM alembic_version'))=='0018'
