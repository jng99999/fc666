from copy import deepcopy
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest
from pydantic import ValidationError

from core.paper.directional import Config, DirectionalPaper, Event, News, Proposal, Quote, entry_gate

BASE = datetime(2026, 10, 20, 12, tzinfo=timezone.utc)


def config(**updates):
    return Config(mode='RESEARCH_PAPER', instrument='XAU/USD', quote_source='TEST_ONLY', news_sources=('TEST_CALENDAR',),
                  initial_cash='10000', tick_size='.01', quantity_step='.01', max_quantity='10', max_order_quote='9000',
                  fee_rate='.001', slippage='.05', quote_max_age_seconds=10, decision_ttl_seconds=60,
                  news_max_age_seconds=120, news_pre_seconds=30, news_post_seconds=60, **updates)


def quote(seconds=0, bid='2000', ask='2000.5', **updates):
    return Quote(instrument='XAU/USD', source='TEST_ONLY', at=BASE + timedelta(seconds=seconds), bid=bid, ask=ask, **updates)


def news(seconds=0, events=(), **updates):
    values = dict(source='TEST_CALENDAR', observed_at=BASE + timedelta(seconds=seconds),
                  coverage_start=BASE - timedelta(hours=1), coverage_end=BASE + timedelta(hours=1), events=events)
    return News(**{**values, **updates})


def proposal(target='LONG', **updates):
    values = dict(request_id='decision-1', target=target, available_at=BASE, quantity='1',
                  stop_loss='1990' if target == 'LONG' else '2010', take_profit='2010' if target == 'LONG' else '1990')
    if target == 'FLAT':
        values.update(quantity='0', stop_loss=None, take_profit=None)
    return Proposal(**{**values, **updates})


def opened(target='LONG'):
    engine = DirectionalPaper(config())
    engine.on_quote(quote(), now=BASE, news=news())
    assert engine.submit(proposal(target), now=BASE, news=news())['status'] == 'ACCEPTED'
    assert engine.fills == []
    engine.on_quote(quote(1), now=BASE + timedelta(seconds=1), news=news())
    assert len(engine.fills) == 1
    return engine


@pytest.mark.parametrize('target', ['LONG', 'SHORT'])
@pytest.mark.parametrize('exit_kind', ['STOP_LOSS', 'TAKE_PROFIT', 'SIGNAL_EXIT'])
def test_two_directions_brackets_fees_conservation_and_reduce_only_exit(target, exit_kind):
    engine = opened(target)
    if exit_kind == 'SIGNAL_EXIT':
        engine.submit(proposal('FLAT', request_id='exit', available_at=BASE + timedelta(seconds=1)),
                      now=BASE + timedelta(seconds=1), news=None)
        bid, ask = '2002', '2002.5'
    elif (exit_kind == 'TAKE_PROFIT') == (target == 'LONG'):
        bid, ask = '2011', '2011.5'
    else:
        bid, ask = '1988.5', '1989'
    view = engine.on_quote(quote(2, bid, ask), now=BASE + timedelta(seconds=2), news=None)
    assert view['position'] is None and len(view['fills']) == 2
    entry, exit = view['fills']
    assert exit['reason'] == exit_kind and exit['direction'] == target
    pnl = (Decimal(exit['price']) - Decimal(entry['price'])) * (1 if target == 'LONG' else -1)
    assert Decimal(view['gross_pnl']) == pnl
    fees = sum(Decimal(f['fee']) for f in view['fills'])
    assert Decimal(view['balance']) == Decimal('10000') + pnl - fees
    assert Decimal(view['fees']) == fees and view['trading_enabled'] is False


@pytest.mark.parametrize('target', ['LONG', 'SHORT'])
def test_stop_gap_executes_actual_adverse_quote_not_promised_stop(target):
    engine = opened(target)
    q = quote(2, '1800', '1801') if target == 'LONG' else quote(2, '2200', '2201')
    view = engine.on_quote(q, now=q.at, news=None)
    assert view['fills'][-1]['reason'] == 'STOP_LOSS'
    assert Decimal(view['fills'][-1]['price']) == (Decimal('1799.95') if target == 'LONG' else Decimal('2201.05'))


def high_event(**updates):
    value = dict(event_id='scheduled-test-only', instruments=('XAU/USD',), impact='HIGH',
                 scheduled_at=BASE + timedelta(seconds=35), known_at=BASE - timedelta(hours=1))
    return Event(**{**value, **updates})


@pytest.mark.parametrize('second,expected', [(4, 'CLEAR'), (5, 'HIGH_IMPACT_EVENT'),
                                            (35, 'HIGH_IMPACT_EVENT'), (95, 'HIGH_IMPACT_EVENT'), (96, 'CLEAR')])
def test_news_embargo_inclusive_boundary(second, expected):
    assert entry_gate(config(), BASE + timedelta(seconds=second), news(events=(high_event(),))) == expected


@pytest.mark.parametrize('snapshot,expected', [
    (None, 'NEWS_UNAVAILABLE'),
    (news(source='UNVERIFIED'), 'NEWS_SOURCE_UNTRUSTED'),
    (news(-121), 'NEWS_STALE'),
    (news(1), 'NEWS_FROM_FUTURE'),
    (news(coverage_end=BASE + timedelta(seconds=29)), 'NEWS_COVERAGE_INCOMPLETE'),
    (news(coverage_start=BASE - timedelta(seconds=59)), 'NEWS_COVERAGE_INCOMPLETE'),
])
def test_unknown_stale_future_or_incomplete_news_blocks_new_risk(snapshot, expected):
    assert entry_gate(config(), BASE, snapshot) == expected
    engine = DirectionalPaper(config())
    engine.on_quote(quote(), now=BASE, news=None)
    assert engine.submit(proposal(), now=BASE, news=snapshot) == {'status': 'REJECTED', 'reason': expected}
    assert engine.fills == []


def test_news_rechecked_at_execution_and_cannot_disable_protection():
    engine = DirectionalPaper(config())
    engine.on_quote(quote(), now=BASE, news=None)
    assert engine.submit(proposal(), now=BASE, news=news())['status'] == 'ACCEPTED'
    q = quote(6)
    view = engine.on_quote(q, now=q.at, news=news(events=(high_event(),)))
    assert view['fills'] == [] and view['pending'] is None
    assert engine.journal[-2]['details']['reason'] == 'HIGH_IMPACT_EVENT'
    engine = opened()
    engine.halted = True
    q = quote(6, '1988', '1989')
    result = engine.on_quote(q, now=q.at, news=news(events=(high_event(),)))
    assert result['fills'][-1]['reason'] == 'STOP_LOSS'


@pytest.mark.parametrize('event', [high_event(impact='LOW'), high_event(instruments=('ETH/USDT',))])
def test_low_impact_or_other_product_does_not_block(event):
    assert entry_gate(config(), BASE + timedelta(seconds=10), news(events=(event,))) == 'CLEAR'


def test_duplicate_decision_and_duplicate_quote_do_not_fill_twice():
    engine = DirectionalPaper(config())
    engine.on_quote(quote(), now=BASE, news=news())
    result = engine.submit(proposal(), now=BASE, news=news())
    assert engine.submit(proposal(), now=BASE, news=None) == result
    with pytest.raises(ValueError, match='conflict'):
        engine.submit(proposal(quantity='2'), now=BASE, news=news())
    engine.on_quote(quote(), now=BASE, news=news())
    assert engine.fills == []
    engine.on_quote(quote(1), now=BASE + timedelta(seconds=1), news=news())
    before = engine.view()
    assert engine.on_quote(quote(1), now=BASE + timedelta(seconds=2), news=news()) == before
    assert len(engine.fills) == 1


def test_revision_stale_future_wrong_source_fail_before_mutation():
    engine = opened()
    before = deepcopy(engine.view())
    for q, now in [(quote(1, '1999', '2000'), BASE + timedelta(seconds=2)),
                   (quote(0), BASE + timedelta(seconds=2)),
                   (quote(3), BASE + timedelta(seconds=2)),
                   (quote(2), BASE + timedelta(seconds=13)),
                   (quote(2).model_copy(update={'source': 'OTHER'}), BASE + timedelta(seconds=2))]:
        with pytest.raises(ValueError):
            engine.on_quote(q, now=now, news=news())
        assert engine.view() == before


@pytest.mark.parametrize('overrides,reason', [({'quantity': '10'}, 'COLLATERAL_OR_ORDER_LIMIT'),
                                             ({'quantity': '.001'}, 'SIZE_OR_PRICE_LIMIT'),
                                             ({'stop_loss': '2001'}, 'BRACKET_INVALID_AT_FILL')])
def test_entry_limits_checked_at_fill(overrides, reason):
    engine = DirectionalPaper(config())
    engine.on_quote(quote(), now=BASE, news=news())
    engine.submit(proposal(**overrides), now=BASE, news=news())
    engine.on_quote(quote(1), now=BASE + timedelta(seconds=1), news=news())
    assert engine.fills == [] and engine.journal[-2]['details']['reason'] == reason


def test_entry_expiry_and_no_implicit_pyramiding_or_short_flip():
    engine = opened()
    result = engine.submit(proposal('SHORT', request_id='flip', available_at=BASE + timedelta(seconds=1)),
                           now=BASE + timedelta(seconds=1), news=news())
    assert result['reason'] == 'FLATTEN_BEFORE_REENTRY'
    other = DirectionalPaper(config())
    other.on_quote(quote(), now=BASE, news=news())
    other.submit(proposal(), now=BASE, news=news())
    other.on_quote(quote(61), now=BASE + timedelta(seconds=61), news=news(61))
    assert other.fills == [] and other.journal[-2]['details']['reason'] == 'DECISION_EXPIRED'


def test_short_insolvency_is_recorded_and_new_risk_stays_halted():
    engine = opened('SHORT')
    q = quote(2, '13000', '13001')
    view = engine.on_quote(q, now=q.at, news=None)
    assert Decimal(view['balance']) < 0 and view['entry_halted'] is True
    result = engine.submit(proposal(request_id='after-gap', available_at=q.at), now=q.at, news=news())
    assert result['reason'] == 'ENTRY_HALTED'


@pytest.mark.parametrize('value', [1.5, True, 'NaN', 'Infinity'])
def test_nonexact_nonfinite_quote_rejected(value):
    with pytest.raises(ValidationError):
        quote(bid=value)


def test_naive_dates_missing_brackets_future_knowledge_and_paper_only():
    with pytest.raises(ValidationError):
        quote().model_validate({**quote().model_dump(), 'at': '2026-10-20T12:00:00'})
    with pytest.raises(ValidationError):
        proposal(stop_loss=None)
    with pytest.raises(ValidationError):
        news(events=(high_event(known_at=BASE + timedelta(seconds=1)),))
    with pytest.raises(ValidationError):
        Config(**{**config().model_dump(), 'mode': 'LIVE'})


def test_replay_determinism_and_prefix_preservation():
    def run(end):
        engine = DirectionalPaper(config())
        for second in range(end):
            q = quote(second, str(2000 + second), str(Decimal('2000.5') + second))
            engine.on_quote(q, now=q.at, news=news())
            if second == 0:
                engine.submit(proposal(), now=q.at, news=news())
        return engine
    short, full, repeated = run(5), run(15), run(15)
    assert full.view() == repeated.view() and full.journal == repeated.journal
    assert full.journal[:len(short.journal)] == short.journal
    assert all(f['quote_at'] > BASE.isoformat() for f in full.fills)


@pytest.mark.parametrize('direction,stop', [('LONG', '2000.4'), ('SHORT', '2000.1')])
def test_bracket_already_triggered_on_executable_side_cannot_open(direction, stop):
    engine = DirectionalPaper(config())
    engine.on_quote(quote(), now=BASE, news=news())
    engine.submit(proposal(direction, stop_loss=stop), now=BASE, news=news())
    engine.on_quote(quote(1), now=BASE + timedelta(seconds=1), news=news())
    assert engine.fills == []
    assert engine.journal[-2]['details']['reason'] == 'BRACKET_ALREADY_TRIGGERED'


def test_submit_observation_clock_cannot_regress_even_when_quote_is_fresh():
    engine = DirectionalPaper(config())
    engine.on_quote(quote(), now=BASE + timedelta(seconds=5), news=news())
    before = engine.view()
    with pytest.raises(ValueError, match='backwards'):
        engine.submit(proposal(), now=BASE, news=news())
    assert engine.view() == before


def test_late_quote_created_before_submission_cannot_backfill_an_entry():
    engine = DirectionalPaper(config())
    engine.on_quote(quote(), now=BASE, news=news())
    engine.submit(proposal(), now=BASE + timedelta(seconds=5), news=news())
    engine.on_quote(quote(3), now=BASE + timedelta(seconds=6), news=news())
    assert engine.fills == []
    engine.on_quote(quote(7), now=BASE + timedelta(seconds=8), news=news())
    assert len(engine.fills) == 1
    assert engine.fills[0]['accepted_at'] < engine.fills[0]['quote_at']


def test_news_snapshot_and_journal_hashes_retain_decision_evidence():
    from core.backtest.spot import digest
    engine = opened()
    for event in engine.journal:
        assert digest({k: v for k, v in event.items() if k != 'sha256'}) == event['sha256']
        identity = event['details'].get('news_sha256')
        if identity:
            assert digest(engine.news_snapshots[identity]) == identity
