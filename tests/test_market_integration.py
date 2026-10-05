from decimal import Decimal
import json
from fastapi.testclient import TestClient
import pytest
from apps.api.main import create_app
from core.models import Candle
from core.market_data.storage import save_instruments,save_candles,DataConflict
from core.exchange.binance import MarketDataError
from core.models import Instrument
from tests.test_integration import database

# Fixture import reuses real disposable PostgreSQL integration setup.
def candle():
    return Candle(instrument_id="binance:spot:BTC-USDT",timeframe="1m",open_time="2026-01-01T00:00:00Z",close_time="2026-01-01T00:01:00Z",
        open="100.000000000000000001",high="101",low="99",close="100",volume="1.25",is_closed=True,source="binance.public")

def seed(engine):
    save_instruments(engine,[Instrument(instrument_id="binance:spot:BTC-USDT",exchange="binance",native_symbol="BTCUSDT",base="BTC",quote="USDT",
        market_type="SPOT",tick_size=".01",quantity_step=".001",min_quantity=".001",min_notional="5")])

def test_import_repeatable_and_conflict_atomic(database):
    engine,_,_=database;seed(engine)
    bar=candle()
    assert save_candles(engine,[bar])==1
    assert save_candles(engine,[bar,bar])==0
    with pytest.raises(DataConflict):save_candles(engine,[bar.model_copy(update={"close":Decimal("100.5")})])
    with pytest.raises(MarketDataError):save_candles(engine,[bar.model_copy(update={"is_closed":False})])

def test_stored_public_api_empty_and_range_validation(database):
    engine,_,settings=database;seed(engine);save_candles(engine,[candle()])
    with TestClient(create_app(settings)) as client:
        instruments=client.get('/api/v1/instruments').json()
        assert instruments[0]['tick_size']=='0.010000000000000000'
        response=client.get('/api/v1/candles',params={'symbol':'BTCUSDT','timeframe':'1m'})
        assert response.status_code==200 and len(response.json())==1
        assert response.json()[0]['open']=='100.000000000000000001'
        assert client.get('/api/v1/candles',params={'symbol':'ETHUSDT'}).json()==[]
        for params in [{'symbol':'SOLUSDT'},{'symbol':'BTCUSDT','timeframe':'2m'},{'symbol':'BTCUSDT','limit':1001},{'symbol':'BTCUSDT','start':'2026-01-01T00:00:00'}]:
            assert client.get('/api/v1/candles',params=params).status_code==422
        response=client.get('/api/v1/candles',params={'symbol':'BTCUSDT','start':'2026-01-01T00:01:00Z'})
        assert response.json()==[]

def test_websocket_subscription_rejection(database):
    _,_,settings=database
    with TestClient(create_app(settings)) as client:
        with client.websocket_connect('/ws/v1/market') as ws:
            ws.send_json({'action':'subscribe','symbols':['SOLUSDT'],'channels':['book']})
            from starlette.websockets import WebSocketDisconnect
            with pytest.raises(WebSocketDisconnect):ws.receive_json()


def test_websocket_ack_unavailable_unsubscribe(database):
    _,_,settings=database
    settings.redis_url="redis://127.0.0.1:6379/15"
    with TestClient(create_app(settings)) as client:
        with client.websocket_connect('/ws/v1/market') as ws:
            ws.send_json({'action':'subscribe','symbols':['BTCUSDT'],'channels':['book']})
            ack=ws.receive_json();assert ack['type']=='subscribed' and ack['delivery']=='latest_snapshot'
            assert ws.receive_json()['type']=='unavailable'
            ws.send_json({'action':'unsubscribe'})
            from starlette.websockets import WebSocketDisconnect
            with pytest.raises(WebSocketDisconnect):ws.receive_json()


def test_database_interval_constraint_cannot_be_bypassed(database):
    from sqlalchemy import text
    from sqlalchemy.exc import IntegrityError
    engine,_,_=database;seed(engine);save_candles(engine,[candle()])
    with engine.begin() as conn:
        with pytest.raises(IntegrityError):
            conn.execute(text("UPDATE candles SET close_time=close_time + interval '1 second'"))


def test_reconnect_backfills_missing_closed_candle(database,monkeypatch):
    from datetime import timedelta
    from apps.worker import market
    engine,_,_=database;seed(engine);first=candle();save_candles(engine,[first])
    second=first.model_copy(update={"open_time":first.open_time+timedelta(minutes=1),"close_time":first.close_time+timedelta(minutes=1)})
    class Adapter:
        calls=0
        def server_time(self):return int(second.close_time.timestamp()*1000)
        def candles(self,*args):self.calls+=1;return [first,second]
    monkeypatch.setattr(market,"INTERVALS",{"1m":60000});monkeypatch.setattr(market,"BACKFILL_BARS",2)
    adapter=Adapter()
    market.backfill_symbol("BTCUSDT",adapter,engine)
    market.backfill_symbol("BTCUSDT",adapter,engine)
    assert adapter.calls==1

def test_indicators_as_of_parameters_and_gaps(database):
    from datetime import timedelta
    engine,_,settings=database;seed(engine)
    first=candle()
    series=[first.model_copy(update={'open_time':first.open_time+timedelta(minutes=i),'close_time':first.close_time+timedelta(minutes=i)}) for i in range(25)]
    save_candles(engine,series)
    with TestClient(create_app(settings)) as client:
        response=client.get('/api/v1/indicators',params={'symbol':'BTCUSDT','as_of':series[19].close_time.isoformat()})
        assert response.status_code==200
        result=response.json()
        assert len(result['rows'])==20 and result['closed_only']
        assert result['rows'][-1]['values']['sma']==100
        assert all(row['available_at']<=result['as_of'] for row in result['rows'])
        assert client.get('/api/v1/indicators',params={'symbol':'ETHUSDT'}).json()['rows']==[]
        for params in ({'period':0},{'period':501},{'as_of':'2026-01-01T00:00:00'},{'as_of':'2099-01-01T00:00:00Z'}):
            assert client.get('/api/v1/indicators',params={'symbol':'BTCUSDT',**params}).status_code==422
        save_candles(engine,[first.model_copy(update={'open_time':first.open_time+timedelta(minutes=30),'close_time':first.close_time+timedelta(minutes=30)})])
        assert client.get('/api/v1/indicators',params={'symbol':'BTCUSDT'}).status_code==409

def test_real_database_research_repeat_and_invalid_inputs(database):
    from tests.test_backtest import liquid
    engine,_,settings=database;seed(engine);save_candles(engine,liquid([10,12,14,10,9]))
    request={'symbol':'BTCUSDT','timeframe':'1m','limit':5,'as_of':'2026-01-01T00:05:00Z','config':{'initial_cash':'1000','period':2,'fee_rate':'0','slippage_rate':'0','allocation':'1','participation':'.1'}}
    with TestClient(create_app(settings)) as client:
        response=client.post('/api/v1/research/backtest',json=request)
        assert response.status_code==200
        result=response.json();assert result['trading_enabled'] is False and len(result['fills'])==2
        assert client.post('/api/v1/research/backtest',json=request).json()==result
        from scripts.reproduce_backtest import reproduce
        assert reproduce(result)==result['run_id']
        for change in ({'limit':1001},{'symbol':'SOLUSDT'},{'as_of':'2026-01-01T00:00:00'},{'config':{'period':True}},{'config':{'initial_cash':1000.01}},{'config':{'leverage':10}}):
            assert client.post('/api/v1/research/backtest',json={**request,**change}).status_code==422
        assert client.post('/api/v1/research/backtest',json={**request,'symbol':'ETHUSDT'}).status_code==409

def test_confirmed_rest_revision_is_audited_repeatable_and_not_silent(database):
    from datetime import timedelta
    from core.market_data.storage import reconcile_confirmed_rest
    from core.storage.models import CandleRevisionRecord
    from sqlalchemy import select
    from sqlalchemy.orm import Session
    engine,_,_=database;seed(engine);original=candle();save_candles(engine,[original])
    corrected=original.model_copy(update={'close':Decimal('100.5')})
    with pytest.raises(DataConflict):save_candles(engine,[corrected])
    with pytest.raises(DataConflict):reconcile_confirmed_rest(engine,[corrected],[original],observed_at=original.close_time+timedelta(minutes=2))
    with pytest.raises(DataConflict):reconcile_confirmed_rest(engine,[corrected],[corrected],observed_at=original.close_time)
    assert reconcile_confirmed_rest(engine,[corrected],[corrected],observed_at=original.close_time+timedelta(minutes=2))==1
    assert save_candles(engine,[corrected])==0
    assert reconcile_confirmed_rest(engine,[corrected],[corrected],observed_at=original.close_time+timedelta(minutes=2))==0
    with Session(engine) as session:
        revisions=list(session.scalars(select(CandleRevisionRecord)))
        assert len(revisions)==1 and Decimal(revisions[0].previous['close'])==100 and Decimal(revisions[0].revised['close'])==Decimal('100.5')

def test_rest_revision_batch_rolls_back_when_any_candidate_is_unconfirmed(database):
    from datetime import timedelta
    from core.market_data.storage import reconcile_confirmed_rest
    from core.storage.models import CandleRevisionRecord,CandleRecord
    from sqlalchemy import select,func
    from sqlalchemy.orm import Session
    engine,_,_=database;seed(engine);first=candle();second=first.model_copy(update={'open_time':first.open_time+timedelta(minutes=1),'close_time':first.close_time+timedelta(minutes=1)})
    save_candles(engine,[first,second]);corrected=[b.model_copy(update={'close':Decimal('100.5')}) for b in (first,second)]
    with pytest.raises(DataConflict):reconcile_confirmed_rest(engine,corrected,corrected,observed_at=second.close_time+timedelta(seconds=30))
    with Session(engine) as session:
        assert session.scalar(select(func.count()).select_from(CandleRevisionRecord))==0
        assert session.get(CandleRecord,(first.instrument_id,first.timeframe,first.open_time)).close==first.close
