from datetime import datetime, timedelta, timezone
from decimal import Decimal
import math
import pytest
from core.models import Candle
from core.indicators.engine import IndicatorEngine, calculate

BASE = datetime(2026, 1, 1, tzinfo=timezone.utc)
def bars(values):
    return [Candle(instrument_id='binance:spot:BTC-USDT',timeframe='1m',open_time=BASE+timedelta(minutes=i),close_time=BASE+timedelta(minutes=i+1),open=str(v),high=str(Decimal(str(v))+1),low=str(Decimal(str(v))-1),close=str(v),volume='1',is_closed=True,source='test.synthetic') for i,v in enumerate(values)]
def run(values,**kwargs): return calculate(bars(values),as_of=BASE+timedelta(days=2),**kwargs)

def test_hand_calculated_average_bands_and_warmup():
    rows=run([10,12,14,16],period=3,oscillator_period=2)
    assert rows[1]['values']['sma'] is None
    assert rows[2]['values']['sma']==12
    assert rows[2]['values']['ema']==12
    assert rows[3]['values']['ema']==14
    assert rows[2]['values']['bollinger_upper']==pytest.approx(12+2*math.sqrt(8/3))
    assert rows[2]['values']['vwap']==12
    assert rows[1]['values']['atr']==pytest.approx(2.5)
    assert rows[2]['values']['atr']==pytest.approx(2.75)
    assert rows[1]['values']['rsi'] is None
    assert rows[2]['values']['rsi']==100

def test_wilder_rsi_published_reference():
    # Wilder worksheet closing prices; independently published first RSI ~70.4641.
    prices=[44.34,44.09,44.15,43.61,44.33,44.83,45.10,45.42,45.84,46.08,45.89,46.03,45.61,46.28,46.28,46.00,46.03]
    rows=run(prices)
    assert rows[13]['values']['rsi'] is None
    assert rows[14]['values']['rsi']==pytest.approx(70.464135,abs=1e-6)
    assert rows[15]['values']['rsi']==pytest.approx(66.249619,abs=1e-6)

def test_macd_linear_reference():
    rows=run(list(range(10,60)))
    # SMA-seeded EMA of a unit-slope line lags (period-1)/2;
    # fast-slow = (26-12)/2 = 7, first signal after 9 MACD observations.
    assert rows[24]['values']['macd'] is None
    assert rows[25]['values']['macd']==pytest.approx(7)
    assert rows[32]['values']['macd_signal'] is None
    assert rows[33]['values']['macd_signal']==pytest.approx(7)
    assert rows[33]['values']['macd_histogram']==pytest.approx(0)

def test_flat_zero_volume_and_session_reset():
    series=bars([10]*40)
    assert calculate(series,as_of=BASE+timedelta(days=1))[20]['values']['rsi']==50
    empty=[b.model_copy(update={'volume':Decimal(0)}) for b in series]
    assert all(r['values']['vwap'] is None for r in calculate(empty,as_of=BASE+timedelta(days=1)))
    # Contiguous across UTC midnight: the new session must exclude previous volume.
    shifted=[b.model_copy(update={'open_time':b.open_time+timedelta(hours=23,minutes=59),'close_time':b.close_time+timedelta(hours=23,minutes=59)}) for b in bars([10,20])]
    assert calculate(shifted,as_of=BASE+timedelta(days=2))[1]['values']['vwap']==20

def test_prefix_invariance_as_of_and_streaming():
    series=bars([10+(i%7)*.3+i*.01 for i in range(70)])
    complete=calculate(series,as_of=series[-1].close_time)
    engine=IndicatorEngine()
    assert [engine.push(b) for b in series]==complete
    for length in (1,14,20,34,45):
        assert calculate(series,as_of=series[length-1].close_time)==complete[:length]
        assert calculate(series[:length],as_of=series[-1].close_time)==complete[:length]
    assert calculate(series,as_of=BASE)==[]
    with pytest.raises(ValueError):calculate(series,as_of=BASE.replace(tzinfo=None))

def test_gap_duplicate_mixed_series_and_open_rejected():
    series=bars([10,11,12])
    for invalid in ([series[0],series[2]],[series[0],series[0]],[series[0],series[1].model_copy(update={'instrument_id':'other'})]):
        with pytest.raises(ValueError,match='contiguous'):calculate(invalid,as_of=BASE+timedelta(days=1))
    engine=IndicatorEngine()
    with pytest.raises(ValueError,match='finalized'):engine.push(series[0].model_copy(update={'is_closed':False}))
    assert calculate([series[0].model_copy(update={'is_closed':False})],as_of=BASE+timedelta(days=1))==[]

@pytest.mark.parametrize('period',[0,-1,501,True,1.5])
def test_parameter_validation(period):
    with pytest.raises(ValueError):IndicatorEngine(period=period)

def test_swings_not_available_until_confirmation_and_ties_rejected():
    rows=run([10,12,15,12,10],swing_radius=2)
    assert all(not row['confirmed_swings'] for row in rows[:4])
    pivot=rows[4]['confirmed_swings'][0]
    assert pivot['kind']=='high' and pivot['price']=='16'
    assert pivot['pivot_time']==bars([10,12,15])[2].open_time.isoformat()
    assert pivot['confirmed_at']==rows[4]['available_at']
    assert not run([10,12,15,15,10])[-1]['confirmed_swings']

def test_confirmed_equal_high_label_is_explicit():
    rows=run([10,12,15,12,10,12,15,12,10])
    assert rows[8]['confirmed_swings'][0]['label']=='EH'

def test_structure_break_is_close_confirmed_once_and_causal():
    values=[10,12,15,12,10,12,17,18,18,12,8]
    rows=run(values)
    assert not any(row['structure_breaks'] for row in rows[:6])
    assert rows[6]['structure_breaks'][0]['kind']=='BOS'
    assert rows[6]['structure_breaks'][0]['level']=='16'
    assert rows[6]['structure_breaks'][0]['available_at']==rows[6]['available_at']
    assert not rows[7]['structure_breaks'] and not rows[8]['structure_breaks']
    assert rows[-1]['structure_breaks'][0]['kind']=='CHOCH'
    assert rows[-1]['structure_breaks'][0]['direction']=='down'
    assert run(values[:7])==rows[:7]
