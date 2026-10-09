from datetime import timedelta
from uuid import uuid4
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session
import pytest
from apps.api.main import create_app
from core.market_data.storage import save_candles
from core.research import jobs
from core.backtest import walk_forward
from core.storage.models import ResearchJobRecord as Job
from tests.test_integration import database
from tests.test_market_integration import seed
from tests.test_backtest import liquid,cfg


def body():return dict(symbol='BTCUSDT',timeframe='1m',limit=12,config=cfg().model_dump(mode='json'),grid=[{'period':2},{'period':3}],train_bars=4,test_bars=4)


def test_walk_queue_frozen_recovery_and_export(database):
    engine,_,settings=database;seed(engine);save_candles(engine,liquid([10,12,14,16,30,28,25,27,29,26,30,31]))
    with TestClient(create_app(settings)) as client:
        response=client.post('/api/v1/research/walk-forwards',json=body());assert response.status_code==202
        task=response.json();job_id=task['job_id'];first=str(uuid4());second=str(uuid4())
        assert task['request']['research_type']=='walk_forward';assert jobs.claim(engine,first)==job_id
        jobs.execute(engine,job_id,first,stopping=lambda:True)
        with Session(engine) as session,session.begin():session.get(Job,job_id).lease_until=jobs.now()-timedelta(seconds=1)
        save_candles(engine,liquid([10,12,14,16,30,28,25,27,29,26,30,31,300]))
        assert jobs.claim(engine,second)==job_id;jobs.execute(engine,job_id,second)
        result=client.get(f'/api/v1/research/jobs/{job_id}/result').json()
        assert len(result['dataset'])==12 and len(result['folds'])==2
        assert walk_forward.verify(result)==result
        assert client.get(f'/api/v1/research/jobs/{job_id}').json()['attempts']==2


def test_walk_cancel_between_candidates_publishes_no_partial_result(database,monkeypatch):
    engine,_,settings=database;seed(engine);save_candles(engine,liquid([10,12,14,16,30,28,25,27,29,26,30,31]))
    with TestClient(create_app(settings)) as client:
        job_id=client.post('/api/v1/research/walk-forwards',json=body()).json()['job_id'];owner=str(uuid4());jobs.claim(engine,owner)
        original=walk_forward.spot;calls=[]
        def compute(*args,**options):
            result=original(*args,**options);calls.append(result)
            if len(calls)==1:jobs.cancel(engine,job_id)
            return result
        monkeypatch.setattr(walk_forward,'spot',compute);jobs.execute(engine,job_id,owner)
        assert len(calls)==1
        assert client.get(f'/api/v1/research/jobs/{job_id}').json()['status']=='CANCELLED'
        assert client.get(f'/api/v1/research/jobs/{job_id}/result').status_code==409


@pytest.mark.parametrize('change,status',[({'train_bars':True},422),({'test_bars':2},422),({'grid':[{'period':100}]},409),({'grid':[{'period':2},{'period':2}]},409),({'train_bars':11},409)])
def test_walk_invalid_not_queued(database,change,status):
    engine,_,settings=database;seed(engine);save_candles(engine,liquid([10,12,14,16,30,28,25,27,29,26,30,31]))
    with TestClient(create_app(settings)) as client:
        assert client.post('/api/v1/research/walk-forwards',json={**body(),**change}).status_code==status
        assert client.get('/api/v1/research/jobs').json()==[]
