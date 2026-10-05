from datetime import timedelta
from decimal import Decimal
from fractions import Fraction

import pytest

from core.strategy.contracts import StrategyContext
from core.strategy.registry import DEFINITIONS, resolve
from tests.test_indicators import bars


def decisions(values, parameters):
    strategy, validated = resolve('sma_long_flat_v1').create(parameters)
    return [strategy.decide(StrategyContext(bar.close_time, bar.close, None)) for bar in bars(values)]


def test_independent_rational_reference_and_prefix():
    values = [10, 12, 14, 9, 8, 8, 12, 17, 4, 4, 4, 4]
    actual = decisions(values, {'fast': 2, 'slow': 4})
    for index, signal in enumerate(actual):
        if index < 3:
            assert signal is None
        else:
            fast = sum(map(Fraction, values[index-1:index+1])) / 2
            slow = sum(map(Fraction, values[index-3:index+1])) / 4
            assert signal.target == ('LONG' if fast > slow else 'FLAT')
            assert signal.available_at == bars(values)[index].close_time
    assert decisions(values[:7], {'fast': 2, 'slow': 4}) == actual[:7]
    assert actual[-1].target == 'FLAT'


@pytest.mark.parametrize('parameters', [
    {'fast': True}, {'fast': '2'}, {'fast': 2.0}, {'fast': 1},
    {'slow': 501}, {'fast': 20, 'slow': 20}, {'fast': 30, 'slow': 20},
    {'unknown': 1},
])
def test_parameters_rejected(parameters):
    with pytest.raises(ValueError):
        resolve('sma_long_flat_v1').create(parameters)


def test_versions_immutable_and_capability_explicit():
    with pytest.raises(ValueError): resolve('sma_long_flat_v2')
    with pytest.raises(TypeError): DEFINITIONS['custom'] = None
    assert resolve('ema_long_flat_v1').describe()['backtest_available'] is True
    assert resolve('sma_long_flat_v1').describe()['backtest_available'] is True
    _, parameters = resolve('ema_long_flat_v1').create({'period': 2})
    with pytest.raises(ValueError): parameters.period = 3


def test_bad_context_does_not_mutate_state():
    strategy, _ = resolve('sma_long_flat_v1').create({'fast': 2, 'slow': 3})
    bar = bars([10])[0]
    context = StrategyContext(bar.close_time, bar.close, None)
    with pytest.raises(ValueError): strategy.decide(StrategyContext(bar.close_time, Decimal('NaN'), None))
    assert strategy.decide(context) is None
    with pytest.raises(ValueError): strategy.decide(context)
    with pytest.raises(ValueError): strategy.decide(StrategyContext(bar.close_time.replace(tzinfo=None), bar.close, None))
    assert strategy.decide(StrategyContext(bar.close_time + timedelta(hours=1), bar.close, None)) is None
