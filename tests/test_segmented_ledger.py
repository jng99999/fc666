from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from decimal import Decimal
from datetime import timedelta
import pytest
from sqlalchemy import text,event as sql_event
from sqlalchemy.exc import DBAPIError
from alembic import command as migration
from core.paper import segmented_ledger as ledger, requested_journal as frozen
from core.paper import requested_finalization
from tests.test_integration import database
from tests.test_requested_execution import BASE,STAMP,request,event,fill,cumulative
from tests.test_requested_segmented_execution import long_history

ID='segmented:account'


def opening():return frozen.opening(ID,'fixture:spot',BASE,STAMP.isoformat())


def command(view,identity,kind,payload):
    return dict(command_id=identity,kind=kind,expected_sha256=view['node_sha256'],expected_revision=view['journal']['revision'],payload=payload)


def setup(engine):
    root=ledger.create(engine,opening());req=request(account_id=ID)
    prepare=command(root,'prepare','PREPARE',req)
    return req,ledger.execute(engine,ID,prepare),root,prepare


def test_persist_more_than_frozen_limit_settles_and_reopens_without_old_account_writes(database):
    engine,_,_=database;req,current,root,prepare=setup(engine)
    _,batches=long_history()
    original=None
    for index,batch in enumerate(batches):
        for item in batch:item['request_id']=req['request_id']
        cmd=command(current,f'batch-{index}','APPEND',{'request_id':req['request_id'],'batch':batch})
        current=ledger.execute(engine,ID,cmd)
        if index==0:original=(cmd,deepcopy(current))
    assert current['journal']['total_events']==1003 and current['journal']['revision']==1004
    assert current['journal']['active_request_id'] is None
    assert Decimal(current['journal']['account']['cash'])==Decimal('636.4')
    assert Decimal(current['journal']['account']['quantity'])==4
    engine.dispose();assert ledger.read(engine,ID)==current
    assert ledger.execute(engine,ID,*original[:1])==original[1]
    assert ledger.execute(engine,ID,prepare)['journal']['revision']==1
    assert ledger.read(engine,ID)==current
    with engine.connect() as db:
        assert db.scalar(text('SELECT count(*) FROM requested_paper_accounts'))==0
        assert db.scalar(text('SELECT count(*) FROM requested_paper_events'))==0
    assert ledger.create(engine,opening())==current
    with pytest.raises(ValueError):ledger.create(engine,{**opening(),'base':{**BASE,'cash':'999'}})


def test_atomic_concurrency_retry_and_conflicting_command(database):
    engine,_,_=database;req,before,_,_=setup(engine)
    one=command(before,'submit-fill','APPEND',{'request_id':req['request_id'],'batch':[event(req,0,'SUBMIT'),fill(req,1)]})
    with ThreadPoolExecutor(2) as pool:results=list(pool.map(lambda _:ledger.execute(engine,ID,one),range(2)))
    assert results[0]==results[1] and ledger.read(engine,ID)['journal']['revision']==3
    changed=deepcopy(one);changed['payload']['batch'][1]['payload']['price']='95'
    with pytest.raises(ValueError,match='Conflicting command'):ledger.execute(engine,ID,changed)
    assert ledger.read(engine,ID)==results[0]


def test_distinct_concurrent_candidates_cannot_mix_or_double_reserve(database):
    engine,_,_=database;root=ledger.create(engine,opening())
    def attempt(name):
        value=request(account_id=ID,client_request_id=name)
        try:ledger.execute(engine,ID,command(root,name,'PREPARE',value));return True
        except ValueError:return False
    with ThreadPoolExecutor(2) as pool:result=list(pool.map(attempt,['a','b']))
    assert sorted(result)==[False,True]
    view=ledger.read(engine,ID)
    assert view['journal']['revision']==1 and len(view['journal']['requests'])==1
    assert Decimal(view['journal']['requests'][0]['summary']['funding']['reserved_cash'])==404


def test_insert_failure_rolls_back_financial_head_and_commit(database):
    engine,_,_=database;req,before,_,_=setup(engine)
    cmd=command(before,'batch','APPEND',{'request_id':req['request_id'],'batch':[event(req,0,'SUBMIT'),fill(req,1)]})
    def fail(conn,cursor,statement,parameters,context,executemany):
        if statement.startswith('INSERT INTO segmented_paper_commits'):raise RuntimeError('Injected failure')
    sql_event.listen(engine,'after_cursor_execute',fail)
    try:
        with pytest.raises(RuntimeError):ledger.execute(engine,ID,cmd)
    finally:sql_event.remove(engine,'after_cursor_execute',fail)
    assert ledger.read(engine,ID)==before
    assert ledger.execute(engine,ID,cmd)['journal']['revision']==3


def test_explicit_withdrawal_and_later_hold_are_persisted_and_old_retry_does_not_release(database):
    engine,_,_=database;req,before,root,prepare=setup(engine)
    receipt=requested_finalization.record(req,'withdraw',1,(STAMP+timedelta(seconds=1)).isoformat())
    withdrawn=ledger.execute(engine,ID,command(before,'withdraw','VOID',receipt))
    assert withdrawn['journal']['revision']==2
    assert Decimal(withdrawn['journal']['requests'][0]['summary']['funding']['reserved_cash'])==0
    second=request(account_id=ID,client_request_id='next',created_at=(STAMP+timedelta(seconds=2)).isoformat())
    current=ledger.execute(engine,ID,command(withdrawn,'next','PREPARE',second))
    assert Decimal(current['journal']['requests'][1]['summary']['funding']['reserved_cash'])==404
    assert ledger.execute(engine,ID,prepare)==before
    assert ledger.read(engine,ID)==current


def test_storage_and_count_exhaustion_preserve_existing_funding_and_allow_exact_retry(database,monkeypatch):
    engine,_,_=database;req,before,_,prepare=setup(engine)
    cmd=command(before,'append','APPEND',{'request_id':req['request_id'],'batch':[event(req,0,'SUBMIT')]})
    with engine.connect() as db:size=db.scalar(text('SELECT sum(octet_length(payload::text)) FROM segmented_paper_commits'))
    with monkeypatch.context() as bounded:
        bounded.setattr(ledger,'STORAGE_BYTES',size+1)
        with pytest.raises(ValueError,match='storage capacity'):ledger.execute(engine,ID,cmd)
    with monkeypatch.context() as bounded:
        bounded.setattr(ledger,'COMMITS',2)
        assert ledger.execute(engine,ID,prepare)==before
        with pytest.raises(ValueError,match='count capacity'):ledger.execute(engine,ID,cmd)
    assert ledger.read(engine,ID)==before


def test_immutable_evidence_head_guard_and_populated_downgrade(database):
    engine,config,_=database;_,before,_,_=setup(engine)
    for statement in ['DELETE FROM segmented_paper_commits','UPDATE segmented_paper_commits SET command_id=command_id','DELETE FROM segmented_paper_ledgers','UPDATE segmented_paper_ledgers SET financial_revision=financial_revision+1']:
        with pytest.raises(DBAPIError):
            with engine.begin() as db:db.execute(text(statement))
    with pytest.raises(ValueError,match='populated'):migration.downgrade(config,'0026')
    assert ledger.read(engine,ID)==before


@pytest.mark.parametrize('fault',['missing','cache'])
def test_missing_commit_and_corrupt_financial_cache_fail_reads_and_retries(database,fault):
    engine,_,_=database;req,before,_,prepare=setup(engine)
    with engine.begin() as db:
        if fault=='missing':
            db.execute(text('ALTER TABLE segmented_paper_commits DISABLE TRIGGER USER'))
            db.execute(text('DELETE FROM segmented_paper_commits WHERE commit_index=0'))
            db.execute(text('ALTER TABLE segmented_paper_commits ENABLE TRIGGER USER'))
        else:
            db.execute(text('ALTER TABLE segmented_paper_ledgers DISABLE TRIGGER USER'))
            db.execute(text("UPDATE segmented_paper_ledgers SET current=jsonb_set(current::jsonb,'{cash}',CAST(:value AS jsonb))::json"), {'value':'"999"'})
            db.execute(text('ALTER TABLE segmented_paper_ledgers ENABLE TRIGGER USER'))
    with pytest.raises(ValueError):ledger.read(engine,ID)
    with pytest.raises(ValueError):ledger.execute(engine,ID,prepare)


def test_nonpostgres_and_legacy_identity_are_rejected():
    from sqlalchemy import create_engine
    with pytest.raises(ValueError):ledger.create(create_engine('sqlite://'),opening())
    with pytest.raises(ValueError):ledger.create(create_engine('sqlite://'),frozen.opening('account','fixture:spot',BASE,STAMP.isoformat()))


@pytest.mark.parametrize('phase',['before','after'])
def test_process_kill_financial_commit_boundary_and_original_retry(database,tmp_path,phase):
    import selectors,signal,subprocess,sys,json
    engine,_,_=database;req,before,_,_=setup(engine)
    cmd=command(before,'append','APPEND',{'request_id':req['request_id'],'batch':[event(req,0,'SUBMIT'),fill(req,1)]})
    path=tmp_path/'command.json';path.write_text(json.dumps(cmd))
    code='''import json,os,signal,sys
from sqlalchemy import create_engine,event
from sqlalchemy.orm import Session
from apps.api.settings import Settings
from core.paper.segmented_ledger import execute
engine=create_engine(Settings().database_url.get_secret_value(),hide_parameters=True)
def pause(conn,cursor,statement,parameters,context,executemany):
 if statement.startswith('INSERT INTO segmented_paper_commits'):
  print('boundary',flush=True);os.kill(os.getpid(),signal.SIGSTOP)
def committed(session):
 print('boundary',flush=True);os.kill(os.getpid(),signal.SIGSTOP)
if sys.argv[2]=='before':event.listen(engine,'after_cursor_execute',pause)
else:event.listen(Session,'after_commit',committed)
execute(engine,'segmented:account',json.load(open(sys.argv[1])))
'''
    process=subprocess.Popen([sys.executable,'-c',code,str(path),phase],stdout=subprocess.PIPE,stderr=subprocess.PIPE)
    try:
        with selectors.DefaultSelector() as selector:
            selector.register(process.stdout,selectors.EVENT_READ)
            assert selector.select(timeout=10)
        assert process.stdout.readline().strip()==b'boundary'
        process.kill();process.wait(timeout=5)
        assert process.returncode==-signal.SIGKILL
    finally:
        if process.poll() is None:process.kill();process.wait(timeout=5)
    if phase=='before':assert ledger.read(engine,ID)==before
    else:assert ledger.read(engine,ID)['journal']['revision']==3
    final=ledger.execute(engine,ID,cmd)
    assert final['journal']['revision']==3
    assert Decimal(final['journal']['account']['cash'])==Decimal('909.1')
    assert ledger.execute(engine,ID,cmd)==final


def test_populated_backup_restore_replays_complete_commit_chain(database,tmp_path):
    from scripts.backup_drill import drill
    engine,_,_=database;req,before,_,_=setup(engine)
    after=ledger.execute(engine,ID,command(before,'append','APPEND',{'request_id':req['request_id'],'batch':[event(req,0,'SUBMIT'),fill(req,1)]}))
    def verify(restored):
        assert ledger.read(restored,ID)==after
        return {'segmented_ledger_equal':True,'commits':after['commit_index']+1,'financial_revision':after['journal']['revision']}
    result=drill(tmp_path/'restore',verify_restored=verify)
    assert result['additional_verification']['segmented_ledger_equal']
    assert ledger.read(engine,ID)==after
