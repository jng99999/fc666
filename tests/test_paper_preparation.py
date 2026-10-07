"""Separately committed preparation faults use isolated PostgreSQL only."""
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from decimal import Decimal

import pytest
from sqlalchemy import create_engine, event, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from core.market_data.storage import save_candles
from core.paper import preparation, streams
from core.storage.models import CandleRecord, InstrumentRecord
from tests.test_integration import database
from tests.test_paper_streams import setup


def candidate(engine, **updates):
    account, bars = setup(engine, **updates)
    save_candles(engine, [bars[2]])
    return account, bars, bars[2].close_time + timedelta(seconds=1)


def test_preparation_commits_without_economic_execution_then_new_connection_consumes(database):
    engine, _, _ = database; account, bars, stamp = candidate(engine)
    before = streams.read(engine, account['session_id'])
    pid = preparation.prepare(engine, account['session_id'], observed_at=stamp)
    report = preparation.capture(engine, account['session_id'])
    assert report['records'][0]['status'] == 'PREPARED'
    pending = streams.read(engine, account['session_id'])
    for key in ['revision', 'observations', 'orders', 'fills', 'account']:
        assert pending[key] == before[key]
    assert report['automatic_replay'] is report['trading_enabled'] is False
    url = engine.url; engine.dispose(); recovered = create_engine(url)
    try:
        assert preparation.consume(recovered, account['session_id'], pid, observed_at=stamp)
        state = streams.read(recovered, account['session_id'])
        assert state['revision'] == 1 and len(state['fills']) == 1
        assert not preparation.consume(recovered, account['session_id'], pid, observed_at=stamp)
        assert preparation.capture(recovered, account['session_id'])['records'][0]['status'] == 'CONSUMED'
        assert streams.read(recovered, account['session_id'])['fills'] == state['fills']
    finally:
        recovered.dispose()


def test_concurrent_preparation_and_consumption_have_one_durable_outcome(database):
    engine, _, _ = database; account, _, stamp = candidate(engine)
    with ThreadPoolExecutor(max_workers=2) as pool:
        ids = list(pool.map(lambda _: preparation.prepare(engine, account['session_id'], observed_at=stamp), range(2)))
    pid = next(value for value in ids if value)
    assert set(ids) <= {None, pid}
    assert len(preparation.capture(engine, account['session_id'])['records']) == 1
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(lambda _: preparation.consume(engine, account['session_id'], pid, observed_at=stamp), range(8)))
    assert sum(results) == 1
    assert len(streams.read(engine, account['session_id'])['fills']) == 1


@pytest.mark.parametrize('command', ['pause', 'resume', 'halt', 'stop'])
def test_control_between_prepare_and_consume_invalidates_pending(database, command):
    engine, _, _ = database; account, _, stamp = candidate(engine)
    if command == 'resume':
        streams.command(engine, account['session_id'], 0, 'pause')
    revision = streams.read(engine, account['session_id'])['revision']
    pid = preparation.prepare(engine, account['session_id'], observed_at=stamp)
    streams.command(engine, account['session_id'], revision, command)
    assert not preparation.consume(engine, account['session_id'], pid, observed_at=stamp)
    row = preparation.capture(engine, account['session_id'])['records'][0]
    assert (row['status'], row['reason']) == ('CANCELLED', 'CONTROL_CHANGED')
    assert streams.read(engine, account['session_id'])['fills'] == []


def test_expired_eligible_preparation_is_cancelled_then_rescanned_as_stale(database):
    engine, _, _ = database; account, bars, stamp = candidate(engine)
    pid = preparation.prepare(engine, account['session_id'], observed_at=stamp)
    late = bars[2].close_time + timedelta(seconds=30)
    assert not preparation.consume(engine, account['session_id'], pid, observed_at=late)
    assert preparation.capture(engine, account['session_id'])['records'][0]['reason'] == 'EXPIRED'
    second = preparation.prepare(engine, account['session_id'], observed_at=late)
    assert second != pid
    assert preparation.consume(engine, account['session_id'], second, observed_at=late)
    state = streams.read(engine, account['session_id'])
    assert state['fills'] == [] and state['observations'][0]['gate'] == 'STALE_BAR'


@pytest.mark.parametrize('change', ['prepared_bar', 'seed_prefix', 'instrument'])
def test_source_correction_cancels_prepared_execution_and_blocks_account(database, change):
    engine, _, _ = database; account, bars, stamp = candidate(engine)
    pid = preparation.prepare(engine, account['session_id'], observed_at=stamp)
    with Session(engine) as db, db.begin():
        if change == 'instrument':
            db.get(InstrumentRecord, bars[2].instrument_id).quantity_step = Decimal('.002')
        else:
            bar = bars[2] if change == 'prepared_bar' else bars[0]
            db.get(CandleRecord, (bar.instrument_id, bar.timeframe, bar.open_time)).volume += 1
    assert not preparation.consume(engine, account['session_id'], pid, observed_at=stamp)
    state = streams.read(engine, account['session_id'])
    assert state['status'] == 'BLOCKED' and state['fills'] == []
    row = preparation.capture(engine, account['session_id'])['records'][0]
    assert (row['status'], row['reason']) == ('CANCELLED', 'SOURCE_CHANGED')


@pytest.mark.parametrize('phase', ['prepare', 'consume'])
def test_commit_fault_leaves_no_execution_and_preserves_recoverable_preparation(database, phase):
    engine, _, _ = database; account, _, stamp = candidate(engine)
    pid = preparation.prepare(engine, account['session_id'], observed_at=stamp) if phase == 'consume' else None
    def fail(session):
        if session.get_bind() is engine:
            session.flush()
            raise RuntimeError('Injected preparation commit fault')
    event.listen(Session, 'before_commit', fail)
    try:
        with pytest.raises(RuntimeError, match='commit fault'):
            if phase == 'prepare': preparation.prepare(engine, account['session_id'], observed_at=stamp)
            else: preparation.consume(engine, account['session_id'], pid, observed_at=stamp)
    finally:
        event.remove(Session, 'before_commit', fail)
    records = preparation.capture(engine, account['session_id'])['records']
    assert len(records) == (1 if phase == 'consume' else 0)
    if records: assert records[0]['status'] == 'PREPARED'
    assert streams.read(engine, account['session_id'])['fills'] == []
    pid = pid or preparation.prepare(engine, account['session_id'], observed_at=stamp)
    assert preparation.consume(engine, account['session_id'], pid, observed_at=stamp)
    assert len(streams.read(engine, account['session_id'])['fills']) == 1


@pytest.mark.parametrize('statement', [
    "UPDATE paper_preparations SET status='PREPARED',finished_at=NULL",
    "UPDATE paper_preparations SET payload='{}'::jsonb",
    'DELETE FROM paper_preparations',
])
def test_database_forbids_consumed_state_payload_changes_and_delete(database, statement):
    engine, _, _ = database; account, _, stamp = candidate(engine)
    pid = preparation.prepare(engine, account['session_id'], observed_at=stamp)
    preparation.consume(engine, account['session_id'], pid, observed_at=stamp)
    before = preparation.capture(engine, account['session_id'])
    with pytest.raises(DBAPIError):
        with engine.begin() as conn: conn.execute(text(statement))
    assert preparation.capture(engine, account['session_id']) == before


def test_capacity_refuses_new_preparation_without_economic_mutation(database, monkeypatch):
    engine, _, _ = database; account, bars, stamp = candidate(engine)
    pid = preparation.prepare(engine, account['session_id'], observed_at=stamp)
    preparation.consume(engine, account['session_id'], pid, observed_at=stamp)
    save_candles(engine, [bars[3]])
    monkeypatch.setattr(preparation, 'LIMIT', 1)
    before = streams.read(engine, account['session_id'])
    with pytest.raises(preparation.Capacity):
        preparation.prepare(engine, account['session_id'], observed_at=bars[3].close_time + timedelta(seconds=1))
    after = streams.read(engine, account['session_id'])
    for key in ['revision', 'observations', 'orders', 'fills', 'account']:
        assert after[key] == before[key]
    assert len(preparation.capture(engine, account['session_id'])['records']) == 1


def test_future_or_naive_observation_clock_is_rejected_without_writes(database):
    from datetime import datetime, timezone
    engine, _, _ = database; account, _, stamp = candidate(engine)
    for bad in [datetime.now(timezone.utc) + timedelta(days=1), stamp.replace(tzinfo=None)]:
        with pytest.raises(ValueError):
            preparation.prepare(engine, account['session_id'], observed_at=bad)
    assert preparation.capture(engine, account['session_id'])['records'] == []
    pid = preparation.prepare(engine, account['session_id'], observed_at=stamp)
    with pytest.raises(ValueError):
        preparation.consume(engine, account['session_id'], pid, observed_at=datetime.now(timezone.utc) + timedelta(days=1))
    assert preparation.capture(engine, account['session_id'])['records'][0]['status'] == 'PREPARED'


def test_preparation_inspection_api_is_read_only(database):
    from uuid import uuid4
    from fastapi.testclient import TestClient
    from apps.api.main import create_app
    engine, _, settings = database; account, _, stamp = candidate(engine)
    preparation.prepare(engine, account['session_id'], observed_at=stamp)
    before = preparation.capture(engine, account['session_id'])
    url = '/api/v1/paper/streams/' + account['session_id'] + '/preparations'
    with TestClient(create_app(settings)) as client:
        response = client.get(url)
        assert response.status_code == 200
        assert response.json() == before
        assert client.post(url, json={}).status_code == 405
        assert client.get('/api/v1/paper/streams/' + str(uuid4()) + '/preparations').status_code == 404
    assert preparation.capture(engine, account['session_id']) == before
    assert streams.read(engine, account['session_id'])['fills'] == []


def test_consumption_acknowledgement_loss_retry_preserves_exact_receipt(database):
    engine, _, _ = database; account, _, stamp = candidate(engine)
    pid = preparation.prepare(engine, account['session_id'], observed_at=stamp)
    def commit_then_lose_response():
        assert preparation.consume(engine, account['session_id'], pid, observed_at=stamp)
        raise ConnectionError('Injected consumed acknowledgement loss')
    with pytest.raises(ConnectionError, match='acknowledgement loss'):
        commit_then_lose_response()
    before = streams.read(engine, account['session_id'])
    assert not preparation.consume(engine, account['session_id'], pid, observed_at=stamp)
    assert streams.read(engine, account['session_id']) == before
    assert len(before['fills']) == 1


@pytest.mark.parametrize('corruption', ['missing_base_key', 'boolean_revision', 'malformed_digest'])
def test_rehashed_payload_structural_corruption_is_rejected(database, corruption):
    from copy import deepcopy
    from types import SimpleNamespace
    from core.backtest.spot import digest
    from core.storage.models import PaperPreparationRecord
    engine, _, _ = database; account, _, stamp = candidate(engine)
    pid = preparation.prepare(engine, account['session_id'], observed_at=stamp)
    with Session(engine) as db:
        record = db.get(PaperPreparationRecord, pid)
        row = SimpleNamespace(**{key: deepcopy(getattr(record, key)) for key in [
            'session_id', 'payload', 'payload_sha256', 'preparation_id', 'created_at', 'finished_at', 'status', 'reason']})
    if corruption == 'missing_base_key': row.payload['base'].pop('ledger_sha256')
    elif corruption == 'boolean_revision': row.payload['base']['revision'] = True
    else: row.payload['base']['ledger_sha256'] = 'not-a-digest'
    row.payload_sha256 = digest(row.payload)
    row.preparation_id = digest({'version': preparation.VERSION, 'payload': row.payload})
    with pytest.raises(ValueError): preparation.validate(row)
    assert preparation.capture(engine, account['session_id'])['records'][0]['status'] == 'PREPARED'


def test_later_gap_fill_does_not_expand_or_invalidate_frozen_batch(database):
    engine, _, _ = database; account, bars = setup(engine)
    save_candles(engine, [bars[2],bars[4]])
    stamp=bars[4].close_time+timedelta(seconds=1)
    pid=preparation.prepare(engine,account['session_id'],observed_at=stamp)
    frozen=preparation.capture(engine,account['session_id'])['records'][0]['payload']['observations']
    assert len(frozen)==1
    save_candles(engine,[bars[3]])
    assert preparation.consume(engine,account['session_id'],pid,observed_at=stamp)
    state=streams.read(engine,account['session_id'])
    assert state['status']=='RUNNING' and state['accepted_bars']==1
    assert state['observations']==frozen
    assert state['fills']==[]
