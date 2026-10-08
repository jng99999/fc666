import pytest
from core.paper import requested_attempts as attempts,requested_dispatch as dispatch,requested_sources as sources
from tests.test_integration import database
from tests.test_requested_dispatch import submitted
from tests.test_requested_attempts import command


@pytest.mark.parametrize('operation',['capture','record','retry','export'])
def test_each_storage_path_binds_original_evidence(database,monkeypatch,operation):
    engine,_,_=database;req,_,_,_=submitted(engine);cmd=command(engine,req);attempts.record(engine,cmd)
    before=sources.capture(engine,'account');original=dispatch.snapshot
    def regressed(*args):
        report=original(*args)
        report['ownership_evidence']['observed_us']-=60000000
        return report
    # Independent observation binding must execute before accepting stored receipt/retry.
    monkeypatch.setattr(dispatch,'snapshot',regressed)
    with pytest.raises(ValueError):
        if operation=='capture':attempts.capture(engine,'account',req['request_id'])
        elif operation=='export':attempts.export(engine,'account',req['request_id'])
        elif operation=='record':attempts.record(engine,{**cmd,'attempt_id':'new'})
        else:attempts.record(engine,cmd)
    assert sources.capture(engine,'account')==before


def test_stored_size_budget_checked_before_loading_payloads(database,monkeypatch):
    engine,_,_=database;req,_,_,_=submitted(engine);cmd=command(engine,req);attempts.record(engine,cmd)
    from sqlalchemy.orm import Session
    original=Session.scalar
    def oversized(self,statement,*args,**kwargs):
        if 'octet_length(payload::text)' in str(statement):return attempts.MAX_BYTES+1
        return original(self,statement,*args,**kwargs)
    monkeypatch.setattr(Session,'scalar',oversized)
    for operation in [lambda:attempts.capture(engine,'account',req['request_id']),lambda:attempts.record(engine,cmd),lambda:attempts.export(engine,'account',req['request_id'])]:
        with pytest.raises(ValueError,match='Stored request assessments'):operation()


def test_insert_crossing_stored_budget_rolls_back(database,monkeypatch):
    from sqlalchemy.orm import Session
    from sqlalchemy import text
    engine,_,_=database;req,_,_,_=submitted(engine);cmd=command(engine,req)
    before=sources.capture(engine,'account');original=Session.scalar;checks=0
    def over_after_insert(self,statement,*args,**kwargs):
        nonlocal checks
        if 'octet_length(payload::text)' in str(statement):
            checks+=1
            if checks==2:return attempts.MAX_BYTES+1
        return original(self,statement,*args,**kwargs)
    monkeypatch.setattr(Session,'scalar',over_after_insert)
    with pytest.raises(ValueError,match='Stored request assessments'):attempts.record(engine,cmd)
    with engine.connect() as db:assert db.scalar(text('SELECT count(*) FROM requested_paper_attempts'))==0
    assert sources.capture(engine,'account')==before
