from copy import deepcopy
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
import pytest
from sqlalchemy import text,event as sql_event
from sqlalchemy.exc import DBAPIError
from alembic import command as migration
from core.paper import shared_capital_pool as pools,shared_capital as capital,requested_journal as journal
from core.storage.schema import SCHEMA_REVISION
from core.paper.fault_adapter import sha
from tests.test_integration import database
from tests.test_shared_capital import pool,proposal
from tests.test_requested_execution import BASE,STAMP,request,event


def opened(engine):
    for name in ['a','b']:journal.create(engine,name,'BTCUSDT',BASE,STAMP.isoformat())


def test_immutable_exclusive_pool_capture_and_later_retry(database,tmp_path):
    engine,_,_=database;opened(engine);definition=pool();assert pools.create(engine,definition)==definition
    assert pools.create(engine,definition)==definition
    report=pools.capture(engine,'pool',proposal());assert pools.verify(report)==report
    assert report['coherent_capture_supported'] and report['exclusive_pool_membership_supported']
    import json,subprocess,sys
    path=tmp_path/'pool.json';path.write_text(json.dumps(report))
    result=subprocess.run([sys.executable,'-m','scripts.verify_shared_capital_pool',str(path)],capture_output=True,text=True,timeout=10)
    assert result.returncode==0,result.stderr
    path.write_text('{"version":"a","version":"b"}')
    assert subprocess.run([sys.executable,'-m','scripts.verify_shared_capital_pool',str(path)],capture_output=True,timeout=10).returncode!=0
    assert not report['submission_allowed'] and not report['shared_reservation_committed']
    from core.paper import requested_controls as controls,requested_preview as preview,shared_capital_admission as admission
    from tests.test_requested_controls import LIMITS
    controls.enroll(engine,'a',controls.policy('a',LIMITS),'enroll-a',STAMP.isoformat())
    controls.command(engine,'a','resume-a',1,'RESUME',STAMP.isoformat())
    financial,controlled=controls.read(engine,'a')
    candidate=request(account_id='a')
    local_proposal={key:candidate[key] for key in capital.CANDIDATE_KEYS}
    local_proposal.update(expected_financial_revision=financial['revision'],expected_control_revision=controlled['revision'])
    local=preview.evaluate(financial,controlled,local_proposal)
    admission.prepare(engine,'pool',local_proposal,local['sha256'])
    assert pools.create(engine,definition)==definition
    assert pools.capture(engine,'pool')['preview']['reserved_quote'].startswith('404.')
    with pytest.raises(ValueError):pools.create(engine,{**definition,'pool_id':'other'})
    with pytest.raises(ValueError):pools.create(engine,{**definition,'max_reserved_quote':'600'})
    for table in ('paper_capital_pools','paper_capital_members'):
        with pytest.raises(DBAPIError):
            with engine.begin() as db:db.execute(text('DELETE FROM '+table))
    forged=deepcopy(report);forged['membership'].pop();forged['sha256']=sha({k:v for k,v in forged.items() if k!='sha256'})
    with pytest.raises(ValueError):pools.verify(forged)


def test_cannot_retroactively_enroll_active_account(database):
    engine,_,_=database;opened(engine);journal.prepare(engine,request(account_id='a'))
    with pytest.raises(ValueError):pools.create(engine,pool())
    with engine.connect() as db:assert db.scalar(text('SELECT count(*) FROM paper_capital_pools'))==0


def test_concurrent_overlapping_pools_have_one_membership(database):
    engine,_,_=database;opened(engine);barrier=Barrier(2)
    def create(identity):
        barrier.wait(timeout=5)
        try:return pools.create(engine,{**pool(),'pool_id':identity})
        except ValueError:return None
    with ThreadPoolExecutor(max_workers=2) as executor:
        results=list(executor.map(create,['one','two']))
    assert sum(value is not None for value in results)==1
    with engine.connect() as db:
        assert db.scalar(text('SELECT count(*) FROM paper_capital_pools'))==1
        assert db.scalar(text('SELECT count(*) FROM paper_capital_members'))==2


def test_all_accounts_locked_before_any_audit(database,monkeypatch):
    engine,_,_=database;opened(engine);pools.create(engine,pool());seen=[];original_lock=journal.lock;original_audit=journal.audit
    def lock(db,account):
        value=original_lock(db,account);seen.append(account);return value
    def audit(db,row):
        assert seen==['a','b']
        return original_audit(db,row)
    monkeypatch.setattr(journal,'lock',lock);monkeypatch.setattr(journal,'audit',audit)
    pools.capture(engine,'pool')


def test_pool_capture_lock_timeout_returns_no_partial_result(database):
    engine,_,_=database;opened(engine);pools.create(engine,pool())
    with engine.begin() as db:
        db.execute(text("SELECT account_id FROM requested_paper_accounts WHERE account_id='b' FOR UPDATE"))
        with pytest.raises(DBAPIError):pools.capture(engine,'pool')
    assert pools.verify(pools.capture(engine,'pool'))


def test_missing_membership_fails_closed_without_repair(database):
    engine,_,_=database;opened(engine);pools.create(engine,pool())
    with engine.begin() as db:
        db.execute(text('ALTER TABLE paper_capital_members DISABLE TRIGGER ALL'))
        db.execute(text("DELETE FROM paper_capital_members WHERE account_id='b'"))
        db.execute(text('ALTER TABLE paper_capital_members ENABLE TRIGGER ALL'))
    with pytest.raises(ValueError):pools.capture(engine,'pool')
    with pytest.raises(ValueError):pools.create(engine,pool())


def test_populated_pool_downgrade_refused(database):
    engine,config,_=database;opened(engine);pools.create(engine,pool())
    with pytest.raises(RuntimeError):migration.downgrade(config,'0021')
    with engine.connect() as db:assert db.scalar(text('SELECT version_num FROM alembic_version'))==SCHEMA_REVISION


def test_empty_pool_migration_roundtrip(database):
    engine,config,_=database;migration.downgrade(config,'0021');migration.upgrade(config,'head')
    with engine.connect() as db:assert db.scalar(text('SELECT count(*) FROM paper_capital_pools'))==0


def test_membership_insert_failure_rolls_back_pool_definition(database):
    engine,_,_=database;opened(engine)
    def fail(conn,cursor,statement,parameters,context,executemany):
        if statement.startswith('INSERT INTO paper_capital_members'):raise RuntimeError('injected membership insert fault')
    sql_event.listen(engine,'before_cursor_execute',fail)
    try:
        with pytest.raises(RuntimeError):pools.create(engine,pool())
    finally:sql_event.remove(engine,'before_cursor_execute',fail)
    with engine.connect() as db:
        assert db.scalar(text('SELECT count(*) FROM paper_capital_pools'))==0
        assert db.scalar(text('SELECT count(*) FROM paper_capital_members'))==0
        assert db.scalar(text('SELECT count(*) FROM requested_paper_accounts'))==2
