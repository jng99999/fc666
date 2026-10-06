from copy import deepcopy
from datetime import timedelta
from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal
import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session
from apps.api.main import create_app
from apps.api.paper import StreamRequest
from core.paper import streams
from core.market_data.storage import save_candles,save_instruments
from core.storage.models import CandleRecord,InstrumentRecord,PaperStreamRecord as Stream
from tests.test_integration import database
from tests.test_market_integration import seed
from tests.test_backtest import liquid,rules,cfg
from tests.test_paper import request as historical_request


def request(bars,**updates):
    value=historical_request();value.pop('as_of');value.update(updates)
    parsed=StreamRequest.model_validate(value).model_dump(mode='json')
    parsed['as_of']=bars[1].close_time.isoformat()
    return parsed


def setup(engine,values=None,**updates):
    bars=liquid(values or [10,12,14,10,9,15,16]);save_instruments(engine,[rules()]);save_candles(engine,bars[:2])
    account=streams.create(engine,request(bars,**updates),bars[:2],rules())
    return account,bars


def arrive(engine,account,bars,index,delay=1):
    save_candles(engine,[bars[index]])
    streams.advance(engine,account['session_id'],observed_at=bars[index].close_time+timedelta(seconds=delay))
    return streams.read(engine,account['session_id'])


def test_warmup_no_historical_trades_and_close_price_after_target(database):
    engine,_,_=database;account,bars=setup(engine)
    assert account['accepted_bars']==0 and account['fills']==[] and account['account']['cash']=='1000'
    state=arrive(engine,account,bars,2)
    fill=state['fills'][0]
    assert fill['decision_at']==bars[1].close_time.isoformat() and fill['execution_at']==bars[2].close_time.isoformat()
    assert Decimal(fill['price'])==14 and Decimal(fill['reference_close'])==14
    assert fill['observed_at']==(bars[2].close_time+timedelta(seconds=1)).isoformat()
    assert state['pending']['status']=='AWAIT_NEXT_CLOSED_BAR'
    assert state['mode']=='REALTIME_CLOSED_BAR_PAPER' and not state['trading_enabled']


def test_closed_close_semantics_differ_from_historical_open(database):
    engine,_,_=database;account,bars=setup(engine)
    bars[2]=bars[2].model_copy(update={'open':Decimal('13')})
    state=arrive(engine,account,bars,2)
    assert Decimal(state['fills'][0]['price'])==14
    assert Decimal(state['fills'][0]['price'])!=bars[2].open


def test_concurrent_duplicate_bar_one_fill_and_stable_id(database):
    engine,_,_=database;account,bars=setup(engine);save_candles(engine,[bars[2]])
    def advance(_):return streams.advance(engine,account['session_id'],observed_at=bars[2].close_time+timedelta(seconds=1))
    with ThreadPoolExecutor(max_workers=4) as pool:list(pool.map(advance,range(4)))
    state=streams.read(engine,account['session_id']);assert state['revision']==1 and len(state['fills'])==1
    streams.advance(engine,account['session_id'],observed_at=bars[2].close_time+timedelta(seconds=3))
    repeated=streams.read(engine,account['session_id'])
    assert repeated['revision']==state['revision'] and repeated['fills']==state['fills']


def test_late_bar_rejected_then_fresh_bar_can_trade(database):
    engine,_,_=database;account,bars=setup(engine)
    late=arrive(engine,account,bars,2,delay=30)
    assert late['observations'][0]['gate']=='STALE_BAR' and late['orders'][0]['reason']=='STALE_BAR' and late['fills']==[]
    # A fresh LONG following the caught-up stale bar may execute at its own close.
    bars[3]=bars[3].model_copy(update={'close':Decimal('16'),'high':Decimal('17'),'low':Decimal('9')})
    fresh=arrive(engine,account,bars,3)
    assert fresh['observations'][-1]['gate']=='ELIGIBLE' and len(fresh['fills'])==1
    assert fresh['fills'][0]['execution_at']==bars[3].close_time.isoformat()


def test_gap_never_skipped_and_backlog_does_not_backfill_trades(database):
    engine,_,_=database;account,bars=setup(engine);save_candles(engine,[bars[3]])
    streams.advance(engine,account['session_id'],observed_at=bars[3].close_time+timedelta(seconds=1))
    state=streams.read(engine,account['session_id']);assert state['feed']['state']=='GAP' and state['accepted_bars']==0
    save_candles(engine,[bars[2]])
    streams.advance(engine,account['session_id'],observed_at=bars[3].close_time+timedelta(seconds=30))
    state=streams.read(engine,account['session_id'])
    assert state['accepted_bars']==2 and state['fills']==[]
    assert all(row['gate']=='STALE_BAR' for row in state['observations'])


def test_pause_keeps_accepting_without_fills_resume_only_future(database):
    engine,_,_=database;account,bars=setup(engine)
    paused=streams.command(engine,account['session_id'],0,'pause');assert paused['status']=='PAUSED'
    state=arrive(engine,account,bars,2)
    assert state['observations'][0]['gate']=='PAUSED' and state['fills']==[]
    state=streams.command(engine,account['session_id'],state['revision'],'resume')
    state=arrive(engine,account,bars,3)
    assert len(state['fills'])==1 and state['fills'][0]['execution_at']==bars[3].close_time.isoformat()


def test_halt_buys_only_allows_sell_and_stop_cannot_resume(database):
    engine,_,_=database;account,bars=setup(engine);state=arrive(engine,account,bars,2)
    state=streams.command(engine,account['session_id'],state['revision'],'halt')
    assert state['risk']['entry_halted']
    state=arrive(engine,account,bars,3);state=arrive(engine,account,bars,4)
    assert [fill['side'] for fill in state['fills']]==['BUY','SELL']
    state=streams.command(engine,account['session_id'],state['revision'],'stop');assert state['status']=='STOPPED'
    with pytest.raises(ValueError):streams.command(engine,account['session_id'],state['revision'],'resume')
    assert not streams.advance(engine,account['session_id'],observed_at=bars[5].close_time)
    assert streams.read(engine,account['session_id'])==state


@pytest.mark.parametrize('change',['seed','accepted','rules','missing'])
def test_revision_or_rules_change_blocks_without_rewriting_ledger(database,change):
    engine,_,_=database;account,bars=setup(engine);state=arrive(engine,account,bars,2)
    with Session(engine) as session,session.begin():
        if change=='rules':session.get(InstrumentRecord,rules().instrument_id).tick_size='1'
        else:
            record=session.get(CandleRecord,(rules().instrument_id,'1m',bars[0 if change=='seed' else 2].open_time))
            if change=='missing':session.delete(record)
            else:record.volume=Decimal('99999')
    assert not streams.advance(engine,account['session_id'],observed_at=bars[3].close_time)
    blocked=streams.read(engine,account['session_id'])
    assert blocked['status']=='BLOCKED' and blocked['fills']==state['fills'] and blocked['account']==state['account']
    assert blocked['feed']['state']==('RULES_CHANGED' if change=='rules' else 'DATA_MISSING' if change=='missing' else 'DATA_REVISED')
    with pytest.raises(ValueError):streams.command(engine,account['session_id'],blocked['revision'],'resume')


def test_cas_and_invalid_durable_ledger_roll_back_acceptance(database):
    engine,_,_=database;account,bars=setup(engine);streams.command(engine,account['session_id'],0,'pause')
    with pytest.raises(streams.Conflict):streams.command(engine,account['session_id'],0,'resume')
    with Session(engine) as session,session.begin():
        record=session.get(Stream,account['session_id']);changed=deepcopy(record.ledger);changed['account']['cash']='9999';record.ledger=changed
    save_candles(engine,[bars[2]])
    with pytest.raises(ValueError,match='integrity'):streams.advance(engine,account['session_id'],observed_at=bars[2].close_time)
    with Session(engine) as session:
        record=session.get(Stream,account['session_id']);assert record.revision==1 and record.observations==[]


def test_api_frozen_snapshot_recovery_and_no_manual_step_or_cutoff(database):
    engine,_,settings=database;account,bars=setup(engine)
    with TestClient(create_app(settings)) as client:
        url='/api/v1/paper/streams/'+account['session_id']
        state=client.get(url).json();assert state['revision']==0
        assert client.post(url+'/command',json={'expected_revision':0,'action':'step'}).status_code==422
        assert client.post(url+'/command',json={'expected_revision':0,'action':'pause','count':2}).status_code==422
        assert client.post('/api/v1/paper/streams',json=historical_request()).status_code==422
        paused=client.post(url+'/command',json={'expected_revision':0,'action':'pause'}).json()
    with TestClient(create_app(settings)) as client:assert client.get(url).json()==paused


def test_pre_activation_late_insert_never_executes_even_within_latency_budget(database):
    engine,_,_=database;bars=liquid([10,12,14,16]);save_instruments(engine,[rules()]);save_candles(engine,bars[:2])
    frozen=request(bars);frozen['as_of']=(bars[2].close_time+timedelta(seconds=1)).isoformat()
    account=streams.create(engine,frozen,bars[:2],rules())
    state=arrive(engine,account,bars,2,delay=2)
    assert state['observations'][0]['gate']=='PRE_ACTIVATION' and state['fills']==[]
    assert state['orders'][0]['reason']=='PRE_ACTIVATION'


def test_stream_export_verification_and_tampering(database):
    from scripts.verify_paper_stream import verify
    engine,_,_=database;account,bars=setup(engine);assert verify(account)=={'warmup':2,'accepted':0,'fills':0}
    state=arrive(engine,account,bars,2);assert verify(state)=={'warmup':2,'accepted':1,'fills':1}
    for changed in ['cash','observed_at','seed','gate']:
        bad=deepcopy(state)
        if changed=='cash':bad['account']['cash']='99999'
        elif changed=='observed_at':bad['observations'][0]['observed_at']=(bars[2].close_time+timedelta(seconds=30)).isoformat()
        elif changed=='seed':bad['seed'][0]['volume']='99'
        else:bad['observations'][0]['gate']='PAUSED'
        with pytest.raises(ValueError):verify(bad)


def test_current_feed_overdue_and_capacity_is_bounded(database):
    engine,_,_=database;account,bars=setup(engine)
    streams.advance(engine,account['session_id'],observed_at=bars[3].close_time)
    assert streams.read(engine,account['session_id'])['feed']['state']=='STALE'
    for _ in range(19):streams.create(engine,request(bars),bars[:2],rules())
    with pytest.raises(streams.Capacity):streams.create(engine,request(bars),bars[:2],rules())
    streams.command(engine,account['session_id'],0,'stop')
    assert streams.create(engine,request(bars),bars[:2],rules())['status']=='RUNNING'


def test_stream_fee_slippage_and_pnl_conservation(database):
    engine,_,_=database
    from core.backtest.spot import BacktestConfig
    config=BacktestConfig(initial_cash='1000',period=2,fee_rate='.01',slippage_rate='.001',allocation='1',participation='.1').model_dump(mode='json')
    account,bars=setup(engine,config=config)
    for index in range(2,5):state=arrive(engine,account,bars,index)
    buy,sell=state['fills'];assert Decimal(buy['price'])==Decimal('14.02') and Decimal(sell['price'])==Decimal('8.99')
    wallet=state['account']
    assert abs(Decimal(wallet['net_pnl'])-Decimal(wallet['realized_pnl'])-Decimal(wallet['unrealized_pnl']))<Decimal('1e-24')
    assert Decimal(wallet['fees'])==sum(Decimal(f['fee']) for f in state['fills'])
    assert all(Decimal(p['cash'])>=0 and Decimal(p['quantity'])>=0 for p in state['equity'])


@pytest.mark.parametrize('limit,reason',[('max_order_quote','MAX_ORDER_QUOTE'),('max_position_quote','MAX_POSITION_QUOTE')])
def test_stream_entry_risk_veto(database,limit,reason):
    engine,_,_=database
    risk={'max_order_quote':'10000','max_position_quote':'10000','max_drawdown':'1',limit:'100'}
    account,bars=setup(engine,risk=risk);state=arrive(engine,account,bars,2)
    assert state['orders'][0]['reason']==reason and state['fills']==[] and state['account']['cash']=='1000'


def test_stream_drawdown_latch_sells_but_never_reenters(database):
    engine,_,_=database;account,bars=setup(engine,risk={'max_order_quote':'10000','max_position_quote':'10000','max_drawdown':'.2'})
    for index in range(2,7):state=arrive(engine,account,bars,index)
    assert state['risk']['drawdown_halted'] and [f['side'] for f in state['fills']]==['BUY','SELL']
    assert state['orders'][-1]['reason']=='MAX_DRAWDOWN'


def test_stream_sma_warmup_and_signal_fraction_reference(database):
    from fractions import Fraction
    engine,_,_=database;account,bars=setup(engine,strategy='sma_long_flat_v1',parameters={'fast':2,'slow':3})
    assert account['signals']==[]
    for index in range(2,7):state=arrive(engine,account,bars,index)
    expected=[]
    for index in range(2,7):
        fast=sum(Fraction(b.close) for b in bars[index-1:index+1])/2
        slow=sum(Fraction(b.close) for b in bars[index-2:index+1])/3
        expected.append({'target':'LONG' if fast>slow else 'FLAT','available_at':bars[index].close_time.isoformat()})
    assert state['signals']==expected
    assert state['fills'][0]['decision_at']==bars[2].close_time.isoformat()
    assert state['fills'][0]['execution_at']==bars[3].close_time.isoformat()


def test_full_exit_clears_cost_basis_at_extreme_valid_decimal_precision():
    from core.models import Instrument
    from core.paper.stream_ledger import evaluate
    from core.backtest.spot import BacktestConfig
    from core.paper.ledger import RiskLimits
    bars=liquid(['10','12','14.123456789123456789','10','9.123456789123456789'])
    bars=[bar.model_copy(update={'volume':Decimal('1000000000')}) for bar in bars]
    instrument=Instrument(instrument_id=bars[0].instrument_id,exchange='binance',native_symbol='BTCUSDT',base='BTC',quote='USDT',market_type='SPOT',tick_size='0.000000000000000001',quantity_step='0.000000000000000001',min_quantity='0.000000000000000001',min_notional='0')
    observations=[{'candle':bar.model_dump(mode='json'),'observed_at':(bar.close_time+timedelta(seconds=1)).isoformat(),'gate':'ELIGIBLE'} for bar in bars[2:]]
    config=BacktestConfig(initial_cash='1000000000',period=2,allocation='1',fee_rate='.011234567891234567',slippage_rate='.012345678912345678',participation='.1')
    limits=RiskLimits(max_order_quote='1000000000',max_position_quote='1000000000',max_drawdown='1')
    result=evaluate(bars[:2],observations,instrument,config,limits,strategy_id='ema_long_flat_v1',parameters={'period':2},snapshot_sha256='a'*64)
    assert [fill['side'] for fill in result['fills']]==['BUY','SELL']
    assert Decimal(result['account']['quantity'])==Decimal(result['account']['cost_basis'])==Decimal(result['account']['unrealized_pnl'])==0
