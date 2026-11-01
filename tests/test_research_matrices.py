from datetime import timedelta
from uuid import uuid4
from concurrent.futures import ThreadPoolExecutor
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select,func
from sqlalchemy.orm import Session
from apps.api.main import create_app
from apps.api.research import MatrixRequest
from core.storage.models import ResearchJobRecord as Job
from core.research import jobs
from core.market_data.storage import save_instruments,save_candles
from tests.test_integration import database
from tests.test_backtest import liquid,rules,cfg
from tests.test_indicators import BASE

def body():
    return {'request_id':str(uuid4()),'symbols':['BTCUSDT','ETHUSDT'],'timeframes':['1m','5m'],
            'variants':[{'strategy':'roc_long_flat_v1','parameters':{'period':2}},
                        {'strategy':'stochastic_long_flat_v1','parameters':{'period':2,'smooth':2,'lower':30,'upper':70}}],
            'start_at':BASE.isoformat(),'as_of':(BASE+timedelta(minutes=20)).isoformat(),'config':cfg().model_dump(mode='json')}

def seed(engine):
    for symbol,identity in [('BTCUSDT','binance:spot:BTC-USDT'),('ETHUSDT','binance:spot:ETH-USDT')]:
        instrument=rules().model_copy(update={'instrument_id':identity,'native_symbol':symbol,'base':symbol[:-4]})
        save_instruments(engine,[instrument])
        for timeframe,minutes,count in [('1m',1,20),('5m',5,4)]:
            values=[10,6,5,15]*5
            save_candles(engine,[bar.model_copy(update={'instrument_id':identity,'timeframe':timeframe,
                'open_time':BASE+timedelta(minutes=i*minutes),'close_time':BASE+timedelta(minutes=(i+1)*minutes)}) for i,bar in enumerate(liquid(values[:count]))])

def test_matrix_cross_market_atomic_idempotent_complete_and_verified(database):
    engine,_,settings=database;seed(engine);request=body()
    with TestClient(create_app(settings)) as client:
        response=client.post('/api/v1/research/matrices',json=request);assert response.status_code==202,response.text
        matrix=response.json();assert len(matrix['jobs'])==8
        assert client.post('/api/v1/research/matrices',json=request).json()['matrix_id']==matrix['matrix_id']
        owner=str(uuid4())
        for _ in matrix['jobs']:jobs.execute(engine,jobs.claim(engine,owner),owner)
        complete=client.get('/api/v1/research/matrices/'+matrix['matrix_id']).json()
        assert complete['status']=='COMPLETE'
        assert {row['request']['limit'] for row in complete['jobs']}=={4,20}
        for row in complete['jobs']:
            assert row['status']=='SUCCEEDED'
            assert client.post('/api/v1/research/jobs/'+row['job_id']+'/verify').json()['consistency_verified'] is True
        assert client.post('/api/v1/research/matrices/'+matrix['matrix_id']+'/cancel').json()==complete
        changed={**request,'config':{**request['config'],'initial_cash':'2000'}}
        assert client.post('/api/v1/research/matrices',json=changed).status_code==409
    with Session(engine) as session:assert session.scalar(select(func.count()).select_from(Job))==8

def test_matrix_missing_history_has_no_partial_admission(database):
    engine,_,settings=database
    save_instruments(engine,[rules()]);save_candles(engine,liquid([10]*20))
    with TestClient(create_app(settings)) as client:
        assert client.post('/api/v1/research/matrices',json=body()).status_code==409
    with Session(engine) as session:assert session.scalar(select(func.count()).select_from(Job))==0

def test_matrix_same_request_concurrent_and_partial_cancel(database):
    engine,_,settings=database;seed(engine);request=body()
    with TestClient(create_app(settings)) as client:
        with ThreadPoolExecutor(max_workers=2) as pool:
            statuses=list(pool.map(lambda _:client.post('/api/v1/research/matrices',json=request).status_code,range(2)))
        assert statuses==[202,202]
        owner=str(uuid4());jobs.execute(engine,jobs.claim(engine,owner),owner)
        cancelled=client.post('/api/v1/research/matrices/'+request['request_id']+'/cancel').json()
        assert sum(row['status']=='SUCCEEDED' for row in cancelled['jobs'])==1
        assert sum(row['status']=='CANCELLED' for row in cancelled['jobs'])==7
    with Session(engine) as session:assert session.scalar(select(func.count()).select_from(Job))==8

@pytest.mark.parametrize('change',[
    {'symbols':['BTCUSDT','BTCUSDT']},{'timeframes':['1m','1m']},{'symbols':[]},
    {'start_at':'2026-01-01T00:00:01+00:00'},{'as_of':'2026-01-01T00:01:00+00:00'},
    {'start_at':'2026-01-01T00:00:00.000001+00:00'},
    {'symbols':['BTCUSDT','ETHUSDT'],'timeframes':['1m','5m','15m','1h','4h','1d']},
])
def test_matrix_bad_axes_windows_and_bounds(change):
    with pytest.raises(ValueError):MatrixRequest.model_validate({**body(),**change})


def test_matrix_aggregate_history_bound():
    with pytest.raises(ValueError,match='20000'):
        MatrixRequest.model_validate({**body(),'timeframes':['1h','4h','1d'],'start_at':'2025-04-18T00:00:00Z','as_of':'2025-10-15T00:00:00Z'})
