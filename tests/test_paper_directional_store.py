from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import json
import sqlite3

import pytest

from core.backtest.spot import digest
from core.paper.directional import Config, Event, News, Proposal, Quote
from core.paper.directional_store import Conflict, Store

BASE = datetime(2026, 10, 27, 12, tzinfo=timezone.utc)


def config():
    return Config(mode='RESEARCH_PAPER', instrument='XAU/USD', quote_source='TEST_ONLY', news_sources=('TEST_CALENDAR',),
                  initial_cash='10000', tick_size='.01', quantity_step='.01', max_quantity='10', max_order_quote='9000',
                  fee_rate='.001', slippage='.05', quote_max_age_seconds=10, decision_ttl_seconds=60,
                  news_max_age_seconds=120, news_pre_seconds=30, news_post_seconds=60)


def quote(seconds=0, bid='2000', ask='2000.5'):
    return Quote(instrument='XAU/USD', source='TEST_ONLY', at=BASE + timedelta(seconds=seconds), bid=bid, ask=ask)


def news(seconds=0, *, high=False):
    events = () if not high else (Event(event_id='test-event', instruments=('XAU/USD',), impact='HIGH',
                                      scheduled_at=BASE, known_at=BASE - timedelta(hours=1)),)
    return News(source='TEST_CALENDAR', observed_at=BASE + timedelta(seconds=seconds),
                coverage_start=BASE - timedelta(hours=1), coverage_end=BASE + timedelta(hours=1), events=events)


def proposal(target='LONG', *, request_id='decision-1', at=BASE):
    return Proposal(request_id=request_id, target=target, available_at=at, quantity='0' if target == 'FLAT' else '1',
                    stop_loss=None if target == 'FLAT' else '1990' if target == 'LONG' else '2010',
                    take_profit=None if target == 'FLAT' else '2010' if target == 'LONG' else '1990')


def create(tmp_path, *, max_operations=10000, metadata=None):
    store = Store(tmp_path / 'directional.sqlite3', max_operations=max_operations)
    view = store.create('owner-a', config(), 'create-1', BASE, metadata)
    return store, view['account_id']


def open_position(tmp_path, target='LONG', *, max_operations=10000, metadata=None):
    store, account_id = create(tmp_path, max_operations=max_operations, metadata=metadata)
    view = store.on_quote(account_id, quote(), BASE, news())
    view = store.submit('owner-a', account_id, proposal(target), view['revision'], BASE, news())
    assert view['decision_result']['status'] == 'ACCEPTED'
    view = store.on_quote(account_id, quote(1), quote(1).at, news())
    assert view['position']['direction'] == target
    return store, account_id, view


@pytest.mark.parametrize('target', ['LONG', 'SHORT'])
@pytest.mark.parametrize('reason', ['STOP_LOSS', 'TAKE_PROFIT'])
def test_restart_restores_pending_and_brackets_then_charges_real_bid_ask_costs(tmp_path, target, reason):
    store, account_id = create(tmp_path)
    view = store.on_quote(account_id, quote(), BASE, news())
    store.submit('owner-a', account_id, proposal(target), view['revision'], BASE, news())
    restarted = Store(store.path)
    assert restarted.read('owner-a', account_id)['pending']['target'] == target
    opened = restarted.on_quote(account_id, quote(1), quote(1).at, news())
    assert len(opened['fills']) == 1
    if (target == 'LONG') == (reason == 'TAKE_PROFIT'):
        closing = quote(2, '2011', '2011.5')
    else:
        closing = quote(2, '1988.5', '1989')
    result = Store(store.path).on_quote(account_id, closing, closing.at, None)
    assert result['position'] is None
    assert result['fills'][-1]['reason'] == reason
    assert len(result['fills']) == 2
    assert Decimal(result['balance']) == Decimal('10000') + Decimal(result['gross_pnl']) - Decimal(result['fees'])
    assert result['trading_enabled'] is False


def test_create_retries_are_owner_scoped_and_parameter_conflicts_do_not_overwrite(tmp_path):
    store, account_id = create(tmp_path, metadata={'strategy': 'MANUAL'})
    again = store.create('owner-a', config(), 'create-1', BASE + timedelta(seconds=10), {'strategy': 'MANUAL'})
    assert again['account_id'] == account_id and again['revision'] == 1
    with pytest.raises(Conflict):
        store.create('owner-a', config(), 'create-1', BASE, {'strategy': 'SMA20_QUOTES'})
    other = store.create('owner-b', config(), 'create-1', BASE)
    assert other['account_id'] != account_id
    with pytest.raises(KeyError):
        store.read('owner-b', account_id)
    assert len(store.listing('owner-a')) == 1
    assert 'owner' not in store.active()[0]


def test_same_decision_and_quote_retry_after_restart_never_fills_twice(tmp_path):
    store, account_id, opened = open_position(tmp_path)
    retry = Store(store.path).submit('owner-a', account_id, proposal(), 1, BASE + timedelta(seconds=2), None)
    assert retry['decision_result']['status'] == 'ACCEPTED'
    assert retry['revision'] == opened['revision'] and len(retry['fills']) == 1
    duplicate = Store(store.path).on_quote(account_id, quote(1), quote(1).at, None)
    assert duplicate == Store(store.path).read('owner-a', account_id)
    assert duplicate['revision'] == opened['revision']
    with pytest.raises(Conflict):
        store.submit('owner-a', account_id, proposal('SHORT'), opened['revision'], quote(1).at, news())
    saved = store.decision_request('owner-a', account_id, 'decision-1')
    assert saved == proposal().model_dump(mode='json')
    with store.connection() as db:
        raw = db.execute("SELECT payload FROM directional_operations WHERE request_key='decision:decision-1'").fetchone()[0]
        original_context = json.loads(raw)['input']
    assert original_context['quote'] == quote().model_dump(mode='json')
    assert original_context['recent_quotes'] == [quote().model_dump(mode='json')]
    assert original_context['news'] == news().model_dump(mode='json')


def test_concurrent_duplicate_quote_and_decisions_have_single_durable_fill(tmp_path):
    store, account_id = create(tmp_path)
    view = store.on_quote(account_id, quote(), BASE, news())
    def submit(_):
        return Store(store.path).submit('owner-a', account_id, proposal(), view['revision'], BASE, news())
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(submit, range(8)))
    assert {r['revision'] for r in results} == {view['revision'] + 1}
    def advance(_):
        return Store(store.path).on_quote(account_id, quote(1), quote(1).at, news())
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(advance, range(8)))
    assert {r['fills_count'] for r in results} == {1}
    assert {r['revision'] for r in results} == {view['revision'] + 2}


@pytest.mark.parametrize('snapshot,reason', [(None, 'NEWS_UNAVAILABLE'), (news(-121), 'NEWS_STALE'), (news(high=True), 'HIGH_IMPACT_EVENT')])
def test_unknown_stale_high_impact_news_blocks_entries_without_fabricated_fills(tmp_path, snapshot, reason):
    store, account_id = create(tmp_path)
    view = store.on_quote(account_id, quote(), BASE, None)
    result = store.submit('owner-a', account_id, proposal(), view['revision'], BASE, snapshot)
    assert result['decision_result'] == {'status': 'REJECTED', 'reason': reason}
    assert result['last_outcome']['status'] == 'REJECTED' and result['last_outcome']['reason'] == reason
    assert result['fills'] == [] and result['pending'] is None


def test_news_rechecked_at_fill_and_cannot_disable_existing_stop(tmp_path):
    store, account_id = create(tmp_path)
    view = store.on_quote(account_id, quote(), BASE, news())
    store.submit('owner-a', account_id, proposal(), view['revision'], BASE, news())
    rejected = store.on_quote(account_id, quote(1), quote(1).at, None)
    assert rejected['pending'] is None and rejected['position'] is None and not rejected['fills']
    assert rejected['last_outcome']['kind'] == 'ENTRY_REJECTED'
    assert rejected['last_outcome']['reason'] == 'NEWS_UNAVAILABLE'
    refreshed = Store(store.path).on_quote(account_id, quote(2), quote(2).at, None)
    assert refreshed['last_outcome'] == rejected['last_outcome']
    again = store.submit('owner-a', account_id, proposal(at=quote(2).at, request_id='second'), refreshed['revision'], quote(2).at, news())
    opened = store.on_quote(account_id, quote(3), quote(3).at, news())
    assert opened['fills_count'] == 1
    closed = store.on_quote(account_id, quote(4, '1980', '1981'), quote(4).at, news(high=True))
    assert closed['fills_count'] == 2 and closed['position'] is None
    assert closed['last_outcome']['status'] == 'FILLED' and closed['last_outcome']['reason'] == 'STOP_LOSS'


def test_stop_cancels_pending_but_keeps_position_protection_across_restart(tmp_path):
    store, account_id, view = open_position(tmp_path)
    stopped = store.stop('owner-a', account_id, 'stop-1', view['revision'], quote(1).at)
    assert stopped['status'] == 'STOPPED' and stopped['entry_halted'] is True
    assert stopped['position'] is not None and stopped['pending'] is None
    assert Store(store.path).active()[0]['account_id'] == account_id
    retry = store.stop('owner-a', account_id, 'stop-1', view['revision'], quote(1).at)
    assert retry['revision'] == stopped['revision']
    with pytest.raises(Conflict):
        store.submit('owner-a', account_id, proposal(request_id='new', at=quote(1).at), stopped['revision'], quote(1).at, news())
    closed = Store(store.path).on_quote(account_id, quote(2, '1980', '1981'), quote(2).at, None)
    assert closed['fills'][-1]['reason'] == 'STOP_LOSS' and closed['position'] is None
    assert Store(store.path).active() == []


def test_stop_before_fill_atomically_cancels_pending_without_deleting_evidence(tmp_path):
    store, account_id = create(tmp_path)
    view = store.on_quote(account_id, quote(), BASE, news())
    accepted = store.submit('owner-a', account_id, proposal(), view['revision'], BASE, news())
    stopped = store.stop('owner-a', account_id, 'stop-1', accepted['revision'], BASE)
    assert stopped['pending'] is None
    after = store.on_quote(account_id, quote(1), quote(1).at, news())
    assert after['fills_count'] == 0
    assert store.decision_request('owner-a', account_id, 'decision-1') is not None


def test_limit_stops_new_risk_with_bounded_idle_checkpoint_but_preserves_protection(tmp_path):
    store, account_id, opened = open_position(tmp_path, max_operations=4)
    # Only create/decision/fill count; quote refreshes cannot exhaust the budget.
    assert opened['revision'] == 3
    blocked = store.submit('owner-a', account_id, proposal('SHORT', request_id='opposite', at=quote(1).at),
                           opened['revision'], quote(1).at, news())
    assert blocked['revision'] == 4
    stopped = store.on_quote(account_id, quote(2), quote(2).at, None)
    assert stopped['status'] == 'STOPPED' and stopped['position'] is not None
    revision = stopped['revision']
    for second in range(3, 50):
        view = Store(store.path).on_quote(account_id, quote(second), quote(second).at, None)
        assert view['revision'] == revision
    assert len(view['recent_quotes']) == 20
    assert view['last_quote']['at'] == quote(49).model_dump(mode='json')['at']
    assert Store(store.path).read('owner-a', account_id)['position'] is not None
    closed = Store(store.path).on_quote(account_id, quote(50, '1980', '1981'), quote(50).at, None)
    assert closed['revision'] == revision + 1
    assert closed['fills_count'] == 2 and closed['position'] is None
    assert closed['fills'][-1]['reason'] == 'STOP_LOSS'
    with store.connection() as db:
        assert db.execute('SELECT count(*) FROM directional_operations').fetchone()[0] == revision + 1


def test_more_than_ten_thousand_idle_ticks_do_not_consume_trade_operation_budget(tmp_path):
    store, account_id = create(tmp_path, max_operations=2)
    for second in range(10050):
        view = store.on_quote(account_id, quote(second), quote(second).at, None)
    assert view['status'] == 'RUNNING' and view['revision'] == 1 and view['events'] == 0
    assert len(view['recent_quotes']) == 20 and view['last_quote']['at'] == quote(10049).model_dump(mode='json')['at']
    with store.connection() as db:
        assert db.execute('SELECT count(*) FROM directional_operations').fetchone()[0] == 1
    assert Store(store.path).read('owner-a', account_id)['status'] == 'RUNNING'


def test_quote_gap_resets_automatic_warmup_but_never_delays_protection(tmp_path):
    store, account_id, opened = open_position(tmp_path, metadata={'strategy': 'SMA20_QUOTES'})
    for second in range(2, 22):
        before = store.on_quote(account_id, quote(second), quote(second).at, None)
    assert len(before['recent_quotes']) == 20
    after = Store(store.path).on_quote(account_id, quote(100, '1980', '1981'), quote(100).at, None)
    assert after['position'] is None and after['fills'][-1]['reason'] == 'STOP_LOSS'
    assert len(after['recent_quotes']) == 1
    assert after['recent_quotes'][0]['at'] == quote(100).model_dump(mode='json')['at']


def test_actual_ten_thousand_trade_operation_limit_keeps_final_protective_exit(tmp_path):
    store, account_id, view = open_position(tmp_path)
    rejected_at_limit = None
    stopped_conflicts = 0
    for index in range(10050):
        try:
            view = store.submit('owner-a', account_id, proposal('SHORT', request_id=f'op-{index}', at=quote(1).at),
                                view['revision'], quote(1).at, news())
            if view['status'] == 'STOPPED':
                rejected_at_limit = view
        except Conflict:
            stopped_conflicts += 1
    assert rejected_at_limit['revision'] == 10001 and rejected_at_limit['position'] is not None
    assert stopped_conflicts > 0
    closed = Store(store.path).on_quote(account_id, quote(2, '1980', '1981'), quote(2).at, None)
    assert closed['revision'] == 10002 and closed['position'] is None
    assert closed['fills_count'] == 2 and closed['fills'][-1]['reason'] == 'STOP_LOSS'


def test_revision_conflict_and_failed_quote_leave_checkpoint_unchanged(tmp_path):
    store, account_id, before = open_position(tmp_path)
    with pytest.raises(Conflict):
        store.stop('owner-a', account_id, 'bad-stop', before['revision'] - 1, quote(1).at)
    with pytest.raises(ValueError):
        store.on_quote(account_id, quote(2), quote(50).at, None)
    with pytest.raises(Conflict):
        store.on_quote(account_id, quote(1, '2002', '2003'), quote(1).at, None)
    assert store.read('owner-a', account_id) == before


def test_store_refuses_legacy_db_and_detects_corrupt_checkpoint_or_fill(tmp_path):
    foreign = tmp_path / 'legacy.sqlite3'
    with sqlite3.connect(foreign) as db:
        db.execute('CREATE TABLE accounts(id TEXT)')
    with pytest.raises(ValueError, match='unrelated'):
        Store(foreign)
    store, account_id, _ = open_position(tmp_path)
    with store.connection() as db:
        with pytest.raises(sqlite3.IntegrityError, match='Immutable'):
            db.execute('DELETE FROM directional_fills')
        state = json.loads(db.execute('SELECT state FROM directional_accounts').fetchone()[0])
        state['balance'] = '9000000'
        db.execute('UPDATE directional_accounts SET state=?,sha256=?', (json.dumps(state), digest(state)))
    with pytest.raises(ValueError, match='mismatch'):
        store.read('owner-a', account_id)


def test_automatic_entry_is_restricted_to_running_baseline_metadata(tmp_path):
    store, account_id = create(tmp_path, metadata={'strategy': 'SMA20_QUOTES'})
    view = store.on_quote(account_id, quote(), BASE, news())
    result = store.submit_automatic(account_id, proposal(), view['revision'], BASE, news())
    assert result['decision_result']['status'] == 'ACCEPTED'
    store.stop('owner-a', account_id, 'stop', result['revision'], BASE)
    with pytest.raises(Conflict):
        store.submit_automatic(account_id, proposal(request_id='second'), result['revision'] + 1, BASE, news())
    other = store.create('owner-a', config(), 'create-2', BASE, {'strategy': 'MANUAL'})
    with pytest.raises(Conflict):
        store.submit_automatic(other['account_id'], proposal(), other['revision'], BASE, news())
