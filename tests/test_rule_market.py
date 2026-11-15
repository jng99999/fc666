"""Isolated synthetic fixtures, fake public clients and temporary SQLite only.

No external HTTP, production quote injection, account or trading credentials.
"""
from datetime import datetime, timedelta, timezone
from decimal import Decimal as D

import pytest

from core.models import Candle
from core.paper.directional import Quote
from core.paper.price_action_rules import RuleBar
from core.paper import rule_market


START = datetime(2025, 1, 6, tzinfo=timezone.utc)
MINUTE = timedelta(minutes=1)


class FakePublic:
    def __init__(self):
        self.rows = []
        self.server_ms = int((START + 3 * MINUTE).timestamp() * 1000)
        self.server_calls = 0
        self.candle_calls = []
        self.error = None

    def server_time(self):
        self.server_calls += 1
        if self.error:
            raise self.error
        return self.server_ms

    def candles(self, *args):
        self.candle_calls.append(args)
        return self.rows


@pytest.fixture
def market(tmp_path, monkeypatch):
    public = FakePublic()
    monkeypatch.setattr(rule_market, 'BitgetPublic', lambda client: public)
    clock = [100.0]
    monkeypatch.setattr(rule_market.time, 'monotonic', lambda: clock[0])
    opened = []

    def create():
        instance = rule_market.RuleMarket(tmp_path / 'synthetic-rule-market.sqlite3')
        opened.append(instance)
        return instance

    first = create()
    yield first, public, clock, create
    for instance in opened:
        instance.close()


def gold_quote(stamp, bid='3300', **updates):
    bid = D(str(bid))
    value = dict(instrument='XAU/USD', source=rule_market.bar_source('XAU/USD'),
                 at=stamp, bid=bid, ask=bid + D('.2'))
    value.update(updates)
    return Quote(**value)


def sample(instance, minute=0, offsets=(0, 10, 20, 30, 40, 50)):
    for index, seconds in enumerate(offsets):
        at = START + minute * MINUTE + timedelta(seconds=seconds)
        instance.observe(gold_quote(at, D(3300) + index), at)


def crypto(index, **updates):
    values = dict(instrument_id='bitget:spot:BTC-USDT', source='bitget.public',
                  timeframe='1m', open_time=START + index * MINUTE,
                  close_time=START + (index + 1) * MINUTE,
                  open=D(100), high=D(101), low=D(99), close=D(100),
                  volume=D(10), is_closed=True)
    values.update(updates)
    return Candle(**values)


def rule_bar(index, **updates):
    row = crypto(index)
    values = dict(instrument='BTCUSDT', source=row.source, timeframe=row.timeframe,
                  open_time=row.open_time, close_time=row.close_time,
                  open=row.open, high=row.high, low=row.low, close=row.close,
                  volume=row.volume, is_closed=True)
    values.update(updates)
    return RuleBar(**values)


def test_gold_only_complete_real_observed_minute_closes_and_never_invents_volume(market):
    instance, public, _, _ = market
    sample(instance)
    assert instance.fetch('XAU/USD', '1m', START + MINUTE - timedelta(microseconds=1)) == []
    bars = instance.fetch('XAU/USD', '1m', START + MINUTE)
    assert len(bars) == 1
    row = bars[0]
    assert row.open_time == START and row.close_time == START + MINUTE
    assert (row.open, row.high, row.low, row.close) == (D('3300.1'), D('3305.1'), D('3300.1'), D('3305.1'))
    assert row.volume is None and row.is_closed is True
    assert row.source == rule_market.bar_source('XAU/USD')
    assert public.server_calls == 0 and public.candle_calls == []


@pytest.mark.parametrize(('offsets', 'expected'), [
    ((21, 30, 40, 50), 0),   # Missing first 20 seconds.
    ((0, 10, 20, 30, 39), 0),  # Missing final 20 seconds.
    ((0, 10, 50), 0),       # A 40-second internal sampling outage.
    ((20, 40), 1),          # Existing explicitly documented coverage tolerance.
    ((0, 30, 59), 1),       # Exactly 30 seconds is allowed, not silently tightened.
])
def test_gold_minute_requires_both_edges_and_internal_coverage(market, offsets, expected):
    instance, _, _, _ = market
    sample(instance, offsets=offsets)
    assert len(instance.fetch('XAU/USD', '1m', START + MINUTE)) == expected


def test_gold_missing_minutes_are_not_forward_filled_and_quotes_survive_restart(market):
    instance, _, _, reopen = market
    sample(instance, 0)
    sample(instance, 2)
    before = instance.fetch('XAU/USD', '1m', START + 3 * MINUTE)
    assert [bar.open_time for bar in before] == [START, START + 2 * MINUTE]
    second = reopen()
    assert second.fetch('XAU/USD', '1m', START + 3 * MINUTE) == before
    with second.connection() as db:
        assert db.execute('SELECT COUNT(*) FROM gold_samples').fetchone()[0] == 12
    assert all(bar.volume is None for bar in before)


@pytest.mark.parametrize(('change', 'now'), [
    ({'source': 'synthetic-wrong-source'}, START),
    ({}, START + timedelta(seconds=61)),
    ({'at': START + timedelta(seconds=1)}, START),
])
def test_bad_source_stale_or_future_gold_quote_is_not_persisted(market, change, now):
    instance, _, _, _ = market
    with pytest.raises(ValueError):
        instance.observe(gold_quote(START, **change), now)
    with instance.connection() as db:
        assert db.execute('SELECT COUNT(*) FROM gold_samples').fetchone()[0] == 0


def test_same_timestamp_revised_gold_quote_rejected_original_samples_retained(market):
    instance, _, _, _ = market
    sample(instance)
    original = instance.fetch('XAU/USD', '1m', START + MINUTE)
    with pytest.raises(ValueError, match='revised'):
        instance.observe(gold_quote(START, '3400'), START + timedelta(seconds=1))
    instance.observe(gold_quote(START), START + timedelta(seconds=1))  # Exact duplicate is idempotent.
    assert instance.fetch('XAU/USD', '1m', START + MINUTE) == original
    with instance.connection() as db:
        assert db.execute('SELECT COUNT(*) FROM gold_samples').fetchone()[0] == 6


def test_late_gold_sample_cannot_silently_rewrite_an_already_cached_closed_bar(market):
    instance, _, clock, _ = market
    sample(instance, offsets=(0, 10, 20, 30, 40))
    instance.refresh('XAU/USD', '1m', START + MINUTE)
    original = instance.read('XAU/USD', '1m')
    assert not original['error'] and len(original['bars']) == 1
    instance.observe(gold_quote(START + timedelta(seconds=50), '3310'), START + timedelta(seconds=65))
    clock[0] += 6
    instance.refresh('XAU/USD', '1m', START + 2 * MINUTE)
    revised = instance.read('XAU/USD', '1m')
    assert revised['error'] is True
    assert revised['bars'] == original['bars']


def test_crypto_public_fetch_freezes_explicit_closed_scope_and_window(market):
    instance, public, _, _ = market
    public.rows = [crypto(i) for i in range(3)]
    now = START + 3 * MINUTE + timedelta(seconds=20)
    public.server_ms = int(now.timestamp() * 1000)
    instance.refresh('BTCUSDT', '1m', now)
    cached = instance.read('BTCUSDT', '1m')
    assert not cached['error'] and len(cached['bars']) == 3
    assert cached['checked_at'] == now
    assert all(row.instrument == 'BTCUSDT' and row.source == 'bitget.public'
               and row.timeframe == '1m' and row.is_closed for row in cached['bars'])
    duration = 60_000
    end = int((START + 3 * MINUTE).timestamp() * 1000)
    assert public.candle_calls == [('BTCUSDT', '1m', end - 500 * duration, end, int(now.timestamp() * 1000))]


@pytest.mark.parametrize('row', [crypto(0, instrument_id='bitget:spot:ETH-USDT'),
                                  crypto(0, is_closed=False)])
def test_crypto_fetch_must_not_relabel_wrong_product_or_unclosed_metadata(market, row):
    instance, public, _, _ = market
    public.rows = [row]
    instance.refresh('BTCUSDT', '1m', START + 3 * MINUTE)
    assert instance.read('BTCUSDT', '1m')['error'] is True


@pytest.mark.parametrize('kind', ['source', 'instrument', 'timeframe', 'future', 'order', 'duplicate', 'over500'])
def test_crypto_refresh_fails_closed_for_invalid_history(market, kind):
    instance, _, _, _ = market
    rows = [rule_bar(i) for i in range(3)]
    if kind == 'source':
        rows[1] = rule_bar(1, source='synthetic-wrong-feed')
    elif kind == 'instrument':
        rows[1] = rule_bar(1, instrument='ETHUSDT')
    elif kind == 'timeframe':
        rows = [rule_bar(0, timeframe='5m', close_time=START + 5 * MINUTE)]
    elif kind == 'future':
        rows.append(rule_bar(3))
    elif kind == 'order':
        rows.reverse()
    elif kind == 'duplicate':
        rows.append(rows[-1])
    else:
        rows = [rule_bar(i) for i in range(501)]
    instance.refresh('BTCUSDT', '1m', START + 3 * MINUTE, provider=lambda *args: rows)
    assert instance.read('BTCUSDT', '1m')['error'] is True
    assert instance.read('BTCUSDT', '1m')['bars'] == ()


@pytest.mark.parametrize('changes', [{'is_closed': False}, {'high': D(98)},
                                      {'volume': D('NaN')},
                                      {'open_time': START + timedelta(seconds=30)}])
def test_model_copy_cannot_bypass_closed_cache_boundary_validation(market, changes):
    instance, _, _, _ = market
    forged = rule_bar(0).model_copy(update=changes)
    instance.refresh('BTCUSDT', '1m', START + 3 * MINUTE, provider=lambda *args: [forged])
    rejected = instance.read('BTCUSDT', '1m')
    assert rejected['error'] is True
    assert rejected['bars'] == ()


def test_closed_revision_blocks_execution_preserves_last_good_and_still_checks_later(market):
    instance, _, _, _ = market
    now = START + 3 * MINUTE
    original = [rule_bar(i) for i in range(3)]
    instance.refresh('BTCUSDT', '1m', now, provider=lambda *args: original)
    assert instance.read('BTCUSDT', '1m')['error'] is False
    revised = original[:]
    revised[-1] = rule_bar(2, close=D('100.2'))
    instance.refresh('BTCUSDT', '1m', now, provider=lambda *args: revised)
    rejected = instance.read('BTCUSDT', '1m')
    assert rejected['error'] is True
    assert rejected['bars'] == tuple(original), 'retained bytes are diagnostic only while error=True'
    calls = []
    def recovered(*args):
        calls.append(args)
        return original + [rule_bar(3)]
    instance.refresh('BTCUSDT', '1m', now + MINUTE, provider=recovered)
    assert len(calls) == 1
    assert instance.read('BTCUSDT', '1m')['error'] is False
    assert instance.read('BTCUSDT', '1m')['bars'][-1].close_time == now + MINUTE


def test_same_closed_boundary_does_not_repeat_public_http_but_new_boundary_refreshes(market):
    instance, public, clock, _ = market
    public.rows = [crypto(i) for i in range(3)]
    now = START + 3 * MINUTE + timedelta(seconds=1)
    public.server_ms = int(now.timestamp() * 1000)
    instance.refresh('BTCUSDT', '1m', now)
    clock[0] += 10
    instance.refresh('BTCUSDT', '1m', now + timedelta(seconds=40))
    assert public.server_calls == 1 and len(public.candle_calls) == 1
    clock[0] += 10
    public.rows.append(crypto(3))
    later = now + MINUTE
    public.server_ms = int(later.timestamp() * 1000)
    instance.refresh('BTCUSDT', '1m', later)
    assert public.server_calls == 2 and len(public.candle_calls) == 2
    assert instance.read('BTCUSDT', '1m')['bars'][-1].close_time == START + 4 * MINUTE


def test_error_cache_rechecks_same_boundary_after_backoff_instead_of_remaining_stuck(market):
    instance, public, clock, _ = market
    public.rows = [crypto(i) for i in range(3)]
    now = START + 3 * MINUTE
    instance.refresh('BTCUSDT', '1m', now)
    original = public.rows[:]
    public.rows = original[:-1] + [crypto(2, close=D('100.2')), crypto(3)]
    now += MINUTE
    public.server_ms = int(now.timestamp() * 1000)
    clock[0] += 6
    instance.refresh('BTCUSDT', '1m', now)
    assert instance.read('BTCUSDT', '1m')['error'] is True
    assert len(public.candle_calls) == 2
    public.rows = original + [crypto(3)]
    clock[0] += 6
    instance.refresh('BTCUSDT', '1m', now)
    assert len(public.candle_calls) == 3
    assert instance.read('BTCUSDT', '1m')['error'] is False
    assert len(instance.read('BTCUSDT', '1m')['bars']) == 4


def test_public_clock_mismatch_and_transport_errors_are_error_states_not_empty_success(market):
    instance, public, clock, _ = market
    now = START + 3 * MINUTE
    public.server_ms += 60_001
    instance.refresh('BTCUSDT', '1m', now)
    assert instance.read('BTCUSDT', '1m')['error'] is True
    assert public.candle_calls == []
    public.error = RuntimeError('synthetic public transport outage')
    clock[0] += 6
    instance.refresh('BTCUSDT', '1m', now)
    assert public.server_calls == 2
    assert instance.read('BTCUSDT', '1m')['error'] is True
