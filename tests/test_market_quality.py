"""Fixture quality faults; never mutate product cache or market history."""
from copy import deepcopy
from datetime import datetime,timedelta,timezone
import json,subprocess,sys
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session
from apps.api.main import create_app
from apps.api.market import market_health
from core.market_data import quality
from core.market_data.storage import save_instruments,save_candles
from core.models import Instrument,Candle
from core.storage.models import CandleRecord
from core.paper.fault_adapter import sha
from tests.test_integration import database

NOW=datetime(2026,1,1,0,3,2,tzinfo=timezone.utc)


def evidence(now=NOW):
    rules=Instrument(instrument_id=quality.SYMBOLS['BTCUSDT'],exchange='binance',native_symbol='BTCUSDT',
        base='BTC',quote='USDT',market_type='SPOT',tick_size='0.01',quantity_step='0.001',min_quantity='0.001',min_notional='5')
    stamp=(now-timedelta(seconds=1)).isoformat()
    common={'schema_version':1,'type':'snapshot','instrument_id':rules.instrument_id,'symbol':'BTCUSDT',
        'generation':'fixture-generation','sequence':1,'event_time':stamp,'received_at':stamp,'quality':'healthy'}
    ticker={**common,'channel':'ticker','payload':{'price':'100','change_percent':'0','volume':'1','event_time':stamp}}
    book={**common,'channel':'book','payload':{'valid':True,'exchange_sequence':10,'snapshot_depth_limit':1000,
        'bids':[['99.99','1']],'asks':[['100.01','1']]}}
    end=now.replace(second=0,microsecond=0)
    bars=[Candle(instrument_id=rules.instrument_id,timeframe='1m',open_time=end-timedelta(minutes=3-i),
        close_time=end-timedelta(minutes=2-i),open='100',high='101',low='99',close='100',volume='1',
        is_closed=True,source='binance.public').model_dump(mode='json') for i in range(3)]
    return {'version':quality.EVIDENCE_VERSION,'symbol':'BTCUSDT','observed_at':now.isoformat(),
        'cache_available':True,'storage_available':True,'ticker':ticker,'book':book,
        'instrument':rules.model_dump(mode='json'),'candles':bars}


def change_time(value,key,seconds):
    value[key]=(quality.clock(value[key])+timedelta(seconds=seconds)).isoformat()


@pytest.mark.parametrize('fault,reason',[
    ('missing_ticker','TICKER_MISSING'),('missing_book','BOOK_MISSING'),('cache_down','CACHE_UNAVAILABLE'),
    ('storage_down','STORAGE_UNAVAILABLE'),('old_received','TICKER_STALE'),('old_event','BOOK_STALE'),
    ('future_event','BOOK_FUTURE_CLOCK'),('future_received','TICKER_FUTURE_CLOCK'),
    ('mixed_generation','GENERATION_MISMATCH'),('wrong_symbol','TICKER_INVALID'),
    ('schema_bool','BOOK_INVALID'),('sequence_bool','BOOK_INVALID'),('book_flag_integer','BOOK_INVALID'),
    ('book_exchange_bool','BOOK_INVALID'),('crossed','BOOK_INVALID'),('duplicate_levels','BOOK_INVALID'),
    ('unordered','BOOK_INVALID'),('empty_side','BOOK_INVALID'),('zero_size','BOOK_INVALID'),
    ('too_many_levels','BOOK_INVALID'),('negative_volume','TICKER_INVALID'),('nan_price','TICKER_INVALID'),
    ('future_payload','TICKER_INVALID'),('price_grid','PRICE_RULE_MISMATCH'),
    ('missing_rules','INSTRUMENT_OR_PRICE_INVALID'),('wrong_rules','INSTRUMENT_OR_PRICE_INVALID'),
    ('few_bars','INSUFFICIENT_CLOSED_BARS'),('gap','CLOSED_BAR_GAP'),('reversed','CLOSED_BAR_GAP'),
    ('old_bars','CLOSED_BARS_STALE'),('future_bar','CLOSED_BAR_IDENTITY_OR_FINALITY_INVALID'),
    ('unclosed','CLOSED_BARS_INVALID'),('boolean_coercion','CLOSED_BARS_INVALID'),('bad_source','CLOSED_BAR_IDENTITY_OR_FINALITY_INVALID'),
])
def test_faults_are_unavailable_and_recover_with_new_evidence(fault,reason):
    original=evidence();value=deepcopy(original)
    if fault.startswith('missing_') and fault!='missing_rules':value[fault.removeprefix('missing_')]=None
    elif fault=='cache_down':value['cache_available']=False
    elif fault=='storage_down':value['storage_available']=False
    elif fault=='old_received':change_time(value['ticker'],'received_at',-20)
    elif fault=='old_event':change_time(value['book'],'event_time',-20)
    elif fault=='future_event':change_time(value['book'],'event_time',4)
    elif fault=='future_received':change_time(value['ticker'],'received_at',4)
    elif fault=='mixed_generation':value['book']['generation']='another'
    elif fault=='wrong_symbol':value['ticker']['symbol']='ETHUSDT'
    elif fault=='schema_bool':value['book']['schema_version']=True
    elif fault=='sequence_bool':value['book']['sequence']=True
    elif fault=='book_flag_integer':value['book']['payload']['valid']=1
    elif fault=='book_exchange_bool':value['book']['payload']['exchange_sequence']=True
    elif fault=='crossed':value['book']['payload']['bids'][0][0]='100.01'
    elif fault=='duplicate_levels':value['book']['payload']['bids']*=2
    elif fault=='unordered':value['book']['payload']['bids']=[['99.98','1'],['99.99','1']]
    elif fault=='empty_side':value['book']['payload']['bids']=[]
    elif fault=='zero_size':value['book']['payload']['asks'][0][1]='0'
    elif fault=='too_many_levels':value['book']['payload']['asks']*=21
    elif fault=='negative_volume':value['ticker']['payload']['volume']='-1'
    elif fault=='nan_price':value['ticker']['payload']['price']='NaN'
    elif fault=='future_payload':change_time(value['ticker']['payload'],'event_time',1)
    elif fault=='price_grid':value['ticker']['payload']['price']='100.005'
    elif fault=='missing_rules':value['instrument']=None
    elif fault=='wrong_rules':value['instrument']['native_symbol']='ETHUSDT'
    elif fault=='few_bars':value['candles']=value['candles'][1:]
    elif fault=='gap':
        change_time(value['candles'][0],'open_time',-60);change_time(value['candles'][0],'close_time',-60)
    elif fault=='reversed':value['candles'].reverse()
    elif fault=='old_bars':
        for bar in value['candles']:change_time(bar,'open_time',-120);change_time(bar,'close_time',-120)
    elif fault=='future_bar':change_time(value['candles'][-1],'open_time',60);change_time(value['candles'][-1],'close_time',60)
    elif fault=='unclosed':value['candles'][-1]['is_closed']=False
    elif fault=='boolean_coercion':value['candles'][-1]['is_closed']=1
    elif fault=='bad_source':value['candles'][-1]['source']='fixture.other'
    result=quality.evaluate(value);assert result['status']=='unavailable' and reason in result['reasons']
    assert quality.verify(result)==result
    assert quality.evaluate(original)['status']=='healthy'


def test_healthy_flags_age_limits_and_offline_replay(tmp_path):
    value=evidence();value['ticker']['received_at']=(NOW-timedelta(seconds=15)).isoformat()
    value['ticker']['event_time']=value['ticker']['received_at']
    value['ticker']['payload']['event_time']=value['ticker']['received_at']
    result=quality.evaluate(value);assert result['status']=='healthy' and quality.verify(result)==result
    assert result['read_only'] and not result['submission_allowed']
    assert not result['cross_store_atomic_capture'] and not result['instrument_rules_freshness_verified']
    path=tmp_path/'quality.json';path.write_text(json.dumps(result))
    command=[sys.executable,'-m','scripts.verify_market_quality',str(path)]
    assert subprocess.run(command,capture_output=True).returncode==0
    path.write_text('{"version":"a","version":"b"}');assert subprocess.run(command,capture_output=True).returncode!=0
    change_time(value['ticker'],'received_at',-.000001);assert 'TICKER_STALE' in quality.evaluate(value)['reasons']


@pytest.mark.parametrize('bad',[[],[[]],[[1,'1']],[['100','1','extra']]])
def test_malformed_book_levels_are_reported_without_throwing(bad):
    value=evidence();value['book']['payload']['bids']=bad
    report=quality.evaluate(value);assert 'BOOK_INVALID' in report['reasons']
    assert quality.verify(report)==report


@pytest.mark.parametrize('key,new',[('status','healthy'),('reasons',[]),('submission_allowed',True),
                                  ('cross_store_atomic_capture',True),('limits',{})])
def test_resealed_false_health_or_authorization_claims_fail(key,new):
    value=evidence();value['book']=None;result=quality.evaluate(value);result[key]=new
    result['sha256']=sha({k:v for k,v in result.items() if k!='sha256'})
    with pytest.raises(ValueError):quality.verify(result)


class Cache:
    def __init__(self,raw):self.raw=raw
    def mget(self,_):return self.raw


def test_presence_and_valid_flag_cannot_make_stale_or_mixed_generation_healthy():
    value=evidence(datetime.now(timezone.utc));cache=Cache([json.dumps(value['ticker']),json.dumps(value['book'])])
    assert market_health(cache)['symbols']['BTCUSDT']=='healthy'
    change_time(value['book'],'event_time',-20);cache.raw[1]=json.dumps(value['book'])
    assert market_health(cache)['symbols']['BTCUSDT']=='unavailable'
    value=evidence(datetime.now(timezone.utc));value['book']['generation']='old';cache.raw=[json.dumps(value['ticker']),json.dumps(value['book'])]
    assert market_health(cache)['symbols']['BTCUSDT']=='unavailable'


def seed(engine):
    value=evidence(datetime.now(timezone.utc));save_instruments(engine,[Instrument.model_validate(value['instrument'])])
    save_candles(engine,[Candle.model_validate(bar) for bar in value['candles']]);return value


def test_postgresql_capture_recovers_gap_without_writing_evidence(database):
    engine,_,_=database;value=seed(engine);cache=Cache([json.dumps(value['ticker']),json.dumps(value['book'])])
    result=quality.capture(engine,cache,'BTCUSDT');assert result['status']=='healthy' and quality.verify(result)==result
    with Session(engine) as db,db.begin():db.delete(db.get(CandleRecord,(quality.SYMBOLS['BTCUSDT'],'1m',quality.clock(value['candles'][1]['open_time']))))
    assert 'INSUFFICIENT_CLOSED_BARS' in quality.capture(engine,cache,'BTCUSDT')['reasons']
    save_candles(engine,[Candle.model_validate(value['candles'][1])])
    assert quality.capture(engine,cache,'BTCUSDT')['status']=='healthy'
    with Session(engine) as db:assert len(list(db.scalars(select(CandleRecord))))==3


def test_capture_cache_failure_duplicate_and_bounds_fail_closed(database):
    engine,_,_=database;seed(engine)
    for raw in [[None,None],['{"channel":"x","channel":"y"}',None],['{"price":NaN}',None],
                ['x'*(quality.MAX_SNAPSHOT_BYTES+1),None]]:
        result=quality.capture(engine,Cache(raw),'BTCUSDT');assert result['status']=='unavailable' and quality.verify(result)==result
    class Broken:
        def mget(self,_):raise RuntimeError('cache unavailable')
    assert 'CACHE_UNAVAILABLE' in quality.capture(engine,Broken(),'BTCUSDT')['reasons']


def test_public_quality_api_no_store_and_missing_data_no_order_writes(database):
    engine,_,settings=database;value=seed(engine)
    with TestClient(create_app(settings)) as client:
        client.app.state.cache.mget=lambda _:[json.dumps(value['ticker']),json.dumps(value['book'])]
        response=client.get('/api/v1/market/quality?symbol=BTCUSDT')
        assert response.status_code==200 and response.headers['cache-control']=='no-store'
        assert quality.verify(response.json())['status']=='healthy'
        assert client.get('/api/v1/market/quality?symbol=OTHER').status_code==422
        client.app.state.cache.mget=lambda _:[None,None]
        assert client.get('/api/v1/market/quality?symbol=BTCUSDT').json()['status']=='unavailable'
    from sqlalchemy import text
    with engine.connect() as db:assert db.scalar(text('SELECT count(*) FROM requested_paper_requests'))==0


def test_autocommit_and_database_failure_do_not_claim_storage_health(database):
    from sqlalchemy import create_engine
    engine,_,_=database;value=seed(engine);cache=Cache([json.dumps(value['ticker']),json.dumps(value['book'])])
    autocommit=engine.execution_options(isolation_level='AUTOCOMMIT')
    assert 'STORAGE_UNAVAILABLE' in quality.capture(autocommit,cache,'BTCUSDT')['reasons']
    broken=create_engine(engine.url.set(port=1),connect_args={'connect_timeout':1},hide_parameters=True)
    try:
        report=quality.capture(broken,cache,'BTCUSDT');assert 'STORAGE_UNAVAILABLE' in report['reasons']
        assert quality.verify(report)==report
    finally:broken.dispose()
