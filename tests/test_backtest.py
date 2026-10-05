from decimal import Decimal
import pytest
from core.models import Instrument
from core.backtest.spot import BacktestConfig,simulate
from tests.test_indicators import bars

def rules():
    return Instrument(instrument_id='binance:spot:BTC-USDT',exchange='binance',native_symbol='BTCUSDT',base='BTC',quote='USDT',market_type='SPOT',tick_size='.01',quantity_step='.001',min_quantity='.001',min_notional='1')

def liquid(values): return [b.model_copy(update={'volume':Decimal('10000')}) for b in bars(values)]
def cfg(**updates): return BacktestConfig(initial_cash='1000',period=2,allocation='1',fee_rate='0',slippage_rate='0',participation='.1',**updates)

def test_hand_calculated_next_open_ledger_and_no_final_fill():
    series=liquid([10,12,14,10,9])
    result=simulate(series,rules(),cfg())
    buy,sell=result['fills']
    assert buy['execution_at']==series[2].open_time.isoformat() and buy['decision_at']==series[1].close_time.isoformat()
    assert buy['price']=='14' and buy['quantity']=='71.428'
    assert sell['execution_at']==series[4].open_time.isoformat() and sell['price']=='9'
    assert Decimal(result['metrics']['final_equity'])==Decimal('642.860')
    assert Decimal(result['metrics']['realized_pnl'])==Decimal('-357.140')
    assert result['pending_final_signal']['status']=='NO_NEXT_BAR'
    assert simulate(series,rules(),cfg())==result
    assert simulate(series[:2],rules(),cfg())['fills']==[]

def test_fee_slippage_rounding_and_conservation():
    config=BacktestConfig(initial_cash='1000',period=2,fee_rate='.01',slippage_rate='.001',allocation='1',participation='.1')
    result=simulate(liquid([10,12,14,10,9]),rules(),config)
    assert result['fills'][0]['price']=='14.02' # adverse and tick ceil
    assert result['fills'][1]['price']=='8.99' # adverse and tick floor
    metrics=result['metrics'];equity=Decimal(metrics['final_equity'])
    assert equity-Decimal('1000')==Decimal(metrics['net_pnl'])
    assert Decimal(metrics['realized_pnl'])+Decimal(metrics['unrealized_pnl'])==Decimal(metrics['net_pnl'])
    assert Decimal(metrics['fees'])==sum(Decimal(f['fee']) for f in result['fills'])
    assert all(Decimal(r['cash'])>=0 and Decimal(r['quantity'])>=0 for r in result['equity'])

def test_prefix_fills_equity_and_hash_identity():
    series=liquid([10,12,14,15,10,9,10,14])
    full=simulate(series,rules(),cfg());short=simulate(series[:5],rules(),cfg())
    assert full['equity'][:5]==short['equity']
    assert [f for f in full['fills'] if f['execution_at']<series[4].close_time.isoformat()]==short['fills']
    assert full['run_id']!=short['run_id']
    assert simulate(series,rules(),BacktestConfig(period=3))['run_id']!=full['run_id']

def test_volume_is_prior_known_bar_and_ioc_partial():
    series=liquid([10,12,14,15]);series[1]=series[1].model_copy(update={'volume':Decimal('1')})
    result=simulate(series,rules(),cfg())
    assert result['fills'][0]['quantity']=='0.1'
    assert result['orders'][0]['status']=='PARTIAL_CANCELLED'
    changed=series.copy();changed[2]=changed[2].model_copy(update={'volume':Decimal('0')})
    assert simulate(changed,rules(),cfg())['fills'][0]==result['fills'][0]

def test_no_liquidity_rejection_and_unmatured_no_trades():
    series=[b.model_copy(update={'volume':Decimal('0')}) for b in bars([10,12,14])]
    result=simulate(series,rules(),cfg())
    assert result['fills']==[] and result['orders'][0]['status']=='REJECTED'
    result=simulate(liquid([10,12]),rules(),BacktestConfig(period=20))
    assert result['fills']==[] and result['signals']==[]

@pytest.mark.parametrize('updates',[{'fee_rate':.01},{'allocation':'0'},{'allocation':'1.1'},{'participation':'.5'},{'slippage_rate':'-.1'},{'period':True},{'period':1},{'initial_cash':'NaN'}])
def test_invalid_config(updates):
    with pytest.raises(ValueError):BacktestConfig(**updates)

def test_capability_and_data_errors():
    series=liquid([10,12,14])
    for invalid in ([series[0]], [series[0],series[2]],[series[0],series[0]],[series[0],series[1].model_copy(update={'is_closed':False})]):
        with pytest.raises(ValueError):simulate(invalid,rules(),cfg())
    perpetual=rules().model_copy(update={'market_type':'PERPETUAL'})
    with pytest.raises(ValueError,match='Spot'):simulate(series,perpetual,cfg())

def test_export_reproduction_and_tamper_detection():
    from scripts.reproduce_backtest import reproduce
    import copy
    result=simulate(liquid([10,12,14,10,9]),rules(),cfg())
    assert reproduce(result)==result['run_id']
    tampered=copy.deepcopy(result);tampered['metrics']['fees']='999'
    with pytest.raises(ValueError,match='differs'):reproduce(tampered)
    tampered=copy.deepcopy(result);tampered['dataset'][0]['volume']='1'
    with pytest.raises(ValueError,match='differs'):reproduce(tampered)


def test_sma_next_open_and_independent_signal_reference():
    from fractions import Fraction
    series=liquid([10,12,14,10,9,15,16])
    result=simulate(series,rules(),cfg(),strategy_id='sma_long_flat_v1',parameters={'fast':2,'slow':3})
    expected=[]
    for index in range(2,len(series)):
        fast=sum(Fraction(b.close) for b in series[index-1:index+1])/2
        slow=sum(Fraction(b.close) for b in series[index-2:index+1])/3
        expected.append({'target':'LONG' if fast>slow else 'FLAT','available_at':series[index].close_time.isoformat()})
    assert result['signals']==expected
    assert result['fills'][0]['execution_at']==series[3].open_time.isoformat()
    prefix=simulate(series[:5],rules(),cfg(),strategy_id='sma_long_flat_v1',parameters={'fast':2,'slow':3})
    assert prefix['equity']==result['equity'][:5]
    assert reproduce_sma(result)==result['run_id']


def reproduce_sma(result):
    from scripts.reproduce_backtest import reproduce
    return reproduce(result)
