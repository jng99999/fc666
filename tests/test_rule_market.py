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


def revision_scene(instance):
    original = [rule_bar(i) for i in range(3)]
    revised = original[:-1] + [rule_bar(2, close=D('100.2'))]
    now = START + 3 * MINUTE
    instance.refresh('BTCUSDT', '1m', now, provider=lambda *args: original)
    instance.refresh('BTCUSDT', '1m', now + timedelta(seconds=1), provider=lambda *args: revised)
    return now, original, revised


def test_revised_baseline_requires_later_identical_complete_snapshot_and_preserves_metadata(market):
    instance, _, _, _ = market
    now, original, revised = revision_scene(instance)
    first = instance.read('BTCUSDT', '1m')
    assert first['error_reason'] == 'HISTORY_REVISED'
    assert first['bars'] == tuple(original)
    assert first['old_sha256'] == rule_market._snapshot_sha(original)
    assert first['new_sha256'] == rule_market._snapshot_sha(revised)
    assert len(first['old_sha256']) == len(first['new_sha256']) == 64
    assert first['revision_detected_at'] == now + timedelta(seconds=1)
    assert first['resume_after'] is None and first['resynchronized_at'] is None
    instance.refresh('BTCUSDT', '1m', now + timedelta(seconds=2), provider=lambda *args: revised)
    restored = instance.read('BTCUSDT', '1m')
    assert restored['error'] is False and restored['error_reason'] is None
    assert restored['bars'] == tuple(revised)
    assert restored['resume_after'] == revised[-1].close_time
    assert restored['resynchronized_at'] == now + timedelta(seconds=2)
    instance.refresh('BTCUSDT', '1m', now + MINUTE, provider=lambda *args: revised + [rule_bar(3)])
    later = instance.read('BTCUSDT', '1m')
    for key in ['old_sha256', 'new_sha256', 'revision_detected_at', 'resynchronized_at', 'resume_after']:
        assert later[key] == restored[key], 'normal data advancement retains revision diagnostics'


def test_same_checked_time_cannot_confirm_a_revised_snapshot(market):
    instance, _, _, _ = market
    now, _, revised = revision_scene(instance)
    same = now + timedelta(seconds=1)
    instance.refresh('BTCUSDT', '1m', same, provider=lambda *args: revised)
    assert instance.read('BTCUSDT', '1m')['error'] is True
    assert instance.read('BTCUSDT', '1m')['error_reason'] == 'HISTORY_RESYNC_PENDING'
    instance.refresh('BTCUSDT', '1m', same + timedelta(microseconds=1), provider=lambda *args: revised)
    assert instance.read('BTCUSDT', '1m')['error'] is False


def test_candidate_changes_never_confirm_until_two_consecutive_valid_snapshots_agree(market):
    instance, _, _, _ = market
    now, original, revised = revision_scene(instance)
    changed = original[:-1] + [rule_bar(2, close=D('100.3'))]
    for seconds, rows in [(2, changed), (3, revised)]:
        instance.refresh('BTCUSDT', '1m', now + timedelta(seconds=seconds), provider=lambda *args: rows)
        state = instance.read('BTCUSDT', '1m')
        assert state['error'] and state['error_reason'] == 'HISTORY_RESYNC_PENDING'
        assert state['bars'] == tuple(original)
        assert state['new_sha256'] == rule_market._snapshot_sha(rows)
    instance.refresh('BTCUSDT', '1m', now + timedelta(seconds=4), provider=lambda *args: revised)
    assert instance.read('BTCUSDT', '1m')['error'] is False


@pytest.mark.parametrize('invalid', ['wrong_source', 'future', 'empty', 'transport'])
def test_invalid_intervening_fetch_breaks_candidate_confirmation(market, invalid):
    instance, _, _, _ = market
    now, original, revised = revision_scene(instance)
    def failure(*args):
        if invalid == 'transport':
            raise RuntimeError('synthetic unavailable HTTP')
        if invalid == 'empty':
            return []
        if invalid == 'future':
            return revised + [rule_bar(3)]
        return revised[:-1] + [rule_bar(2, source='SYNTHETIC_WRONG_SOURCE')]
    instance.refresh('BTCUSDT', '1m', now + timedelta(seconds=2), provider=failure)
    failed = instance.read('BTCUSDT', '1m')
    assert failed['error'] and failed['bars'] == tuple(original)
    assert failed['error_reason'] == 'HISTORY_UNAVAILABLE'
    instance.refresh('BTCUSDT', '1m', now + timedelta(seconds=3), provider=lambda *args: revised)
    assert instance.read('BTCUSDT', '1m')['error'] is True
    instance.refresh('BTCUSDT', '1m', now + timedelta(seconds=4), provider=lambda *args: revised)
    assert instance.read('BTCUSDT', '1m')['error'] is False


def test_window_boundary_change_requires_a_second_whole_window_not_overlap_stitching(market):
    instance, _, _, _ = market
    now, original, revised = revision_scene(instance)
    next_window = revised[1:] + [rule_bar(3)]
    instance.refresh('BTCUSDT', '1m', now + MINUTE, provider=lambda *args: next_window)
    assert instance.read('BTCUSDT', '1m')['error'] is True
    assert instance.read('BTCUSDT', '1m')['bars'] == tuple(original)
    instance.refresh('BTCUSDT', '1m', now + MINUTE + timedelta(seconds=1), provider=lambda *args: next_window)
    restored = instance.read('BTCUSDT', '1m')
    assert restored['bars'] == tuple(next_window)
    assert restored['resume_after'] == now + MINUTE


def test_return_to_original_verified_baseline_restores_without_adopting_revised_data(market):
    instance, _, _, _ = market
    now, original, _ = revision_scene(instance)
    rejected = instance.read('BTCUSDT', '1m')
    instance.refresh('BTCUSDT', '1m', now + timedelta(seconds=2), provider=lambda *args: original)
    restored = instance.read('BTCUSDT', '1m')
    assert restored['error'] is False and restored['bars'] == tuple(original)
    assert restored['resume_after'] == original[-1].close_time
    assert restored['resynchronized_at'] == now + timedelta(seconds=2)
    assert restored['old_sha256'] == rejected['old_sha256'] and restored['new_sha256'] == rejected['new_sha256']


def test_missing_revised_row_does_not_masquerade_as_return_to_original_baseline(market):
    instance, _, _, _ = market
    now, original, _ = revision_scene(instance)
    truncated = original[:2]
    instance.refresh('BTCUSDT', '1m', now + timedelta(seconds=2), provider=lambda *args: truncated)
    assert instance.read('BTCUSDT', '1m')['error'] is True


def test_durable_recovery_watermark_forces_two_complete_snapshots_after_process_restart(market):
    instance, _, _, reopen = market
    now, _, revised = revision_scene(instance)
    restarted = reopen()
    for seconds in [2, 3]:
        restarted.refresh('BTCUSDT', '1m', now + timedelta(seconds=seconds),
            provider=lambda *args: revised, recovery_after=now)
        state = restarted.read('BTCUSDT', '1m')
        assert state['error'] is (seconds == 2)
    assert state['resume_after'] == revised[-1].close_time
    assert state['resynchronized_at'] == now + timedelta(seconds=3)
    restarted.refresh('BTCUSDT', '1m', now + MINUTE,
        provider=lambda *args: revised + [rule_bar(3)], recovery_after=now)
    assert restarted.read('BTCUSDT', '1m')['error'] is False, 'same persisted watermark does not repeatedly quarantine'


def test_non_aligned_detection_time_requires_confirmation_not_a_later_candle(market):
    instance, _, _, _ = market
    now = START + 3 * MINUTE
    watermark = now + timedelta(seconds=20)
    rows = [rule_bar(i) for i in range(3)]
    for seconds in [30, 31]:
        instance.refresh('BTCUSDT', '1m', now + timedelta(seconds=seconds),
            provider=lambda *args: rows, recovery_after=watermark)
        assert instance.read('BTCUSDT', '1m')['error'] is (seconds == 30)
    restored = instance.read('BTCUSDT', '1m')
    assert restored['error'] is False and restored['resume_after'] == now
    assert restored['resynchronized_at'] == now + timedelta(seconds=31)
    instance.refresh('BTCUSDT', '1m', now + timedelta(seconds=32),
        provider=lambda *args: rows, recovery_after=watermark)
    assert instance.read('BTCUSDT', '1m')['error'] is False
    assert instance.read('BTCUSDT', '1m')['resynchronized_at'] == restored['resynchronized_at']


def test_original_baseline_return_clears_wall_clock_recovery_without_repeated_quarantine(market):
    instance, _, _, _ = market
    now, original, _ = revision_scene(instance)
    detected = now + timedelta(seconds=1)
    instance.refresh('BTCUSDT', '1m', now + timedelta(seconds=2),
        provider=lambda *args: original, recovery_after=detected)
    restored = instance.read('BTCUSDT', '1m')
    assert restored['error'] is False and restored['resume_after'] == now
    assert restored['resynchronized_at'] > detected
    instance.refresh('BTCUSDT', '1m', now + timedelta(seconds=3),
        provider=lambda *args: original, recovery_after=detected)
    assert instance.read('BTCUSDT', '1m')['error'] is False
    assert instance.read('BTCUSDT', '1m')['resynchronized_at'] == restored['resynchronized_at']


@pytest.mark.parametrize('exception', [RuntimeError('synthetic durable guard failure'), SystemExit('synthetic stop')])
def test_state_callback_failure_is_rethrown_and_never_publishes_tradable_recovery(market, exception):
    instance, _, _, _ = market
    now, original, revised = revision_scene(instance)
    seen = []
    def callback(value):
        assert instance.read('BTCUSDT', '1m') == value  # Reentrant lock; visible state is staged atomically.
        seen.append(value)
        raise exception
    with pytest.raises(type(exception)):
        instance.refresh('BTCUSDT', '1m', now + timedelta(seconds=2),
                         provider=lambda *args: revised, on_state=callback)
    isolated = instance.read('BTCUSDT', '1m')
    assert seen[0]['error'] is False
    assert isolated['error'] is True and isolated['bars'] == tuple(original)
    assert isolated['error_reason'] == 'HISTORY_RESYNC_PENDING'
    assert isolated['resynchronized_at'] is None
    published = []
    instance.refresh('BTCUSDT', '1m', now + timedelta(seconds=3),
                     provider=lambda *args: revised, on_state=published.append)
    assert published[-1]['error'] is False and instance.read('BTCUSDT', '1m')['error'] is False


def test_first_snapshot_callback_failure_does_not_leave_any_executable_bars(market):
    instance, _, _, _ = market
    now = START + 3 * MINUTE
    rows = [rule_bar(i) for i in range(3)]
    def failed(value):
        raise RuntimeError('synthetic initial durable-write error')
    with pytest.raises(RuntimeError):
        instance.refresh('BTCUSDT', '1m', now, provider=lambda *args: rows, on_state=failed)
    assert instance.read('BTCUSDT', '1m')['error'] is True
    assert instance.read('BTCUSDT', '1m')['bars'] == ()


def test_initial_read_exposes_all_recovery_metadata_with_neutral_defaults(market):
    instance, _, _, _ = market
    initial = instance.read('BTCUSDT', '1m')
    for key in ['error_reason', 'revision_detected_at', 'resynchronized_at', 'resume_after', 'old_sha256', 'new_sha256']:
        assert key in initial and initial[key] is None
