from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from uuid import uuid4
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session
import pytest
from tests.test_integration import database
from tests.test_backtest import liquid,rules,cfg
from tests.test_market_integration import seed
from core.market_data.storage import save_candles
from core.research import jobs
from core.storage.models import ResearchJobRecord as Job
from apps.api.main import create_app
from scripts.reproduce_backtest import reproduce


def submit(engine):return jobs.enqueue(engine,{'config':cfg().model_dump(mode='json')},liquid([10,12,14,10,9]),rules())['job_id']

def test_job_claim_exclusive_and_result_survives_new_session(database):
    engine,_,_=database;job_id=submit(engine)
    owners=[str(uuid4()) for _ in range(4)]
    with ThreadPoolExecutor(max_workers=4) as pool:claimed=list(pool.map(lambda owner:jobs.claim(engine,owner),owners))
    assert claimed.count(job_id)==1 and claimed.count(None)==3
    owner=owners[claimed.index(job_id)]
    jobs.execute(engine,job_id,owner)
    with Session(engine) as session:
        task=session.get(Job,job_id)
        assert task.status=='SUCCEEDED' and task.progress==100 and task.attempts==1
        assert reproduce(task.result)==task.result['run_id']


def test_expired_lease_reclaim_fences_old_worker(database):
    engine,_,_=database;job_id=submit(engine);first=str(uuid4());second=str(uuid4())
    assert jobs.claim(engine,first)==job_id
    with Session(engine) as session,session.begin():session.get(Job,job_id).lease_until=jobs.now()-timedelta(seconds=1)
    assert jobs.claim(engine,second)==job_id
    with pytest.raises(jobs.LostLease):jobs.finish(engine,job_id,first,result={'run_id':'invalid'})
    jobs.execute(engine,job_id,second)
    with Session(engine) as session:
        task=session.get(Job,job_id);assert task.status=='SUCCEEDED' and task.attempts==2


def test_queued_and_running_cancellation_have_no_result(database):
    engine,_,_=database;queued=submit(engine)
    assert jobs.cancel(engine,queued)['status']=='CANCELLED'
    assert jobs.claim(engine,str(uuid4())) is None
    running=submit(engine);owner=str(uuid4());jobs.claim(engine,owner);jobs.cancel(engine,running)
    jobs.execute(engine,running,owner)
    with Session(engine) as session:
        task=session.get(Job,running);assert task.status=='CANCELLED' and task.result is None and task.progress<100
    assert jobs.cancel(engine,running)['status']=='CANCELLED'


def test_cancel_wins_before_success_commit(database):
    engine,_,_=database;job_id=submit(engine);owner=str(uuid4());jobs.claim(engine,owner)
    jobs.cancel(engine,job_id);jobs.finish(engine,job_id,owner,result={'run_id':'should not persist'})
    with Session(engine) as session:assert session.get(Job,job_id).result is None


def test_success_cannot_be_cancelled_retroactively(database):
    engine,_,_=database;job_id=submit(engine);owner=str(uuid4());jobs.claim(engine,owner);jobs.execute(engine,job_id,owner)
    with pytest.raises(ValueError):jobs.cancel(engine,job_id)


def test_altered_snapshot_or_request_is_failed_without_result(database):
    engine,_,_=database
    for attribute in ('snapshot','request'):
        job_id=submit(engine);owner=str(uuid4());jobs.claim(engine,owner)
        with Session(engine) as session,session.begin():
            task=session.get(Job,job_id);setattr(task,attribute,{**getattr(task,attribute),'tampered':True})
        jobs.execute(engine,job_id,owner)
        with Session(engine) as session:
            task=session.get(Job,job_id);assert task.status=='FAILED' and task.error and task.result is None


def test_shutdown_leaves_recoverable_lease_and_retry_limit(database):
    engine,_,_=database;job_id=submit(engine)
    for attempt in range(3):
        owner=str(uuid4());assert jobs.claim(engine,owner)==job_id
        jobs.execute(engine,job_id,owner,lambda:True)
        with Session(engine) as session,session.begin():
            task=session.get(Job,job_id);assert task.status=='RUNNING' and task.result is None
            task.lease_until=jobs.now()-timedelta(seconds=1)
    assert jobs.claim(engine,str(uuid4())) is None
    with Session(engine) as session:assert session.get(Job,job_id).status=='FAILED'


def test_bounded_active_queue_and_health(database):
    engine,_,_=database
    assert not jobs.healthy(engine)
    owner=str(uuid4());jobs.heartbeat(engine,owner);assert jobs.healthy(engine)
    for _ in range(jobs.ACTIVE_LIMIT):submit(engine)
    with pytest.raises(jobs.QueueFull):submit(engine)


def test_job_api_freezes_cutoff_results_and_rejects_bad_ids(database):
    engine,_,settings=database;seed(engine);save_candles(engine,liquid([10,12,14,10,9]))
    with TestClient(create_app(settings)) as client:
        response=client.post('/api/v1/research/jobs',json={'symbol':'BTCUSDT','timeframe':'1m','limit':5,'config':cfg().model_dump(mode='json')})
        assert response.status_code==202;task=response.json();job_id=task['job_id']
        assert task['request']['as_of'] and task['request']['strategy']=='ema_long_flat_v1'
        assert client.get(f'/api/v1/research/jobs/{job_id}/result').status_code==409
        owner=str(uuid4());jobs.claim(engine,owner);jobs.execute(engine,job_id,owner)
        result=client.get(f'/api/v1/research/jobs/{job_id}/result').json();assert reproduce(result)==result['run_id']
        assert client.get(f'/api/v1/research/jobs/{job_id}').json()['status']=='SUCCEEDED'
        assert client.post(f'/api/v1/research/jobs/{job_id}/cancel').status_code==409
        assert client.get('/api/v1/research/jobs/invalid').status_code==422
        assert client.get(f'/api/v1/research/jobs/{uuid4()}').status_code==404
        assert client.get('/api/v1/research/strategies').json()[0]['strategy']=='ema_long_flat_v1'
        assert client.post('/api/v1/research/jobs',json={'strategy':'custom_python'}).status_code==422

def test_cancel_during_actual_computation_discards_partial_outputs(database,monkeypatch):
    engine,_,_=database
    job_id=jobs.enqueue(engine,{'config':cfg().model_dump(mode='json')},liquid(list(range(10,70))),rules())['job_id']
    owner=str(uuid4());jobs.claim(engine,owner);original=jobs.simulate
    def computation(*args,checkpoint):
        def during(done,total):
            if done>=25:jobs.cancel(engine,job_id)
            checkpoint(done,total)
        return original(*args,checkpoint=during)
    monkeypatch.setattr(jobs,'simulate',computation)
    jobs.execute(engine,job_id,owner)
    with Session(engine) as session:
        task=session.get(Job,job_id);assert task.status=='CANCELLED' and task.result is None and task.progress<100


def test_enqueued_snapshot_not_changed_by_later_database_market_updates(database):
    from core.storage.models import InstrumentRecord
    from core.backtest.spot import simulate
    from apps.api.research import RunRequest,prepare
    engine,_,_=database;seed(engine);series=liquid([10,12,14,10,9]);save_candles(engine,series)
    bars,instrument,request=prepare(engine,RunRequest(symbol='BTCUSDT',timeframe='1m',limit=5,config=cfg()))
    expected=simulate(bars,instrument,cfg());job_id=jobs.enqueue(engine,request,bars,instrument)['job_id']
    with Session(engine) as session,session.begin():session.get(InstrumentRecord,instrument.instrument_id).tick_size='2'
    save_candles(engine,liquid([10,12,14,10,9,20])[5:])
    owner=str(uuid4());jobs.claim(engine,owner);jobs.execute(engine,job_id,owner)
    with Session(engine) as session:assert session.get(Job,job_id).result==expected
