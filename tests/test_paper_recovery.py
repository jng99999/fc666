from copy import deepcopy
from datetime import datetime,timezone,timedelta
from uuid import uuid4
import pytest
from sqlalchemy.orm import Session
from fastapi.testclient import TestClient
from apps.api.main import create_app
from core.paper import streams,recovery
from core.storage.models import PaperStreamRecord,PaperWorkerRecord,CandleRecord,InstrumentRecord
from tests.test_integration import database
from tests.test_paper_streams import setup,arrive


def account(engine):
    value,bars=setup(engine);state=arrive(engine,value,bars,2)
    return value['session_id'],bars,state


def test_receipts_readonly_reconstruction_and_no_replay(database):
    engine,_,_=database;id,_,before=account(engine)
    report=recovery.capture(engine,id)
    assert recovery.verify(report)=={'state':'WORKER_UNCONFIRMED','orders':1,'fills':1}
    assert report['receipts'][0]['order']['order_id']==before['orders'][0]['order_id']
    assert report['receipts'][0]['fill']==before['fills'][0]
    assert report['pending_decision']['status']=='AWAIT_NEXT_CLOSED_BAR'
    assert streams.read(engine,id)==before and report['automatic_replay'] is False
    damaged=deepcopy(report);damaged['receipts']=[]
    with pytest.raises(ValueError):recovery.verify(damaged)


@pytest.mark.parametrize('kind',['source','missing','rules'])
def test_source_changes_need_review_without_blocking_mutation(database,kind):
    engine,_,_=database;id,bars,before=account(engine)
    with Session(engine) as db,db.begin():
        if kind=='rules':db.get(InstrumentRecord,bars[0].instrument_id).tick_size='0.02'
        else:
            row=db.get(CandleRecord,(bars[0].instrument_id,'1m',bars[0].open_time))
            if kind=='missing':db.delete(row)
            else:row.volume='1'
    report=recovery.capture(engine,id)
    assert report['recovery_state']=='NEEDS_REVIEW'
    assert streams.read(engine,id)==before
    recovery.verify(report)


@pytest.mark.parametrize('status,expected',[('RUNNING','WAITING_FOR_BAR'),('PAUSED','WAITING_FOR_BAR'),('STOPPED','TERMINAL_RETAINED'),('LIMIT_REACHED','TERMINAL_RETAINED'),('BLOCKED','BLOCKED_RETAINED')])
def test_state_precedence_and_worker(database,status,expected):
    engine,_,_=database;id,_,_=account(engine)
    with Session(engine) as db,db.begin():
        db.get(PaperStreamRecord,id).status=status
        db.add(PaperWorkerRecord(worker_id=str(uuid4()),heartbeat_at=datetime.now(timezone.utc)))
    report=recovery.capture(engine,id)
    assert report['account_status']==status and report['recovery_state']==expected
    assert report['automatic_replay'] is False


def test_future_input_and_size_fail_closed(database,monkeypatch):
    engine,_,_=database;id,_,_=account(engine);report=recovery.capture(engine,id)
    for field in ['worker_heartbeat','as_of']:
        source=deepcopy(report['inputs'])
        source[field]=(datetime.now(timezone.utc)+timedelta(days=1)).isoformat() if field=='worker_heartbeat' else '2020-01-01T00:00:00+00:00'
        with pytest.raises(ValueError):recovery.evaluate(source)
    monkeypatch.setattr(recovery,'MAX_BYTES',1)
    with pytest.raises(ValueError):recovery.capture(engine,id)


def test_api_missing_corrupt_and_readonly(database):
    engine,_,settings=database;id,_,_=account(engine)
    with TestClient(create_app(settings)) as client:
        path='/api/v1/paper/streams/'+id+'/recovery'
        assert client.get(path).status_code==200
        assert client.post(path,json={}).status_code==405
        assert client.get('/api/v1/paper/streams/'+str(uuid4())+'/recovery').status_code==404
        with Session(engine) as db,db.begin():db.get(PaperStreamRecord,id).snapshot={}
        assert client.get(path).status_code==409
