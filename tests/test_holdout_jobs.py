from uuid import uuid4
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session
import pytest
from apps.api.main import create_app
from core.market_data.storage import save_candles
from core.research import jobs
from core.storage.models import ResearchJobRecord as Job
from tests.test_integration import database
from tests.test_market_integration import seed
from tests.test_backtest import liquid,cfg
from scripts.reproduce_backtest import reproduce


def body():return {'base':{'symbol':'BTCUSDT','timeframe':'1m','limit':9,'config':cfg().model_dump(mode='json')},'train_bars':4}


def test_holdout_real_queue_recovery_snapshot_and_export(database):
    from datetime import timedelta
    engine,_,settings=database;seed(engine);save_candles(engine,liquid([10,12,14,16,30,28,25,27,29]))
    with TestClient(create_app(settings)) as client:
        response=client.post('/api/v1/research/holdouts',json=body());assert response.status_code==202
        task=response.json();job_id=task['job_id'];first=str(uuid4());second=str(uuid4())
        assert task['request']['research_type']=='holdout'
        assert jobs.claim(engine,first)==job_id
        jobs.execute(engine,job_id,first,stopping=lambda:True)
        with Session(engine) as session,session.begin():session.get(Job,job_id).lease_until=jobs.now()-timedelta(seconds=1)
        save_candles(engine,liquid([10,12,14,16,30,28,25,27,29,300]))
        assert jobs.claim(engine,second)==job_id;jobs.execute(engine,job_id,second)
        result=client.get(f'/api/v1/research/jobs/{job_id}/result').json()
        assert result['manifest']['train_bars']==4 and result['manifest']['test_bars']==5
        assert result['manifest']['parameters']==result['train']['manifest']['parameters']==result['test']['manifest']['parameters']
        assert reproduce(result)==result['run_id']
        assert client.get(f'/api/v1/research/jobs/{job_id}').json()['attempts']==2


def test_cancel_between_segments_discards_both_results(database,monkeypatch):
    engine,_,settings=database;seed(engine);save_candles(engine,liquid([10,12,14,16,30,28,25,27,29]))
    with TestClient(create_app(settings)) as client:
        job_id=client.post('/api/v1/research/holdouts',json=body()).json()['job_id']
        owner=str(uuid4());jobs.claim(engine,owner)
        from core.backtest import holdout
        original=holdout.spot_simulate;calls=[]
        def compute(*args,**options):
            result=original(*args,**options);calls.append(result)
            if len(calls)==1:jobs.cancel(engine,job_id)
            return result
        monkeypatch.setattr(holdout,'spot_simulate',compute)
        jobs.execute(engine,job_id,owner)
        assert len(calls)==1
        assert client.get(f'/api/v1/research/jobs/{job_id}').json()['status']=='CANCELLED'
        assert client.get(f'/api/v1/research/jobs/{job_id}/result').status_code==409
        with Session(engine) as session:assert session.get(Job,job_id).result is None


@pytest.mark.parametrize('split,expected',[(True,422),(2.0,422),(0,422),(2,409),(8,409)])
def test_invalid_holdout_rejected_before_enqueue(database,split,expected):
    engine,_,settings=database;seed(engine);save_candles(engine,liquid([10,12,14,16,30,28,25,27,29]))
    with TestClient(create_app(settings)) as client:
        request=body();request['train_bars']=split
        assert client.post('/api/v1/research/holdouts',json=request).status_code==expected
        assert client.get('/api/v1/research/jobs').json()==[]
