"""Explicit synthetic clocks/news only; no network or durable account mutation."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from core.paper.directional import Config, DirectionalPaper, Event, News, Proposal, Quote, entry_gate, pending_entry_gate

BASE = datetime(2026, 11, 15, tzinfo=timezone.utc)


def config():
    return Config(mode='RESEARCH_PAPER', instrument='XAU/USD', quote_source='TEST_ONLY',
                  news_sources=('TEST_CALENDAR',), initial_cash='10000', tick_size='.01',
                  quantity_step='.01', max_quantity='10', max_order_quote='9000', fee_rate='.001',
                  slippage='.05', quote_max_age_seconds=10, decision_ttl_seconds=60,
                  news_max_age_seconds=120, news_pre_seconds=0, news_post_seconds=0)


def at(seconds):
    return BASE + timedelta(seconds=seconds)


def quote(seconds=0, bid='2000', ask='2000.5'):
    return Quote(instrument='XAU/USD', source='TEST_ONLY', at=at(seconds), bid=bid, ask=ask)


def news(seconds=0, *, high=False):
    events = (Event(event_id='explicit-test-event', instruments=('XAU/USD',), impact='HIGH',
                    scheduled_at=at(seconds), known_at=at(-3600)),) if high else ()
    return News(source='TEST_CALENDAR', observed_at=at(seconds), coverage_start=at(-3600),
                coverage_end=at(3600), events=events)


def proposal(target='LONG', *, request_id='entry', seconds=0):
    return Proposal(request_id=request_id, target=target, available_at=at(seconds),
                    quantity='0' if target == 'FLAT' else '1',
                    stop_loss=None if target == 'FLAT' else '1990' if target == 'LONG' else '2010',
                    take_profit=None if target == 'FLAT' else '2010' if target == 'LONG' else '1990')


def pending(direction='LONG'):
    engine = DirectionalPaper(config())
    engine.on_quote(quote(), now=at(0), news=news())
    engine.submit(proposal(direction), now=at(0), news=news())
    return engine


def test_high_window_on_duplicate_quote_cancels_old_intent_before_window_clears():
    engine = pending()
    view = engine.on_quote(quote(), now=at(6), news=news(6, high=True))
    assert view['pending'] is None and view['fills'] == []
    rejection = engine.journal[-1]
    assert rejection['kind'] == 'ENTRY_REJECTED'
    assert rejection['details']['request_id'] == 'entry'
    assert rejection['details']['reason'] == 'HIGH_IMPACT_EVENT'
    assert rejection['details']['observed_at'] == at(6).isoformat()
    engine.on_quote(quote(7), now=at(7), news=news(7))
    assert engine.pending is None and engine.position is None and engine.fills == []


@pytest.mark.parametrize('snapshot,seconds,expected', [
    (news(6, high=True), 6, 'HIGH_IMPACT_EVENT'),
    (None, 6, 'NEWS_UNAVAILABLE'),
    (news(-121), 0, 'NEWS_STALE'),
    (news(6).model_copy(update={'source': 'OTHER'}), 6, 'NEWS_SOURCE_UNTRUSTED'),
    (news(7), 6, 'NEWS_FROM_FUTURE'),
    (news(6).model_copy(update={'coverage_end': at(5)}), 6, 'NEWS_COVERAGE_INCOMPLETE'),
    (news(61), 61, 'DECISION_EXPIRED'),
])
def test_cancels_new_risk_without_any_new_quote(snapshot, seconds, expected):
    engine = pending()
    before_quote = engine.quote
    engine.cancel_pending_risk(now=at(seconds), news=snapshot)
    assert engine.quote == before_quote and engine.pending is None and engine.pending_accepted_at is None
    assert engine.position is None and engine.fills == []
    assert engine.journal[-1]['details']['reason'] == expected


def test_clear_gate_preserves_intent_and_repeated_cancellation_is_idempotent():
    engine = pending()
    engine.cancel_pending_risk(now=at(1), news=news(1))
    assert engine.pending is not None
    first = engine.cancel_pending_risk(now=at(2), news=None)
    journal = deepcopy(engine.journal)
    second = engine.cancel_pending_risk(now=at(2), news=None)
    assert first == second and journal == engine.journal


def test_reverse_clock_fails_before_any_mutation():
    engine = pending()
    engine.cancel_pending_risk(now=at(5), news=news(5))
    before, journal = deepcopy(engine.view()), deepcopy(engine.journal)
    with pytest.raises(ValueError, match='backwards'):
        engine.cancel_pending_risk(now=at(4), news=None)
    assert before == engine.view() and journal == engine.journal and engine.observed_at == at(5)


@pytest.mark.parametrize('reason', ['HALTED', 'INSOLVENT'])
def test_entry_halt_or_empty_balance_cancels_pending_without_changing_position(reason):
    engine = pending()
    if reason == 'HALTED':
        engine.halted = True
    else:
        # Explicit conserved depletion state; configuration remains valid.
        engine.balance = Decimal(0)
        engine.fees = engine.config.initial_cash
    engine.cancel_pending_risk(now=at(1), news=news(1))
    assert engine.pending is None and engine.position is None
    assert engine.journal[-1]['details']['reason'] == 'ENTRY_HALTED'


def opened(direction='LONG'):
    engine = DirectionalPaper(config())
    engine.on_quote(quote(), now=at(0), news=news())
    engine.submit(proposal(direction), now=at(0), news=news())
    engine.on_quote(quote(1), now=at(1), news=news(1))
    return engine


def test_reduce_only_flat_survives_news_outage_and_halt():
    engine = opened()
    engine.submit(proposal('FLAT', request_id='exit', seconds=1), now=at(1), news=None)
    position = deepcopy(engine.position)
    engine.halted = True
    engine.cancel_pending_risk(now=at(2), news=None)
    assert engine.pending.target == 'FLAT' and engine.position == position
    engine.on_quote(quote(3), now=at(3), news=news(3, high=True))
    assert engine.position is None and engine.fills[-1]['reason'] == 'SIGNAL_EXIT'


@pytest.mark.parametrize('direction,bid,ask,expected', [
    ('LONG', '1980', '1981', 'STOP_LOSS'), ('LONG', '2011', '2012', 'TAKE_PROFIT'),
    ('SHORT', '2011', '2012', 'STOP_LOSS'), ('SHORT', '1980', '1981', 'TAKE_PROFIT'),
])
def test_halted_existing_brackets_still_execute_during_high_event(direction, bid, ask, expected):
    engine = opened(direction)
    position = deepcopy(engine.position)
    engine.halted = True
    engine.cancel_pending_risk(now=at(2), news=news(2, high=True))
    assert engine.position == position
    engine.on_quote(quote(3, bid, ask), now=at(3), news=news(3, high=True))
    assert engine.position is None and len(engine.fills) == 2
    assert engine.fills[-1]['reason'] == expected


def event_snapshot(seconds=10, *, impact='HIGH', instruments=('XAU/USD',), coverage_start=-3600):
    event = Event(event_id='between-polls', instruments=instruments, impact=impact,
                  scheduled_at=at(5), known_at=at(-3600))
    return News(source='TEST_CALENDAR', observed_at=at(seconds), coverage_start=at(coverage_start),
                coverage_end=at(3600), events=(event,))


@pytest.mark.parametrize('direction', ['LONG', 'SHORT'])
def test_pending_cannot_cross_entire_high_window_between_two_real_quotes(direction):
    engine = pending(direction)
    snapshot = event_snapshot()
    assert entry_gate(engine.config, at(10), snapshot) == 'CLEAR'
    view = engine.on_quote(quote(10), now=at(10), news=snapshot)
    assert view['pending'] is None and view['position'] is None and view['fills'] == []
    rejection = [event for event in engine.journal if event['kind'] == 'ENTRY_REJECTED'][-1]
    assert rejection['details']['reason'] == 'HIGH_IMPACT_EVENT_CROSSED'


def test_without_quote_poll_recheck_also_cancels_an_intent_that_crossed_the_window():
    engine = pending()
    engine.cancel_pending_risk(now=at(10), news=event_snapshot())
    assert engine.pending is None and engine.fills == []
    assert engine.journal[-1]['details']['reason'] == 'HIGH_IMPACT_EVENT_CROSSED'


@pytest.mark.parametrize('direction', ['LONG', 'SHORT'])
def test_current_news_coverage_is_not_enough_without_accepted_intent_history(direction):
    engine = pending(direction)
    snapshot = event_snapshot(coverage_start=1)
    assert entry_gate(engine.config, at(10), snapshot) == 'CLEAR'
    engine.on_quote(quote(10), now=at(10), news=snapshot)
    assert engine.pending is None and engine.position is None and engine.fills == []
    assert any(event['details'].get('reason') == 'NEWS_COVERAGE_INCOMPLETE' for event in engine.journal)


@pytest.mark.parametrize('direction', ['LONG', 'SHORT'])
def test_new_intent_received_after_event_is_allowed_on_a_later_actual_quote(direction):
    engine = DirectionalPaper(config())
    engine.on_quote(quote(10), now=at(10), news=event_snapshot())
    result = engine.submit(proposal(direction, seconds=10), now=at(10), news=event_snapshot())
    assert result['status'] == 'ACCEPTED'
    engine.on_quote(quote(11), now=at(11), news=event_snapshot(11))
    assert engine.position['direction'] == direction and len(engine.fills) == 1


@pytest.mark.parametrize('changes', [{'impact': 'LOW'}, {'instruments': ('BTCUSDT',)}])
def test_other_product_or_low_impact_event_does_not_cancel_pending(changes):
    engine = pending()
    engine.on_quote(quote(10), now=at(10), news=event_snapshot(**changes))
    assert engine.position['direction'] == 'LONG' and len(engine.fills) == 1


def test_coverage_must_include_the_configured_post_margin_before_acceptance():
    settings = config().model_copy(update={'news_post_seconds': 4})
    snapshot = event_snapshot(coverage_start=-3)
    assert entry_gate(settings, at(10), snapshot) == 'CLEAR'
    assert pending_entry_gate(settings, at(0), at(10), snapshot) == 'NEWS_COVERAGE_INCOMPLETE'


def test_crossed_window_intersection_is_inclusive_and_newer_acceptance_is_clear():
    settings = config().model_copy(update={'news_post_seconds': 4})
    snapshot = event_snapshot()
    assert pending_entry_gate(settings, at(9), at(10), snapshot) == 'HIGH_IMPACT_EVENT_CROSSED'
    assert pending_entry_gate(settings, at(10), at(10), snapshot) == 'CLEAR'


def test_current_evidence_gate_keeps_precedence_and_future_acceptance_is_invalid():
    snapshot = event_snapshot().model_copy(update={'source': 'UNTRUSTED'})
    assert pending_entry_gate(config(), at(0), at(10), snapshot) == 'NEWS_SOURCE_UNTRUSTED'
    with pytest.raises(ValueError, match='future'):
        pending_entry_gate(config(), at(11), at(10), event_snapshot())
