from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from copy import deepcopy
import pytest
from sqlalchemy import text,event as sql_event
from sqlalchemy.exc import DBAPIError
from core.paper import shared_capital_admission as admission,shared_capital_pool as pools,shared_capital as capital,requested_controls as controls,requested_preview as preview,requested_journal as journal
from core.paper.fault_adapter import sha
from tests.test_integration import database
from tests.test_shared_capital_pool import opened
from tests.test_shared_capital import pool
from tests.test_requested_controls import LIMITS
from tests.test_requested_execution import STAMP,request,event


def ready(engine):
    opened(engine)
    for name in ['a','b']:
        controls.enroll(engine,name,controls.policy(name,LIMITS),'enroll-'+name,STAMP.isoformat())
        controls.command(engine,name,'resume-'+name,1,'RESUME',STAMP.isoformat())
    pools.create(engine,pool())


def proposed(engine,name):
    req=request(account_id=name);fin,ctrl=controls.read(engine,name)
    proposal={key:req[key] for key in capital.CANDIDATE_KEYS}
    proposal.update(expected_financial_revision=fin['revision'],expected_control_revision=ctrl['revision'])
    return proposal,preview.evaluate(fin,ctrl,proposal)['sha256']


def test_atomic_pool_hold_exact_retry_and_cap_denial(database,tmp_path):
    engine,_,_=database;ready(engine);a,digest=proposed(engine,'a');b,other=proposed(engine,'b')
    value=admission.prepare(engine,'pool',a,digest);assert admission.verify(value)==value
    assert admission.prepare(engine,'pool',a,digest)==value
    import json,subprocess,sys
    path=tmp_path/'admission.json';path.write_text(json.dumps(value))
    result=subprocess.run([sys.executable,'-m','scripts.verify_shared_capital_admission',str(path)],capture_output=True,text=True,timeout=10)
    assert result.returncode==0,result.stderr
    path.write_text('{"version":"a","version":"b"}')
    assert subprocess.run([sys.executable,'-m','scripts.verify_shared_capital_admission',str(path)],capture_output=True,timeout=10).returncode!=0
    with pytest.raises(ValueError):admission.prepare(engine,'pool',b,other)
    assert journal.read(engine,'b')['revision']==0
    assert pools.capture(engine,'pool')['preview']['reserved_quote'].startswith('404.')
    with engine.connect() as db:assert db.scalar(text('SELECT count(*) FROM paper_capital_admissions'))==1
    with pytest.raises(ValueError):journal.prepare(engine,request(account_id='b'))
    assert journal.read(engine,'b')['revision']==0
    forged=deepcopy(value);forged['pool_capture']['preview']['forecast']['projected_reserved_quote']='0';forged['sha256']=sha({k:v for k,v in forged.items() if k!='sha256'})
    with pytest.raises(ValueError):admission.verify(forged)


def test_simultaneous_accounts_cannot_both_cross_pool_cap(database):
    engine,_,_=database;ready(engine);proposals=[proposed(engine,name) for name in ['a','b']];barrier=Barrier(2)
    def prepare(values):
        barrier.wait(timeout=5)
        try:return admission.prepare(engine,'pool',*values)
        except ValueError:return None
    with ThreadPoolExecutor(max_workers=2) as executor:results=list(executor.map(prepare,proposals))
    assert sum(value is not None for value in results)==1
    assert pools.capture(engine,'pool')['preview']['reserved_quote'].startswith('404.')
    with engine.connect() as db:
        assert db.scalar(text('SELECT count(*) FROM requested_paper_requests'))==1
        assert db.scalar(text('SELECT count(*) FROM paper_capital_admissions'))==1


def test_insert_failure_rolls_back_request_gate_and_hold(database):
    engine,_,_=database;ready(engine);a,digest=proposed(engine,'a')
    def fail(conn,cursor,statement,parameters,context,executemany):
        if statement.startswith('INSERT INTO paper_capital_admissions'):raise RuntimeError('injected admission insert failure')
    sql_event.listen(engine,'before_cursor_execute',fail)
    try:
        with pytest.raises(RuntimeError):admission.prepare(engine,'pool',a,digest)
    finally:sql_event.remove(engine,'before_cursor_execute',fail)
    assert journal.read(engine,'a')['revision']==0
    with engine.connect() as db:
        assert db.scalar(text('SELECT count(*) FROM requested_paper_requests'))==0
        assert db.scalar(text('SELECT count(*) FROM requested_paper_gates'))==0
        assert db.scalar(text('SELECT count(*) FROM paper_capital_admissions'))==0


def test_missing_mandatory_admission_fails_every_journal_audit(database):
    engine,_,_=database;ready(engine);a,digest=proposed(engine,'a');admission.prepare(engine,'pool',a,digest)
    with engine.begin() as db:
        db.execute(text('ALTER TABLE paper_capital_admissions DISABLE TRIGGER ALL'))
        db.execute(text('DELETE FROM paper_capital_admissions'))
        db.execute(text('ALTER TABLE paper_capital_admissions ENABLE TRIGGER ALL'))
    with pytest.raises(ValueError):journal.read(engine,'a')
    with pytest.raises(ValueError):pools.capture(engine,'pool')
    with pytest.raises(ValueError):admission.prepare(engine,'pool',a,digest)


def test_shared_admission_immutable_and_populated_downgrade(database):
    from alembic import command
    engine,config,_=database;ready(engine);a,digest=proposed(engine,'a');admission.prepare(engine,'pool',a,digest)
    with pytest.raises(DBAPIError):
        with engine.begin() as db:db.execute(text('DELETE FROM paper_capital_admissions'))
    with pytest.raises(RuntimeError):command.downgrade(config,'0022')


def test_empty_admission_migration_roundtrip(database):
    from alembic import command
    engine,config,_=database;command.downgrade(config,'0022');command.upgrade(config,'head')
    with engine.connect() as db:assert db.scalar(text('SELECT count(*) FROM paper_capital_admissions'))==0


def test_upgrade_refuses_retrofit_of_existing_pool_request(database):
    from alembic import command
    from sqlalchemy.orm import Session
    from core.storage.models import RequestedPaperRequestRecord as Request,RequestedPaperAccountRecord as Account
    engine,config,_=database;opened(engine);pools.create(engine,pool());command.downgrade(config,'0022')
    req=request(account_id='a');summary=journal.contract.reduce(req,[])
    with Session(engine) as db,db.begin():
        db.add(Request(request_id=req['request_id'],account_id='a',client_request_id=req['client_request_id'],ordinal=0,payload=req,payload_sha256=sha(req)))
        account=db.get(Account,'a');account.current=summary['account'];account.active_request_id=req['request_id'];account.revision=1
    with pytest.raises(RuntimeError,match='retrofit'):command.upgrade(config,'head')
    with engine.connect() as db:assert db.scalar(text('SELECT version_num FROM alembic_version'))=='0022'


def test_later_fills_allow_other_account_without_duplicate_reservation(database):
    from tests.test_requested_execution import fill
    engine,_,_=database;ready(engine);a,digest=proposed(engine,'a');one=admission.prepare(engine,'pool',a,digest);req=one['local_preview']['request']
    journal.accept(engine,'a',req['request_id'],event(req,0,'SUBMIT'))
    journal.accept(engine,'a',req['request_id'],fill(req,1,size='2',fee='1.80'))
    assert admission.prepare(engine,'pool',a,digest)==one
    b,other=proposed(engine,'b');admission.prepare(engine,'pool',b,other)
    assert pools.capture(engine,'pool')['preview']['reserved_quote'].startswith('606.')
    with engine.connect() as db:assert db.scalar(text('SELECT count(*) FROM paper_capital_admissions'))==2


def test_post_insert_evidence_budget_rolls_back_preparation(database,monkeypatch):
    from sqlalchemy.orm import Session
    engine,_,_=database;ready(engine);a,digest=proposed(engine,'a');original=Session.scalar
    def overflow(self,statement,*args,**kwargs):
        if 'octet_length(payload::text)' in str(statement):
            existing=self.connection().execute(text('SELECT count(*) FROM paper_capital_admissions')).scalar()
            if existing:return admission.MAX_BYTES+1
        return original(self,statement,*args,**kwargs)
    monkeypatch.setattr(Session,'scalar',overflow)
    with pytest.raises(ValueError,match='Stored shared admission'):admission.prepare(engine,'pool',a,digest)
    assert journal.read(engine,'a')['revision']==0
    with engine.connect() as db:
        assert db.scalar(text('SELECT count(*) FROM paper_capital_admissions'))==0
        assert db.scalar(text('SELECT count(*) FROM requested_paper_requests'))==0
