"""Recovery faults use isolated PostgreSQL fixtures, never product market data."""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from datetime import timedelta

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session

from core.market_data.storage import save_candles
from core.paper import streams
from core.storage.models import PaperStreamRecord
from tests.test_integration import database
from tests.test_paper_streams import arrive, setup


def durable(engine, session_id):
    with Session(engine) as session:
        record = session.get(PaperStreamRecord, session_id)
        return deepcopy({name: getattr(record, name) for name in (
            'snapshot', 'observations', 'ledger', 'revision', 'status',
            'halt_at', 'feed', 'updated_at',
        )})


def economic_state(state):
    # Feed is the latest polling observation, not an additional execution.
    return {name: state[name] for name in (
        'revision', 'accepted_bars', 'observations', 'orders', 'fills',
        'account', 'equity', 'signals', 'pending', 'risk',
    )}


def test_committed_advance_lost_acknowledgement_retry_never_duplicates(database):
    engine, _, _ = database
    account, bars = setup(engine)
    save_candles(engine, [bars[2]])
    cutoff = bars[2].close_time + timedelta(seconds=1)

    def deliver_then_lose_acknowledgement():
        assert streams.advance(engine, account['session_id'], observed_at=cutoff)
        raise ConnectionError('Injected acknowledgement loss after commit')

    with pytest.raises(ConnectionError, match='after commit'):
        deliver_then_lose_acknowledgement()
    committed = streams.read(engine, account['session_id'])
    assert committed['revision'] == committed['accepted_bars'] == 1
    assert len(committed['orders']) == len(committed['fills']) == 1
    assert not streams.advance(engine, account['session_id'], observed_at=cutoff)
    recovered = streams.read(engine, account['session_id'])
    assert economic_state(recovered) == economic_state(committed)
    assert durable(engine, account['session_id'])['ledger']['fills'] == committed['fills']


def test_new_engine_recovers_both_trade_sides_and_stable_orders(database):
    engine, _, _ = database
    account, bars = setup(engine)
    for index in range(2, 5):
        state = arrive(engine, account, bars, index)
    assert [fill['side'] for fill in state['fills']] == ['BUY', 'SELL']
    persisted = durable(engine, account['session_id'])
    url = engine.url
    engine.dispose()
    recovered_engine = create_engine(url)
    try:
        assert streams.read(recovered_engine, account['session_id']) == state
        assert durable(recovered_engine, account['session_id']) == persisted
        assert not streams.advance(recovered_engine, account['session_id'],
                                   observed_at=bars[4].close_time + timedelta(seconds=1))
        recovered = streams.read(recovered_engine, account['session_id'])
        assert economic_state(recovered) == economic_state(state)
        assert [order['order_id'] for order in recovered['orders']] == [
            order['order_id'] for order in state['orders']]
        assert len({order['order_id'] for order in recovered['orders']}) == len(state['orders'])
    finally:
        recovered_engine.dispose()


def test_exception_after_flush_before_commit_rolls_back_entire_acceptance(database):
    engine, _, _ = database
    account, bars = setup(engine)
    save_candles(engine, [bars[2]])
    before = durable(engine, account['session_id'])
    cutoff = bars[2].close_time + timedelta(seconds=1)
    failures = []

    def fail_after_flush(session):
        if session.get_bind() is engine:
            record = session.get(PaperStreamRecord, account['session_id'])
            if record.revision == before['revision']:
                return
            session.flush()
            failures.append((record.revision, len(record.observations), len(record.ledger['fills'])))
            raise RuntimeError('Injected failure after SQL flush before commit')

    event.listen(Session, 'before_commit', fail_after_flush)
    try:
        with pytest.raises(RuntimeError, match='before commit'):
            streams.advance(engine, account['session_id'], observed_at=cutoff)
    finally:
        event.remove(Session, 'before_commit', fail_after_flush)
    assert failures == [(1, 1, 1)]
    assert durable(engine, account['session_id']) == before
    assert streams.advance(engine, account['session_id'], observed_at=cutoff)
    recovered = streams.read(engine, account['session_id'])
    assert recovered['revision'] == recovered['accepted_bars'] == 1
    assert len(recovered['orders']) == len(recovered['fills']) == 1


def test_concurrent_recovery_preserves_exact_committed_execution(database):
    engine, _, _ = database
    account, bars = setup(engine)
    committed = arrive(engine, account, bars, 2)
    engine.dispose()
    expected = economic_state(committed)
    cutoff = bars[2].close_time + timedelta(seconds=2)

    def retry(_):
        return streams.advance(engine, account['session_id'], observed_at=cutoff)

    with ThreadPoolExecutor(max_workers=6) as pool:
        assert list(pool.map(retry, range(12))) == [False] * 12
    assert economic_state(streams.read(engine, account['session_id'])) == expected
    persisted = durable(engine, account['session_id'])
    assert persisted['revision'] == 1
    assert persisted['observations'] == committed['observations']
    assert persisted['ledger']['orders'] == committed['orders']
    assert persisted['ledger']['fills'] == committed['fills']
    assert persisted['ledger']['account'] == committed['account']
