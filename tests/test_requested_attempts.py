from copy import deepcopy
import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from core.paper import requested_attempts as attempts, requested_dispatch as dispatch, requested_sources as sources, requested_ownership as own
from core.paper.fault_adapter import sha
from tests.test_integration import database
from tests.test_requested_dispatch import submitted


def command(engine,req):
    report=dispatch.capture(engine,'account');source=report['ownership_evidence']['source_evidence']
    return dict(account_id='account',request_id=req['request_id'],client_id=report['dispatches'][0]['dispatch']['client_id'],owner='first',ownership_token=1,attempt_id='local-failure-1',failure='TIMEOUT',expected_financial_revision=source['journal']['journal']['revision'],expected_control_revision=source['controls']['revision'])


def test_durable_attempt_exact_retry_and_immutable_evidence(database,tmp_path):
    engine,_,_=database;req,_,_,_=submitted(engine);cmd=command(engine,req);before=sources.capture(engine,'account')
    value=attempts.record(engine,cmd)
    assert attempts.verify(value)==value and not value['remote_send_performed']
    import json,subprocess,sys
    path=tmp_path/'attempt.json';path.write_text(json.dumps(value))
    result=subprocess.run([sys.executable,'-m','scripts.verify_requested_attempt',str(path)],capture_output=True,text=True,timeout=10)
    assert result.returncode==0,result.stderr
    path.write_text('{"version":"a","version":"b"}')
    assert subprocess.run([sys.executable,'-m','scripts.verify_requested_attempt',str(path)],capture_output=True,timeout=10).returncode!=0
    assert attempts.record(engine,cmd)==value
    assert attempts.capture(engine,'account',req['request_id'])==[value]
    assert sources.capture(engine,'account')==before
    for update in [dict(failure='EMPTY_RESULT'),dict(client_id='0'*64),dict(expected_financial_revision=999)]:
        with pytest.raises(ValueError):attempts.record(engine,{**cmd,**update})
    for sql in ['UPDATE requested_paper_attempts SET payload_sha256=payload_sha256','DELETE FROM requested_paper_attempts']:
        with pytest.raises(DBAPIError):
            with engine.begin() as db:db.execute(text(sql))
    forged=deepcopy(value);forged['remote_send_performed']=True;forged['sha256']=sha({k:v for k,v in forged.items() if k!='sha256'})
    with pytest.raises(ValueError):attempts.verify(forged)


def test_attempt_expiry_during_processing_rolls_back(database,monkeypatch):
    engine,_,_=database;req,lease,_,_=submitted(engine);cmd=command(engine,req)
    original=attempts.check;calls=0
    def expire(*args):
        nonlocal calls
        values=original(*args);calls+=1
        if calls==2:monkeypatch.setattr(own,'clock',lambda db:lease['expires_us'])
        return values
    monkeypatch.setattr(attempts,'check',expire)
    with pytest.raises(ValueError):attempts.record(engine,cmd)
    assert attempts.capture(engine,'account',req['request_id'])==[]


def test_attempt_takeover_fences_retries_and_preserves_identity(database,monkeypatch):
    engine,_,_=database;req,lease,_,_=submitted(engine);cmd=command(engine,req);first=attempts.record(engine,cmd)
    monkeypatch.setattr(own,'clock',lambda db:lease['expires_us'])
    own.claim(engine,'account',req['request_id'],'second',60)
    with pytest.raises(ValueError):attempts.record(engine,cmd)
    second=attempts.record(engine,{**cmd,'attempt_id':'local-failure-2','owner':'second','ownership_token':2})
    assert second['ordinal']==1 and second['command']['client_id']==first['command']['client_id']
    assert len(attempts.capture(engine,'account',req['request_id']))==2


def test_attempt_invalid_fences_precede_storage():
    for update in [dict(ownership_token=True),dict(expected_financial_revision='1'),dict(attempt_id=''),dict(failure='NOT_FOUND')]:
        cmd=dict(account_id='account',request_id='1'*64,client_id='2'*64,owner='first',ownership_token=1,attempt_id='one',failure='TIMEOUT',expected_financial_revision=2,expected_control_revision=2)
        with pytest.raises(ValueError):attempts.record(None,{**cmd,**update})


def test_populated_downgrade_refused_and_attempt_bound(database):
    from alembic import command as migration
    engine,config,_=database;req,_,_,_=submitted(engine);cmd=command(engine,req)
    for index in range(attempts.LIMIT):attempts.record(engine,{**cmd,'attempt_id':str(index)})
    with pytest.raises(ValueError):attempts.record(engine,{**cmd,'attempt_id':'over-limit'})
    assert len(attempts.capture(engine,'account',req['request_id']))==attempts.LIMIT
    with pytest.raises(RuntimeError):migration.downgrade(config,'0020')
    with engine.connect() as db:assert db.scalar(text('SELECT version_num FROM alembic_version'))=='0021'


def test_empty_attempt_migration_roundtrip(database):
    from alembic import command as migration
    engine,config,_=database
    migration.downgrade(config,'0020');migration.upgrade(config,'head')
    with engine.connect() as db:assert db.scalar(text('SELECT count(*) FROM requested_paper_attempts'))==0
