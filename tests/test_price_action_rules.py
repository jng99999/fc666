"""Synthetic closed-bar fixtures only: no provider, model call or venue execution."""
from datetime import datetime, timedelta, timezone
from decimal import Decimal as D
import json

import pytest
from pydantic import ValidationError

from core.paper.price_action_rules import RuleBar, analyze, VERSION


START = datetime(2025, 1, 6, tzinfo=timezone.utc)
SCOPE = dict(instrument='BTCUSDT', source='bitget.public', timeframe='1h')


def bar(index, price='100', **updates):
    price = D(str(price))
    fields = dict(**SCOPE, open_time=START + timedelta(hours=index),
                  close_time=START + timedelta(hours=index + 1),
                  open=price, high=price + D('.5'), low=price - D('.5'),
                  close=price, volume=None, is_closed=True)
    fields.update(updates)
    return RuleBar(**fields)


def run(bars, rule='TREND_PULLBACK', **updates):
    options = dict(**SCOPE, now=bars[-1].close_time if bars else START, rule=rule)
    options.update(updates)
    return analyze(bars, **options)


def trend_seed():
    steps = [D(-1), D(0), D(1), D(0)]
    return [bar(i, D(100) + D(i) * D('.04') + steps[i % 4]) for i in range(77)]


def trend_fixture():
    bars = trend_seed()
    ema = D(run(bars)['evidence']['ema20']).quantize(D('.000001'))
    bars.append(bar(len(bars), ema + D('.6'), open=ema - D('.2'), low=ema - D('.5'), high=ema + D('.8')))
    return bars


def range_fixture():
    bars = [bar(i, [100, 102, 100, 98][i % 4]) for i in range(62)]
    # A slow approach to the original fixed lower edge reduces RSI naturally.
    for index in range(1, 31):
        price = D(102) - D(index) * D('.15')
        bars.append(bar(len(bars), price, low=max(D('97.5'), price - D('.5'))))
    bars.append(bar(len(bars), '98.2', open=D('97.7'), low=D('97.5')))
    return bars


def fvg_fixture():
    bars = trend_seed()
    for close, opening, low, high in [('108', '107.9', '107.8', '108.3'),
                                      ('108.2', '108.1', '107.9', '108.5'),
                                      ('108.1', '107.7', '107.6', '108.5')]:
        bars.append(bar(len(bars), close, open=D(opening), low=D(low), high=D(high)))
    return bars


def mirror(bars):
    return [row.model_copy(update={'open': D(300) - row.open, 'high': D(300) - row.low,
                                  'low': D(300) - row.high, 'close': D(300) - row.close}) for row in bars]


@pytest.mark.parametrize(('rule', 'factory'), [('STRUCTURE_FVG', fvg_fixture),
                                               ('RANGE_REVERSION', range_fixture),
                                               ('TREND_PULLBACK', trend_fixture)])
@pytest.mark.parametrize('direction', ['LONG', 'SHORT'])
def test_all_three_rules_generate_causal_long_and_short_research_signals(rule, factory, direction):
    bars = factory()
    if direction == 'SHORT':
        bars = mirror(bars)
    result = run(bars, rule)
    assert result['state'] == 'READY'
    assert result['target'] == direction
    assert result['available_at'] == bars[-1].close_time.isoformat()
    assert result['reason'] == f'NEW_{direction}_SIGNAL'
    assert result['evidence']['scope'] == 'SYNTHETIC_DIRECTIONAL_PAPER_ONLY'
    assert result['rule_version'] == VERSION
    assert 'stop_loss' not in result and 'take_profit' not in result
    json.dumps(result, allow_nan=False)


@pytest.mark.parametrize(('rule', 'factory'), [('STRUCTURE_FVG', fvg_fixture),
                                               ('RANGE_REVERSION', range_fixture),
                                               ('TREND_PULLBACK', trend_fixture)])
def test_same_condition_on_next_bar_has_no_repeated_signal_and_never_means_flat(rule, factory):
    bars = factory()
    assert run(bars, rule)['target'] == 'LONG'
    last = bars[-1]
    bars.append(bar(len(bars), last.close, open=last.open, high=last.high, low=last.low))
    result = run(bars, rule)
    assert result['target'] is None
    assert result['reason'] == 'NO_NEW_SIGNAL'
    assert result['evidence']['conditions'][rule] == 'LONG', 'same valid condition is suppressed, not turned into an exit'


def test_empty_warmup_and_exact_decimal_indicators():
    assert run([])['state'] == 'WARMUP'
    assert run([])['available_at'] is None
    bars = [bar(i) for i in range(64)]
    assert run(bars[:63])['state'] == 'WARMUP'
    result = run(bars)
    assert result['state'] == 'READY' and result['target'] is None
    assert result['samples'] == result['required'] == 64
    assert D(result['evidence']['ema20']) == D(100)
    assert D(result['evidence']['ema60']) == D(100)
    assert D(result['evidence']['rsi14']) == D(50)
    assert D(result['evidence']['atr14']) == D(1)


@pytest.mark.parametrize('updates', [
    {'open': 1.1}, {'open': True}, {'close': 'NaN'}, {'high': 'Infinity'},
    {'low': 0}, {'low': '101'}, {'high': '99'}, {'volume': '-1'},
    {'is_closed': False}, {'is_closed': 1}, {'is_closed': 'true'},
    {'open_time': START.replace(tzinfo=None)}, {'close_time': START},
    {'open_time': START + timedelta(minutes=1), 'close_time': START + timedelta(hours=1, minutes=1)},
    {'open_time': 1736121600}, {'timeframe': '30m'},
])
def test_rule_bar_rejects_bad_values_or_invented_closure(updates):
    with pytest.raises((ValueError, ValidationError)):
        bar(0, **updates)


def test_aware_offset_normalizes_to_aligned_utc_and_naive_strings_fail():
    row = bar(0, open_time='2025-01-06T08:00:00+08:00', close_time='2025-01-06T09:00:00+08:00')
    assert row.open_time == START
    assert row.open_time.utcoffset() == timedelta(0)
    with pytest.raises(ValidationError):
        bar(0, open_time='2025-01-06T00:00:00', close_time='2025-01-06T01:00:00')


def test_wilder_atr_uses_fourteen_bar_seed_and_one_fourteenth_smoothing():
    bars = [bar(i) for i in range(14)]
    assert D(run(bars)['evidence']['atr14']) == D(1)
    bars.append(bar(14, 100, high=D(103), low=D(97)))
    actual = D(run(bars)['evidence']['atr14'])
    # Multiply instead of reusing a lower precision expected division.
    assert abs(actual * 14 - D(19)) < D('1e-24')


@pytest.mark.parametrize(('low', 'present'), [('100.6', False), ('100.8', True)])
def test_fvg_minimum_width_is_filtered_using_current_wilder_atr(low, present):
    bars = [bar(i) for i in range(14)]
    bars.append(bar(14, 101, low=D(low), high=D('101.2')))
    result = run(bars, 'STRUCTURE_FVG')
    matching = [gap for gap in result['evidence']['fvgs'] if gap['formed_at'] == bars[12].open_time.isoformat()]
    assert bool(matching) is present
    assert result['target'] is None, 'a newly forming FVG never trades itself'


def test_bos_and_choch_are_close_breaks_of_previously_confirmed_pivots():
    values = [100, 102, 110, 102, 100, 98, 90, 98, 100, 112, 100, 88]
    bars = [bar(i, price) for i, price in enumerate(values)]
    assert run(bars[:9])['evidence']['structure']['breaks'] == []
    up = run(bars[:10])['evidence']['structure']['breaks']
    assert [(event['kind'], event['direction']) for event in up] == [('BOS', 'LONG')]
    both = run(bars)['evidence']['structure']['breaks']
    assert [(event['kind'], event['direction']) for event in both] == [('BOS', 'LONG'), ('CHOCH', 'SHORT')]
    assert both[-1]['available_at'] == bars[-1].close_time.isoformat()
    wick = bars[:9] + [bar(9, 105, high=D(115))]
    assert run(wick)['evidence']['structure']['breaks'] == []


def test_analyze_rejects_bad_scope_future_duplicates_order_and_model_construct_bypass():
    bars = trend_fixture()
    for options in [{'source': 'another-feed'}, {'instrument': 'XAU/USD'}, {'timeframe': '4h'},
                    {'rule': 'NOT_A_RULE'}, {'now': START.replace(tzinfo=None)},
                    {'now': bars[-1].close_time - timedelta(microseconds=1)}]:
        result = run(bars, **options)
        assert result['state'] == 'UNAVAILABLE' and result['target'] is None
    for changed in [bars + [bars[-1]], list(reversed(bars)), bars[:4] + [bars[4].model_copy(update={'source': 'other'})] + bars[5:]]:
        assert run(changed)['state'] == 'UNAVAILABLE'
    malicious = bars[-1].model_copy(update={'is_closed': False})
    assert run(bars[:-1] + [malicious])['state'] == 'UNAVAILABLE'
    assert run([bar(i) for i in range(501)])['state'] == 'UNAVAILABLE'


def test_gap_resets_all_warmup_and_regions_without_reconnecting_history():
    seed = fvg_fixture()
    tail = [bar(100 + i) for i in range(64)]
    warm = run(seed + tail[:20], 'STRUCTURE_FVG')
    assert warm['state'] == 'WARMUP' and warm['samples'] == 20
    assert warm['reason'] == 'GAP_RESET_WARMUP'
    assert warm['target'] is None
    assert warm['evidence']['structure']['breaks'] == []
    assert warm['evidence']['fvgs'] == []
    combined, isolated = run(seed + tail), run(tail)
    for key in ['ema20', 'ema60', 'rsi14', 'atr14', 'ranges', 'fvgs', 'structure']:
        assert combined['evidence'][key] == isolated['evidence'][key]
    assert combined['state'] == 'READY' and combined['evidence']['gaps'] == 1


def test_prefix_does_not_backfill_pivots_breaks_or_fvg_and_never_reads_future():
    pivot_bars = [bar(i, p) for i, p in enumerate([100, 101, 110, 101, 100])]
    assert run(pivot_bars[:4])['evidence']['structure']['high'] is None
    pivot = run(pivot_bars)['evidence']['structure']['high']
    assert pivot['time'] == pivot_bars[2].open_time.isoformat()
    assert pivot['confirmed_at'] == pivot_bars[4].close_time.isoformat()
    bars = fvg_fixture()
    saved = run(bars[:77], 'STRUCTURE_FVG')
    assert saved['evidence']['structure']['breaks'] == []
    assert run(bars[:78], 'STRUCTURE_FVG')['target'] is None
    assert run(bars[:79], 'STRUCTURE_FVG')['target'] is None
    assert run(bars, 'STRUCTURE_FVG')['target'] == 'LONG'
    assert run(bars[:77], 'STRUCTURE_FVG') == saved
    assert run(bars, 'STRUCTURE_FVG', now=bars[78].close_time)['state'] == 'UNAVAILABLE'


def test_fixed_box_never_moves_formation_boundaries_and_breaks_on_close_only():
    bars = [bar(i, [100, 102, 100, 98][i % 4]) for i in range(24)]
    original = run(bars, 'RANGE_REVERSION')['evidence']['ranges'][0]
    assert original['upper_touches'] >= 2 and original['lower_touches'] >= 2
    assert D(original['efficiency']) <= D('.35')
    bars.append(bar(24, 100, high=D(110)))
    wicked = run(bars, 'RANGE_REVERSION')['evidence']['ranges'][0]
    assert wicked['state'] == 'ACTIVE'
    bars.append(bar(25, 110))
    broken = run(bars, 'RANGE_REVERSION')['evidence']['ranges'][0]
    assert broken['state'] == 'BROKEN'
    for key in ['low', 'high', 'edge', 'buffer', 'formed_at', 'confirmed_at']:
        assert original[key] == wicked[key] == broken[key]
    bars.append(bar(26, 100))
    returned = run(bars, 'RANGE_REVERSION')['evidence']['ranges']
    assert len(returned) == 1 and returned[0]['state'] == 'BROKEN'


def test_full_fvg_fill_invalidates_region_even_if_same_candle_closes_back_above():
    bars = fvg_fixture()
    before = run(bars[:79], 'STRUCTURE_FVG')
    up_gaps = [gap for gap in before['evidence']['fvgs'] if gap['direction'] == 'LONG' and gap['state'] == 'ACTIVE']
    assert up_gaps
    bars[-1] = bar(79, '108.1', open=D('107.7'), low=D(101), high=D('108.5'))
    after = run(bars, 'STRUCTURE_FVG')
    assert after['target'] is None
    for gap in up_gaps:
        updated = next(row for row in after['evidence']['fvgs'] if row['formed_at'] == gap['formed_at'] and row['confirmed_at'] == gap['confirmed_at'])
        assert updated['state'] == 'MITIGATED'
        assert updated['low'] == gap['low'] and updated['high'] == gap['high']
        assert updated['invalidated_at'] == bars[-1].close_time.isoformat()


def test_changing_product_or_volume_does_not_reuse_unrelated_market_state():
    bars = trend_fixture()
    first = run(bars)
    gold = [row.model_copy(update={'instrument': 'XAU/USD', 'source': 'gold.public', 'volume': None}) for row in bars]
    result = run(gold, instrument='XAU/USD', source='gold.public')
    assert result['target'] == first['target'] == 'LONG'
    assert result['evidence']['market'] == {'instrument': 'XAU/USD', 'source': 'gold.public', 'timeframe': '1h'}
    assert run(bars) == first
