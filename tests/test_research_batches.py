from copy import deepcopy
from uuid import uuid4
from concurrent.futures import ThreadPoolExecutor
from fastapi.testclient import TestClient
from sqlalchemy import select,func
from sqlalchemy.orm import Session
import pytest
from apps.api.main import create_app
from core.research import jobs,batches
from core.storage.models import ResearchJobRecord as Job,InstrumentRecord
from core.market_data.storage import save_candles
from tests.test_integration import database
from tests.test_market_integration import seed
from tests.test_backtest import liquid,rules,cfg
from scripts.reproduce_backtest import reproduce


def request():
    return {'base':{'symbol':'BTCUSDT','timeframe':'1m','limit':5,'config':cfg().model_dump(mode='json')},
            'variants':[{'strategy':'ema_long_flat_v1','parameters':{'period':2}},
                        {'strategy':'sma_long_flat_v1','parameters':{'fast':2,'slow':3}}]}


def setup(engine):
    seed(engine);save_candles(engine,liquid([10,12,14,10,9]))


def test_batch_fixed_snapshot_metrics_and_full_export(database):
    engine,_,settings=database;setup(engine)
    with TestClient(create_app(settings)) as client:
        response=client.post('/api/v1/research/batches',json=request());assert response.status_code==202
        batch=response.json();batch_id=batch['batch_id'];ids=[row['job_id'] for row in batch['jobs']]
        assert batch['status']=='ACTIVE' and batch['counts']['QUEUED']==2
        # Mutating canonical history/rules after admission must not change accepted input.
        with Session(engine) as session,session.begin():
            session.get(InstrumentRecord,rules().instrument_id).tick_size='10'
            snapshots=[session.get(Job,job_id).snapshot for job_id in ids]
            assert snapshots[0]['dataset']==snapshots[1]['dataset']
            assert snapshots[0]['instrument']==snapshots[1]['instrument']
        save_candles(engine,liquid([10,12,14,10,9,99]))
        owner=str(uuid4())
        for _ in ids:jobs.execute(engine,jobs.claim(engine,owner),owner)
        complete=client.get(f'/api/v1/research/batches/{batch_id}').json()
        assert complete['status']=='COMPLETE' and complete['counts']['SUCCEEDED']==2
        assert complete['shared_sha256']==batch['shared_sha256']
        for row in complete['jobs']:
            exported=client.get(f"/api/v1/research/jobs/{row['job_id']}/result").json()
            assert reproduce(exported)==row['run_id']
            assert row['metrics']==exported['metrics']
            assert exported['manifest']['bars']==5
        assert complete['jobs'][0]['run_id']!=complete['jobs'][1]['run_id']
        # Cancelling an already completed batch retains every successful result.
        assert client.post(f'/api/v1/research/batches/{batch_id}/cancel').json()==complete


def test_batch_capacity_atomic_under_concurrent_submissions(database):
    engine,_,settings=database;setup(engine)
    for _ in range(17):jobs.enqueue(engine,{'config':cfg().model_dump(mode='json')},liquid([10,12,14]),rules())
    with TestClient(create_app(settings)) as client:
        def submit(_):return client.post('/api/v1/research/batches',json=request()).status_code
        with ThreadPoolExecutor(max_workers=2) as pool:statuses=list(pool.map(submit,range(2)))
    assert sorted(statuses)==[202,429]
    with Session(engine) as session:assert session.scalar(select(func.count()).select_from(Job))==19


def test_batch_cancel_and_failed_member_do_not_erase_success(database):
    engine,_,settings=database;setup(engine)
    body=request();body['variants'].append({'strategy':'ema_long_flat_v1','parameters':{'period':3}})
    with TestClient(create_app(settings)) as client:
        batch=client.post('/api/v1/research/batches',json=body).json();owner=str(uuid4())
        # Explicitly run the first member under the usual lease protocol.
        first=jobs.claim(engine,owner);jobs.execute(engine,first,owner)
        running=jobs.claim(engine,owner)
        with Session(engine) as session,session.begin():
            job=session.get(Job,running);job.request={**job.request,'parameters':{'period':99}}
        jobs.execute(engine,running,owner)
        cancelled=client.post(f"/api/v1/research/batches/{batch['batch_id']}/cancel").json()
        assert cancelled['status']=='COMPLETE'
        assert cancelled['counts']=={'QUEUED':0,'RUNNING':0,'SUCCEEDED':1,'FAILED':1,'CANCELLED':1}
        assert sum(row['metrics'] is not None for row in cancelled['jobs'])==1


@pytest.mark.parametrize('change',['duplicate','too_many','too_few','bad_parameter','extra'])
def test_batch_request_rejection_creates_no_members(database,change):
    engine,_,settings=database;setup(engine);body=request()
    if change=='duplicate':body['variants']=[body['variants'][0],deepcopy(body['variants'][0])]
    if change=='too_many':body['variants']*=5
    if change=='too_few':body['variants']=body['variants'][:1]
    if change=='bad_parameter':body['variants'][1]['parameters']['fast']=True
    if change=='extra':body['variants'][1]['parameters']['unknown']=1
    with TestClient(create_app(settings)) as client:
        assert client.post('/api/v1/research/batches',json=body).status_code==422
        assert client.get(f'/api/v1/research/batches/{uuid4()}').status_code==404
        assert client.get('/api/v1/research/batches/invalid').status_code==422
    with Session(engine) as session:assert session.scalar(select(func.count()).select_from(Job))==0


def test_batch_running_cancellation_and_retry_idempotence(database):
    engine,_,settings=database;setup(engine)
    with TestClient(create_app(settings)) as client:
        response=client.post('/api/v1/research/batches',json=request());assert response.status_code==202
        batch=response.json();owner=str(uuid4());running=jobs.claim(engine,owner)
        first=client.post(f"/api/v1/research/batches/{batch['batch_id']}/cancel").json()
        assert first['counts']['RUNNING']==1 and first['counts']['CANCELLED']==1
        assert all(row['cancel_requested'] for row in first['jobs'])
        jobs.execute(engine,running,owner)
        completed=client.post(f"/api/v1/research/batches/{batch['batch_id']}/cancel").json()
        assert completed['status']=='COMPLETE' and completed['counts']['CANCELLED']==2
        assert all(row['metrics'] is None and row['run_id'] is None for row in completed['jobs'])
        assert client.post(f"/api/v1/research/batches/{batch['batch_id']}/cancel").json()==completed
