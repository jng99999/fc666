"""Synthetic fixtures validate model semantics, never historical fills or news."""
from datetime import timedelta
from decimal import Decimal as D
import json

import pytest

from core.backtest import price_action as engine
from core.paper.directional import News, Event
from tests.test_price_action_rules import START, bar, trend_fixture, range_fixture, fvg_fixture, mirror


@pytest.fixture(autouse=True)
def reset_pure_memoization():
    engine.clear_signal_cache()
    yield
    engine.clear_signal_cache()


def config(**updates):
    fields = dict(rule='TREND_PULLBACK', initial_cash='10000', quantity='1', fee_rate='0',
                  spread='0', slippage='0', stop_loss_percent='.1', take_profit_percent='.1',
                  tick_size='.01', quantity_step='.01')
    fields.update(updates)
    return engine.PriceActionBacktestConfig(**fields)


def simulate(bars, cfg=None, **updates):
    options = dict(instrument='BTCUSDT', source='bitget.public', timeframe='1h',
                   as_of=bars[-1].close_time, news_policy='UNCOVERED_RESEARCH')
    options.update(updates)
    return engine.simulate(bars, cfg or config(), **options)


def fake_signals(monkeypatch, targets):
    """Only execution unit tests replace rule signals; strategy tests below do not."""
    def analyze(rows, **scope):
        index = int((rows[-1].open_time - START).total_seconds() / 3600)
        return dict(rule_version=engine.RULE_VERSION, state='READY', target=targets.get(index),
                    available_at=rows[-1].close_time.isoformat(), reason='SYNTHETIC_EXECUTION_UNIT_TEST',
                    samples=len(rows), required=64, evidence={})
    monkeypatch.setattr(engine, 'rule_analyze', analyze)


@pytest.mark.parametrize(('rule', 'factory'), [('TREND_PULLBACK', trend_fixture),
                                               ('RANGE_REVERSION', range_fixture),
                                               ('STRUCTURE_FVG', fvg_fixture)])
@pytest.mark.parametrize('direction', ['LONG', 'SHORT'])
def test_actual_rules_queue_at_close_and_execute_only_at_next_observed_open(rule, factory, direction):
    bars = factory()
    if direction == 'SHORT':
        bars = mirror(bars)
    original = bars[:]
    last = bars[-1].close
    before = simulate(bars, config(rule=rule))
    assert before['fills'] == []
    assert before['pending_final_signal']['status'] == 'NO_NEXT_BAR'
    bars.append(bar(len(bars), last, open=last, low=last - D('.2'), high=last + D('.2')))
    after = simulate(bars, config(rule=rule))
    assert after['fills'][0]['action'] == 'OPEN' and after['fills'][0]['direction'] == direction
    assert after['fills'][0]['execution_at'] == bars[-1].open_time.isoformat()
    assert after['fills'][0]['decision_at'] == original[-1].close_time.isoformat()
    assert after['fills'][0]['execution_at'] != original[-1].open_time.isoformat()
    assert after['fills'][0]['model_fill'] is True
    assert 'reference_bid' not in after['fills'][0] and 'reference_ask' not in after['fills'][0]
    assert simulate(original, config(rule=rule)) == before, 'future appended bars never rewrite a prior run'


def test_missing_historical_news_is_strict_by_default_and_diagnostic_is_never_eligible():
    bars = trend_fixture(); last = bars[-1].close
    bars.append(bar(len(bars), last))
    strict = engine.simulate(bars, config(), instrument='BTCUSDT', source='bitget.public',
                             timeframe='1h', as_of=bars[-1].close_time)
    assert strict['fills'] == [] and strict['signals'][-1]['status'] == 'NEWS_UNAVAILABLE'
    assert strict['news_coverage']['status'] == 'NOT_COVERED'
    diagnostic = simulate(bars)
    assert len(diagnostic['fills']) == 1
    for result in [strict, diagnostic]:
        assert not result['news_validated'] and not result['live_eligible'] and not result['trading_enabled']
        assert not result['news_coverage']['complete']
    assert strict['run_id'] != diagnostic['run_id']


def test_same_intrabar_stop_and_target_is_conservative_stop_first(monkeypatch):
    fake_signals(monkeypatch, {0: 'LONG'})
    result = simulate([bar(0), bar(1, 100, high=D(112), low=D(88))])
    assert [row['reason'] for row in result['fills']] == ['SIGNAL_ENTRY', 'STOP_LOSS']
    assert D(result['fills'][-1]['price']) == 90
    assert result['metrics']['ambiguous_stop_first'] == 1
    assert result['fills'][-1]['execution_window']['order'] == 'UNKNOWN_STOP_FIRST'


def test_opening_target_precedes_unknown_intrabar_double_touch_and_never_gets_windfall(monkeypatch):
    fake_signals(monkeypatch, {0: 'LONG'})
    result = simulate([bar(0), bar(1), bar(2, 100, open=D(120), high=D(125), low=D(80))])
    assert result['fills'][-1]['reason'] == 'TAKE_PROFIT'
    assert D(result['fills'][-1]['price']) == 110
    assert result['fills'][-1]['execution_at'] == (START + timedelta(hours=2)).isoformat()
    assert result['metrics']['ambiguous_stop_first'] == 0


def test_gap_beyond_existing_stop_uses_observed_adverse_open_not_old_stop_price(monkeypatch):
    fake_signals(monkeypatch, {0: 'LONG'})
    result = simulate([bar(0), bar(1), bar(2, 80)], config(spread='2', slippage='.5'))
    assert result['fills'][-1]['reason'] == 'STOP_LOSS'
    assert D(result['fills'][-1]['price']) == D('78.5')
    assert D(result['fills'][-1]['reference_ohlc_price']) == 80


def test_ohlc_touch_is_not_executable_side_touch_with_nonzero_assumed_spread(monkeypatch):
    fake_signals(monkeypatch, {0: 'LONG'})
    # Decision model base=101, fixed TP=111.10; center high=TP still
    # leaves modeled sell side one unit below it, so no target execution.
    result = simulate([bar(0), bar(1, 100, high=D('111.1'), low=D(99))], config(spread='2'))
    assert len(result['fills']) == 1 and result['open_position'] is not None
    assert D(result['open_position']['take_profit']) == D('111.1')


def test_jump_before_entry_cannot_reanchor_old_brackets_to_a_more_favorable_plan(monkeypatch):
    fake_signals(monkeypatch, {0: 'LONG'})
    result = simulate([bar(0), bar(1, 120)])
    assert result['fills'] == []
    assert result['orders'][-1]['status'] == 'REJECTED'
    assert result['orders'][-1]['reason'] in ('BRACKET_INVALID_AT_FILL', 'BRACKET_ALREADY_TRIGGERED')
    assert D(result['orders'][-1]['stop_loss']) == 90
    assert D(result['orders'][-1]['take_profit']) == 110


def test_cost_accounting_and_balance_conservation_are_exact_and_inputs_frozen(monkeypatch):
    fake_signals(monkeypatch, {0: 'LONG'})
    bars = [bar(0), bar(1, 102, open=D(100), low=D(99), high=D(105)), bar(2, 110, high=D(120), low=D(100))]
    cfg = config(fee_rate='.001', spread='2', slippage='.5')
    first = simulate(bars, cfg)
    assert D(first['fills'][0]['price']) == D('101.5')
    assert D(first['fills'][-1]['price']) == D('111.15')
    assert D(first['metrics']['fees']) == D('.21265')
    assert D(first['metrics']['total_cost']) == D('3.21265')
    assert D(first['metrics']['net_pnl']) == D('9.43735')
    assert D(first['metrics']['final_equity']) == D(10000) + D(first['metrics']['realized_pnl']) + D(first['metrics']['unrealized_pnl'])
    assert first == simulate(bars, cfg)
    changed = simulate(bars, config(fee_rate='.002', spread='2', slippage='.5'))
    assert changed['run_id'] != first['run_id']
    assert D(changed['metrics']['fees']) > D(first['metrics']['fees'])
    assert D(changed['metrics']['net_pnl']) < D(first['metrics']['net_pnl'])
    json.dumps(first, allow_nan=False)


def test_missing_interval_cancels_next_open_intent_without_fabricating_fill(monkeypatch):
    fake_signals(monkeypatch, {0: 'LONG'})
    result = simulate([bar(0), bar(2)])
    assert result['fills'] == []
    assert result['orders'][-1]['reason'] == 'MISSING_NEXT_BAR_NO_FILL'
    assert result['metrics']['gap_count'] == 1


def test_existing_position_across_gap_uses_only_next_observed_protection_and_discloses_unknown_path(monkeypatch):
    fake_signals(monkeypatch, {0: 'LONG'})
    result = simulate([bar(0), bar(1), bar(5, 80)])
    assert result['fills'][-1]['execution_at'] == (START + timedelta(hours=5)).isoformat()
    assert result['fills'][-1]['price'] == '80'
    assert result['gaps'][0]['open_position_path_unknown'] is True


def test_opposite_signal_exits_first_and_requires_a_later_new_signal_to_reverse(monkeypatch):
    fake_signals(monkeypatch, {0: 'LONG', 1: 'SHORT', 3: 'SHORT'})
    result = simulate([bar(i) for i in range(5)])
    assert [(row['action'], row['direction']) for row in result['fills']] == [('OPEN', 'LONG'), ('CLOSE', 'LONG'), ('OPEN', 'SHORT')]
    assert result['fills'][1]['execution_at'] == (START + timedelta(hours=2)).isoformat()
    assert result['fills'][2]['execution_at'] == (START + timedelta(hours=4)).isoformat()


def test_same_direction_is_not_pyramided_and_no_signal_never_closes_position(monkeypatch):
    fake_signals(monkeypatch, {0: 'LONG', 1: 'LONG'})
    result = simulate([bar(i) for i in range(4)])
    assert len(result['fills']) == 1
    assert result['signals'][-1]['status'] == 'SAME_DIRECTION'
    assert result['open_position']['direction'] == 'LONG'


def news(at, *, event_at=None, source='SYNTHETIC_ARCHIVE'):
    events = () if event_at is None else (Event(event_id='synthetic-high', instruments=('BTCUSDT',), impact='HIGH',
                                              scheduled_at=event_at, known_at=START),)
    return News(source=source, observed_at=at, coverage_start=START - timedelta(days=1),
                coverage_end=START + timedelta(days=1), events=events)


def test_known_high_impact_blocks_uncovered_research_entry_but_not_existing_protection(monkeypatch):
    fake_signals(monkeypatch, {0: 'LONG'})
    cfg = config(news_sources=('SYNTHETIC_ARCHIVE',), news_pre_seconds=0, news_post_seconds=0)
    blocked = simulate([bar(0), bar(1)], cfg, news_snapshots=[news(START, event_at=START + timedelta(hours=1))])
    assert blocked['fills'] == [] and blocked['signals'][0]['status'] == 'HIGH_IMPACT_EVENT'
    held = simulate([bar(0), bar(1), bar(2, 80)], cfg,
                    news_snapshots=[news(START, event_at=START + timedelta(hours=2))])
    assert [row['reason'] for row in held['fills']] == ['SIGNAL_ENTRY', 'STOP_LOSS']


def test_strict_snapshot_selection_cannot_read_future_news_or_ignore_missing_coverage(monkeypatch):
    fake_signals(monkeypatch, {0: 'LONG'})
    cfg = config(news_sources=('SYNTHETIC_ARCHIVE',))
    bars = [bar(0), bar(1)]
    future = news(START + timedelta(hours=2))
    result = simulate(bars, cfg, news_snapshots=[future], news_policy='REQUIRE_HISTORICAL')
    assert result['fills'] == [] and result['signals'][0]['status'] == 'NEWS_UNAVAILABLE'
    assert result['news_coverage']['uncovered_checks'] > 0
    with pytest.raises(ValueError, match='Future-observed'):
        simulate(bars, cfg, news_snapshots=[news(START + timedelta(hours=3))])


def test_strict_trusted_fresh_complete_snapshots_allow_entry_without_claiming_authenticity(monkeypatch):
    fake_signals(monkeypatch, {0: 'LONG'})
    cfg = config(news_sources=('SYNTHETIC_ARCHIVE',))
    rows = [news(START + timedelta(hours=i)) for i in range(3)]
    result = simulate([bar(0), bar(1)], cfg, news_snapshots=rows, news_policy='REQUIRE_HISTORICAL')
    assert len(result['fills']) == 1
    assert result['news_coverage']['complete'] is True
    assert result['news_validated'] is False and result['live_eligible'] is False


def test_budget_and_quantity_limits_reject_instead_of_changing_fixed_size(monkeypatch):
    fake_signals(monkeypatch, {0: 'LONG'})
    result = simulate([bar(0), bar(1)], config(max_order_quote='99'))
    assert result['fills'] == [] and result['signals'][0]['status'] == 'COLLATERAL_OR_ORDER_LIMIT'
    with pytest.raises(ValueError):
        config(quantity='1.001')
    with pytest.raises(ValueError):
        config(max_quantity='.5')


def test_boundary_validation_rejects_future_unclosed_mixed_and_conflicting_news():
    bars = [bar(0), bar(1)]
    for broken in [bars[::-1], bars + [bars[-1]], bars[:-1] + [bars[-1].model_copy(update={'is_closed': False})],
                   bars[:-1] + [bars[-1].model_copy(update={'source': 'wrong'})]]:
        with pytest.raises(ValueError):
            simulate(broken)
    with pytest.raises(ValueError):
        simulate(bars, as_of=bars[0].close_time)
    with pytest.raises(ValueError):
        simulate(bars, news_policy='IGNORE_NEWS')
    with pytest.raises(ValueError, match='Authorize'):
        simulate(bars, news_snapshots=[news(START)])
    with pytest.raises(ValueError, match='Conflicting'):
        simulate(bars, config(news_sources=('SYNTHETIC_ARCHIVE',)),
                 news_snapshots=[news(START), news(START, event_at=START + timedelta(hours=1))])


def test_rolling_window_is_identical_to_public_live_rule_and_bounded_to_500(monkeypatch):
    observed = []
    original = engine.rule_analyze
    def inspect(rows, **options):
        observed.append(len(rows))
        return original(rows, **options)
    monkeypatch.setattr(engine, 'rule_analyze', inspect)
    bars = [bar(i) for i in range(505)]
    first = simulate(bars)
    assert max(observed) == 500 and observed[-5:] == [500] * 5
    assert first['signals'] == []
    count = len(observed)
    second = simulate(bars, config(fee_rate='.001'))
    assert len(observed) == count, 'cost pressure reruns reuse only immutable rule reports'
    assert first['manifest']['rule_history'] == 'EXACT_PUBLIC_ANALYZE_ROLLING_500'
    assert first['manifest']['data_sha256'] == second['manifest']['data_sha256']
    assert first['run_id'] != second['run_id']


def test_numeric_equal_decimal_representations_cannot_share_a_different_input_evidence_hash():
    original = range_fixture()
    represented = [row.model_copy(update={key: getattr(row, key).quantize(D('.000001'))
                                         for key in ('open', 'high', 'low', 'close')}) for row in original]
    assert original == represented  # Decimal equality is deliberately representation-insensitive.
    cfg = config(rule='RANGE_REVERSION')
    simulate(original, cfg)
    warm = simulate(represented, cfg)
    expected = engine.rule_analyze(represented, instrument='BTCUSDT', source='bitget.public',
                                   timeframe='1h', now=represented[-1].close_time, rule='RANGE_REVERSION')
    assert warm['signals'][-1]['evidence_sha256'] == engine.digest(expected['evidence'])
    engine.clear_signal_cache()
    assert warm == simulate(represented, cfg), 'one run_id must have the same receipt cold and warm'
