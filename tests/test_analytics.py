from decimal import Decimal
import math
import pytest
from core.backtest.analytics import analyze
from tests.test_backtest import liquid,rules,cfg
from core.backtest.spot import simulate


def observations(values):
    return [{'as_of':f'2026-01-0{i+1}T00:00:00+00:00','equity':str(v),'quantity':str(i%2)} for i,v in enumerate(values)]

def test_independent_return_ratio_and_drawdown_reference():
    data=analyze(observations([110,99]),[],Decimal(100),86400)
    assert data['sharpe']==pytest.approx(0,abs=1e-12)
    assert data['sortino']==pytest.approx(0,abs=1e-12)
    assert data['annualized_return']==pytest.approx(.99**(365/2)-1)
    assert Decimal(data['drawdown'][1]['drawdown'])==Decimal('.1')
    assert data['calmar']==pytest.approx(data['annualized_return']/.1)
    assert data['exposure_fraction']==.5
    assert data['closed_trade_count']==0 and data['expectancy'] is None
    assert data['profit_factor'] is None and data['warnings']


def test_sample_sharpe_and_downside_rms_reference():
    # Returns +10%, -5%, +20%: independent closed-form denominators.
    data=analyze(observations([110,104.5,125.4]),[],Decimal(100),86400)
    mean=(.1-.05+.2)/3
    sample=math.sqrt(sum((v-mean)**2 for v in [.1,-.05,.2])/2)
    assert data['sharpe']==pytest.approx(mean/sample*math.sqrt(365))
    assert data['sortino']==pytest.approx(mean/(.05/math.sqrt(3))*math.sqrt(365))


def test_zero_dispersion_and_no_downside_not_faked():
    flat=analyze(observations([100,100]),[],Decimal(100),86400)
    assert flat['sharpe'] is None and flat['sortino'] is None and flat['calmar'] is None
    assert flat['annualized_return']==0
    positive=analyze(observations([110,121]),[],Decimal(100),86400)
    assert positive['sharpe'] is None and positive['sortino'] is None
    assert positive['annualized_return']==pytest.approx(1.1**365-1)


def test_partial_exit_only_counts_when_roundtrip_is_closed():
    fills=[{'side':'BUY','execution_at':'2026-01-01T00:00:00+00:00','quantity':'2','fee':'1'},
           {'side':'SELL','execution_at':'2026-01-01T01:00:00+00:00','realized_pnl':'3','fee':'.1','quantity_after':'1'},
           {'side':'SELL','execution_at':'2026-01-01T02:00:00+00:00','realized_pnl':'-1','fee':'.1','quantity_after':'0'}]
    assert analyze(observations([100,102]),fills[:2],Decimal(100),86400)['closed_trade_count']==0
    data=analyze(observations([100,102]),fills,Decimal(100),86400)
    assert data['closed_trade_count']==1 and data['expectancy']=='2' and data['win_rate']==1
    trade=data['closed_trades'][0]
    assert trade['exit_count']==2 and trade['fees']=='1.2' and trade['holding_seconds']==7200


def test_trade_pnl_matches_ledger_and_slippage_includes_tick_rounding():
    result=simulate(liquid([10,12,14,10,9]),rules(),cfg())
    assert result['analysis']['closed_trade_count']==1
    assert result['analysis']['closed_trades'][0]['net_pnl']==result['metrics']['realized_pnl']
    assert result['analysis']['win_rate']==0 and Decimal(result['analysis']['profit_factor'])==0
    assert all(Decimal(f['slippage_cost'])==0 for f in result['fills'])


def test_v1_exports_still_reproduce():
    from core.backtest.legacy_v1 import simulate as v1
    from scripts.reproduce_backtest import reproduce
    original=v1(liquid([10,12,14,10,9]),rules(),cfg())
    assert reproduce(original)==original['run_id']
    latest=simulate(liquid([10,12,14,10,9]),rules(),cfg())
    assert reproduce(latest)==latest['run_id'] and latest['run_id']!=original['run_id']
    assert latest['equity']==original['equity']


def test_excessive_annualization_unavailable_with_reason():
    data=analyze(observations([100,200]),[],Decimal(100),60)
    assert data['annualized_return'] is None and data['calmar'] is None
    assert 'annualized_return' in data['unavailable_reasons']

def test_multi_trade_profit_factor_and_average_use_closed_net_pnl():
    fills=[]
    for day,pnl in ((1,'10'),(2,'-4'),(3,'0')):
        fills.extend([{'side':'BUY','execution_at':f'2026-01-0{day}T00:00:00+00:00','quantity':'1','fee':'.1'},
                      {'side':'SELL','execution_at':f'2026-01-0{day}T01:00:00+00:00','realized_pnl':pnl,'fee':'.1','quantity_after':'0'}])
    result=analyze(observations([100,106]),fills,Decimal(100),86400)
    assert result['closed_trade_count']==3
    assert result['win_rate']==pytest.approx(1/3)
    assert Decimal(result['profit_factor'])==Decimal('2.5')
    assert Decimal(result['expectancy'])==2
    assert Decimal(result['average_win'])==10 and Decimal(result['average_loss'])==-4


def test_v1_reproduction_isolated_from_future_indicator_changes(monkeypatch):
    import core.indicators.engine
    from core.backtest.legacy_v1 import simulate as v1
    expected=v1(liquid([10,12,14,10,9]),rules(),cfg())
    class Broken:
        def __init__(self,*a,**kw):raise RuntimeError('Future implementation')
    monkeypatch.setattr(core.indicators.engine,'IndicatorEngine',Broken)
    assert v1(liquid([10,12,14,10,9]),rules(),cfg())==expected
