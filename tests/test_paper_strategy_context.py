"""Pure-engine strategy integration; synthetic bars, no database or exchange writes.

Research registration does not imply public paper/replay API availability. These
checks cover the internal evaluators without expanding their public contracts.
"""
import json
from datetime import timedelta
from decimal import Decimal

import pytest

from core.backtest.spot import simulate
from core.indicators.engine import IndicatorEngine
from core.models import Candle
from core.paper.ledger import VERSION, RiskLimits, evaluate as historical
from core.paper.stream_ledger import evaluate as realtime
from core.strategy.contracts import StrategyContext
from core.strategy.registry import DEFINITIONS, lookback, resolve
from tests.test_backtest import cfg, liquid, rules


CASES = [
    ('ema_long_flat_v1', {'period': 2}),
    ('sma_long_flat_v1', {'fast': 2, 'slow': 3}),
    ('rsi_long_flat_v1', {'period': 3, 'lower': 30, 'upper': 70}),
    ('bollinger_long_flat_v1', {'period': 4, 'deviations': 1}),
    ('channel_long_flat_v1', {'entry': 3, 'exit': 2}),
    ('macd_long_flat_v1', {'fast': 2, 'slow': 3, 'signal': 2}),
    ('roc_long_flat_v1', {'period': 3}),
    ('stochastic_long_flat_v1', {'period': 3, 'smooth': 2, 'lower': 30, 'upper': 70}),
]
VALUES = [10, 10, 10, 6, 5, 11, 15, 14, 8, 5, 4, 12, 16]
LIMITS = RiskLimits(max_order_quote='10000', max_position_quote='10000', max_drawdown='1')


def evaluate(engine, series, strategy_id, parameters):
    common = dict(strategy_id=strategy_id, parameters=parameters, snapshot_sha256='a' * 64)
    if engine == 'historical':
        return historical(series, rules(), cfg(), LIMITS, **common)
    observations = [
        {'candle': bar.model_dump(mode='json'),
         'observed_at': (bar.close_time + timedelta(seconds=1)).isoformat(),
         'gate': 'ELIGIBLE'}
        for bar in series[2:]
    ]
    return realtime(series[:2], observations, rules(), cfg(), LIMITS, **common)


def expected_signals(series, strategy_id, parameters):
    strategy, _ = resolve(strategy_id).create(parameters)
    indicator = IndicatorEngine(period=2)
    result = []
    for bar in series:
        row = indicator.push(bar)
        signal = strategy.decide(StrategyContext(bar.close_time, bar.close,
                                                row['values']['ema'], bar.high, bar.low))
        if signal is not None:
            result.append({'target': signal.target, 'available_at': signal.available_at.isoformat()})
    return result


def test_every_registered_strategy_has_an_internal_paper_case():
    assert {name for name, _ in CASES} == set(DEFINITIONS)


@pytest.mark.parametrize('engine', ['historical', 'realtime'])
@pytest.mark.parametrize('strategy_id,parameters', CASES)
def test_complete_ohlc_context_drives_actual_paper_orders(engine, strategy_id, parameters):
    series = liquid(VALUES)
    result = evaluate(engine, series, strategy_id, parameters)
    assert result['signals'] == expected_signals(series, strategy_id, parameters)
    assert result['fills'], 'A registered rule must reach the actual fill engine in this fixture'
    assert any(fill['side'] == 'BUY' for fill in result['fills'])
    assert all(Decimal(row['quantity']) >= 0 for row in result['equity'])
    assert all(fill['simulated'] is True for fill in result['fills'])
    assert abs(Decimal(result['account']['net_pnl']) - Decimal(result['account']['realized_pnl'])
               - Decimal(result['account']['unrealized_pnl'])) < Decimal('1e-40')
    for fill in result['fills']:
        assert Decimal(fill['quantity']) > 0
        assert fill['decision_at'] <= fill['execution_at']
        execution = next(bar for bar in series if (
            bar.open_time if engine == 'historical' else bar.close_time).isoformat() == fill['execution_at'])
        assert Decimal(fill['price']) == (execution.open if engine == 'historical' else execution.close)
        if engine == 'realtime':
            assert fill['decision_at'] < fill['execution_at']
            assert fill['observed_at'] > fill['execution_at']


@pytest.mark.parametrize('engine', ['historical', 'realtime'])
@pytest.mark.parametrize('strategy_id,parameters', CASES)
def test_warmup_and_future_bars_do_not_change_reached_prefix(engine, strategy_id, parameters):
    series = liquid(VALUES)
    needed = lookback(strategy_id, parameters)
    warmup = evaluate(engine, series[:needed - 1], strategy_id, parameters)
    assert warmup['signals'] == [] and warmup['fills'] == []
    full = evaluate(engine, series, strategy_id, parameters)
    reached = evaluate(engine, series[:8], strategy_id, parameters)
    assert full['equity'][:8] == reached['equity']
    assert [row for row in full['signals'] if row['available_at'] <= series[7].close_time.isoformat()] == reached['signals']
    cutoff = series[7].open_time if engine == 'historical' else series[7].close_time
    assert [fill for fill in full['fills'] if fill['execution_at'] <= cutoff.isoformat()] == reached['fills']
    altered = [*series[:8], *[bar.model_copy(update={
        'open': Decimal('900'), 'high': Decimal('999'), 'low': Decimal('800'), 'close': Decimal('950')})
        for bar in series[8:]]]
    future_changed = evaluate(engine, altered, strategy_id, parameters)
    assert future_changed['equity'][:8] == reached['equity']


@pytest.mark.parametrize('strategy_id,parameters', CASES)
def test_historical_paper_matches_separate_backtest_execution(strategy_id, parameters):
    series = liquid(VALUES)
    paper = evaluate('historical', series, strategy_id, parameters)
    research = simulate(series, rules(), cfg(), strategy_id=strategy_id, parameters=parameters)
    fields = ('side', 'decision_at', 'execution_at', 'quantity', 'price', 'fee', 'cash_after', 'quantity_after')
    assert [{key: row[key] for key in fields} for row in paper['fills']] == [
        {key: row[key] for key in fields} for row in research['fills']]
    assert Decimal(paper['account']['equity']) == Decimal(research['metrics']['final_equity'])


@pytest.mark.parametrize('engine', ['historical', 'realtime'])
@pytest.mark.parametrize('strategy_id,parameters', CASES)
def test_offline_json_roundtrip_reproduces_complete_engine_ledger(engine, strategy_id, parameters):
    series = liquid(VALUES)
    original = evaluate(engine, series, strategy_id, parameters)
    archive = json.loads(json.dumps({'bars': [bar.model_dump(mode='json') for bar in series],
                                     'strategy': strategy_id, 'parameters': parameters, 'ledger': original}))
    rebuilt = evaluate(engine, [Candle.model_validate(bar) for bar in archive['bars']],
                       archive['strategy'], archive['parameters'])
    assert rebuilt == archive['ledger']


@pytest.mark.parametrize('strategy_id,parameters', CASES)
def test_historical_export_verifier_accepts_real_ledger_and_rejects_tamper(strategy_id, parameters):
    from scripts.verify_paper_prefix import verify

    series = liquid(VALUES)
    prefix = series[:8]
    ledger = evaluate('historical', prefix, strategy_id, parameters)
    exported = {
        'session_id': '00000000-0000-4000-8000-000000000001', 'revision': 1,
        'cursor': len(prefix), 'total': len(series), 'status': 'READY',
        'clock': prefix[-1].close_time.isoformat(),
        'manifest': {
            'version': VERSION, 'strategy_version': 1, 'snapshot_sha256': 'a' * 64,
            'symbol': 'BTCUSDT', 'timeframe': '1m', 'strategy': strategy_id,
            'parameters': parameters, 'config': cfg().model_dump(mode='json'),
            'risk': LIMITS.model_dump(mode='json'), 'instrument': rules().model_dump(mode='json'),
            'start': series[0].open_time.isoformat(), 'end': series[-1].close_time.isoformat(),
        },
        'candles': [bar.model_dump(mode='json') for bar in prefix],
        'trading_enabled': False, 'mode': 'HISTORICAL_PAPER', **ledger,
    }
    restored = json.loads(json.dumps(exported))
    assert verify(restored) == {'bars': len(prefix), 'orders': len(ledger['orders']), 'fills': len(ledger['fills'])}
    restored['account']['cash'] = '999999'
    with pytest.raises(ValueError, match='causal reconstruction'):
        verify(restored)


@pytest.mark.parametrize('strategy_id,parameters', CASES)
@pytest.mark.parametrize('gate', ['PAUSED', 'STALE_BAR', 'PRE_ACTIVATION'])
def test_ineligible_observations_warm_indicators_without_trading(strategy_id, parameters, gate):
    series = liquid(VALUES)
    observations = [
        {'candle': bar.model_dump(mode='json'),
         'observed_at': (bar.close_time + timedelta(seconds=1)).isoformat(), 'gate': gate}
        for bar in series[2:]]
    result = realtime(series[:2], observations, rules(), cfg(), LIMITS,
                      strategy_id=strategy_id, parameters=parameters, snapshot_sha256='a' * 64)
    assert result['fills'] == []
    assert result['account']['cash'] == '1000'
    assert result['signals'] == expected_signals(series, strategy_id, parameters)
