from decimal import Decimal
import pytest
from core.strategy.contracts import StrategyContext
from core.strategy.registry import resolve, lookback
from tests.test_backtest import liquid, rules, cfg
from core.backtest.spot import simulate
from core.backtest.holdout import validate_split
from tests.test_indicators import bars


def decisions(name, values, parameters):
    strategy, _ = resolve(name).create(parameters)
    return [strategy.decide(StrategyContext(b.close_time,b.close,None,b.high,b.low)) for b in bars(values)]


@pytest.mark.parametrize('name,parameters', [
    ('rsi_long_flat_v1',{'period':3,'lower':30,'upper':70}),
    ('bollinger_long_flat_v1',{'period':4,'deviations':1}),
    ('channel_long_flat_v1',{'entry':3,'exit':2}),
    ('macd_long_flat_v1',{'fast':2,'slow':3,'signal':2}),
    ('roc_long_flat_v1',{'period':3}),
])
def test_causal_prefix_and_next_open_fills(name,parameters):
    values=[10,10,10,6,5,11,15,14,8,5,4,12,16]
    full=decisions(name,values,parameters)
    assert decisions(name,values[:8],parameters)==full[:8]
    assert any(signal and signal.target=='LONG' for signal in full)
    series=liquid(values)
    run=simulate(series,rules(),cfg(),strategy_id=name,parameters=parameters)
    short=simulate(series[:8],rules(),cfg(),strategy_id=name,parameters=parameters)
    assert run['equity'][:8]==short['equity']
    assert run['trading_enabled'] is False
    assert run['fills']
    for fill in run['fills']:
        assert fill['decision_at']==fill['execution_at']
        index=next(i for i,b in enumerate(series) if b.open_time.isoformat()==fill['execution_at'])
        assert any(s and s.available_at==series[index-1].close_time for s in full[:index])
    assert simulate(series,rules(),cfg(),strategy_id=name,parameters=parameters)==run
    needed=lookback(name,parameters)
    with pytest.raises(ValueError,match='lookback'):
        validate_split(series,needed,name,parameters,cfg())


def test_rsi_hand_computed_extremes_and_flat():
    actual=decisions('rsi_long_flat_v1',[10,9,8,7,20],{'period':2,'lower':30,'upper':70})
    assert actual[:2]==[None,None]
    assert [x.target for x in actual[2:]]==['LONG','LONG','FLAT']
    assert all(x is None or x.target=='FLAT' for x in decisions('rsi_long_flat_v1',[10]*8,{'period':2}))


def test_bands_population_variance_and_channel_excludes_decision_bar():
    actual=decisions('bollinger_long_flat_v1',[10,10,10,6,10],{'period':4,'deviations':1})
    # Mean9, population sd=sqrt3:6 is below lower;10 returns above midline.
    assert actual[3].target=='LONG' and actual[4].target=='FLAT'
    assert all(x is None or x.target=='FLAT' for x in decisions('bollinger_long_flat_v1',[10]*8,{'period':3}))
    actual=decisions('channel_long_flat_v1',[10,10,10,12,9,7],{'entry':3,'exit':2})
    assert actual[:3]==[None]*3
    assert [x.target for x in actual[3:]]==['LONG','LONG','FLAT'] #9 equals prior low: hold.


@pytest.mark.parametrize('name,parameters', [
    ('rsi_long_flat_v1',{'lower':70,'upper':30}),
    ('rsi_long_flat_v1',{'period':True}),
    ('bollinger_long_flat_v1',{'deviations':0}),
    ('channel_long_flat_v1',{'entry':5,'exit':10}),
    ('macd_long_flat_v1',{'fast':12,'slow':12}),
    ('macd_long_flat_v1',{'signal':True}),
    ('macd_long_flat_v1',{'signal':1}),
    ('roc_long_flat_v1',{'period':501}),
    ('roc_long_flat_v1',{'period':'20'}),
])
def test_invalid_parameters(name,parameters):
    with pytest.raises(ValueError):resolve(name).create(parameters)


@pytest.mark.parametrize('name',['rsi_long_flat_v1','bollinger_long_flat_v1','channel_long_flat_v1','macd_long_flat_v1','roc_long_flat_v1'])
def test_bad_context_does_not_advance(name):
    strategy,_=resolve(name).create({})
    b=bars([10])[0]
    with pytest.raises(ValueError):strategy.decide(StrategyContext(b.close_time,Decimal('NaN'),None,b.high,b.low))
    context=StrategyContext(b.close_time,b.close,None,b.high,b.low)
    assert strategy.decide(context) is None
    with pytest.raises(ValueError):strategy.decide(context)


def test_public_universe_identity_and_unsupported_symbol():
    from core.exchange.catalog import BITGET_SYMBOLS
    from core.exchange.bitget import normalize_candle, MarketDataError
    assert len(BITGET_SYMBOLS)==8
    for symbol,identity in BITGET_SYMBOLS.items():
        assert normalize_candle(symbol,'1m',['60000','10','12','9','11','1'],120000).instrument_id==identity
    with pytest.raises(MarketDataError):normalize_candle('UNKNOWNUSDT','1m',['60000','10','12','9','11','1'],120000)


def test_request_contracts_support_research_without_expanding_paper_strategies():
    from apps.api.research import RunRequest, Variant
    from apps.api.paper import StreamRequest
    from apps.api.replay import ReplayRequest
    from core.exchange.public import SYMBOLS, PROVIDER
    assert StreamRequest().max_lag_seconds==(60 if PROVIDER=='bitget' else 15)
    with pytest.raises(ValueError):StreamRequest(max_lag_seconds=61)
    for symbol in SYMBOLS:
        assert RunRequest(symbol=symbol,strategy='rsi_long_flat_v1').parameters['period']==14
        assert StreamRequest(symbol=symbol).symbol==symbol
        assert ReplayRequest(symbol=symbol).symbol==symbol
    for name in ('rsi_long_flat_v1','bollinger_long_flat_v1','channel_long_flat_v1','macd_long_flat_v1','roc_long_flat_v1'):
        assert Variant(strategy=name,parameters={}).strategy==name
        with pytest.raises(ValueError):StreamRequest(strategy=name)
    with pytest.raises(ValueError):RunRequest(symbol='UNKNOWNUSDT')
    with pytest.raises(ValueError):RunRequest(strategy='unknown')


def test_macd_independent_fraction_reference_and_exact_warmup():
    from fractions import Fraction
    values=[10,12,11,15,7,9,18,13,6,8,16]
    def ema(sequence,period):
        result=[None]*(period-1)
        value=sum(map(Fraction,sequence[:period]))/period
        result.append(value)
        for close in sequence[period:]:
            value+=Fraction(2,period+1)*(close-value)
            result.append(value)
        return result
    fast,slow=ema(values,2),ema(values,3)
    macd=[a-b for a,b in zip(fast[2:],slow[2:])]
    signal=ema(macd,2)
    actual=decisions('macd_long_flat_v1',values,{'fast':2,'slow':3,'signal':2})
    assert actual[:3]==[None]*3
    for i in range(3,len(values)):
        assert actual[i].target==('LONG' if macd[i-2]>signal[i-2] else 'FLAT')
    assert lookback('macd_long_flat_v1',{})==34
    assert all(s is None or s.target=='FLAT' for s in decisions('macd_long_flat_v1',[10]*40,{}))


def test_roc_exact_lag_and_zero_returns_stay_flat():
    actual=decisions('roc_long_flat_v1',[10,8,12,8,12,9],{'period':2})
    assert actual[:2]==[None,None]
    assert [s.target for s in actual[2:]]==['LONG','FLAT','FLAT','LONG']
    assert lookback('roc_long_flat_v1',{})==21


@pytest.mark.parametrize('name,parameters',[
    ('macd_long_flat_v1',{'fast':2,'slow':3,'signal':2}),
    ('roc_long_flat_v1',{'period':3}),
])
def test_new_strategies_holdout_and_walk_forward_reproduce(name,parameters):
    from core.backtest.holdout import simulate as holdout
    from core.backtest.walk_forward import simulate as walk, validate, verify as verify_walk
    from scripts.reproduce_backtest import reproduce
    series=liquid([10,12,11,15,7,9,18,13,6,8,16,10]*3)
    report=holdout(series,rules(),cfg(),strategy_id=name,parameters=parameters,train_bars=18)
    assert reproduce(report)==report['run_id']
    report=walk(series,rules(),cfg(),strategy_id=name,grid=[parameters],train_bars=18,test_bars=9)
    assert verify_walk(report)==report
    with pytest.raises(ValueError,match='lookback'):
        validate(series,rules(),name,[parameters],18,lookback(name,parameters))
