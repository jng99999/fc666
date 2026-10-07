"""Independent Paper receipts verified against isolated PostgreSQL ledger transactions."""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from datetime import timedelta
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import event, select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from apps.api.main import create_app
from core.backtest.spot import digest
from core.market_data.storage import save_candles
from core.paper import intents, streams
from core.storage.models import PaperOrderIntentRecord as Intent, PaperStreamRecord as Stream
from tests.test_backtest import cfg
from tests.test_integration import database
from tests.test_paper_streams import arrive, setup


def durable(engine, account):
    with Session(engine) as db:
        row = db.get(Stream, account['session_id'])
        return deepcopy({key: getattr(row, key) for key in (
            'observations', 'ledger', 'revision', 'status', 'feed', 'updated_at', 'intent_version')})


def legacy_execution(engine, account, bars, marker=None):
    """Construct a pre-migration ledger directly, without disabling any trigger."""
    save_candles(engine, [bars[2]])
    with Session(engine) as db, db.begin():
        record = db.get(Stream, account['session_id'])
        record.intent_version = marker
        record.observations = [{'candle': bars[2].model_dump(mode='json'),
            'observed_at': (bars[2].close_time + timedelta(seconds=1)).isoformat(), 'gate': 'ELIGIBLE'}]
        record.ledger = streams.computed(record)
        record.revision = 1


@pytest.mark.parametrize('status', ['FILLED', 'PARTIAL_CANCELLED', 'REJECTED'])
def test_exact_receipts_and_transition_paths_are_read_only(database, status):
    engine, _, _ = database
    updates = {}
    if status == 'PARTIAL_CANCELLED':
        config = cfg().model_dump(mode='json'); config['participation'] = '.001'
        updates['config'] = config
    if status == 'REJECTED':
        updates['risk'] = {'max_order_quote': '100', 'max_position_quote': '10000', 'max_drawdown': '1'}
    account, bars = setup(engine, **updates)
    state = arrive(engine, account, bars, 2)
    before = durable(engine, account)
    report = intents.capture(engine, account['session_id'])
    assert report['coverage'] == 'VERIFIED'
    assert report['durable_orders'] == report['ledger_orders'] == 1
    receipt = report['intents'][0]
    assert receipt['status'] == state['orders'][0]['status'] == status
    assert receipt['payload']['order'] == state['orders'][0]
    assert receipt['payload']['fill'] == (state['fills'][0] if state['fills'] else None)
    assert receipt['payload_sha256'] == digest(receipt['payload'])
    assert receipt['transitions'] == (['CREATED', 'REJECTED'] if status == 'REJECTED' else ['CREATED', 'VALIDATED', status])
    assert receipt['origin'] == 'BAR_ACCEPTANCE'
    assert receipt['intent_id'] == digest({'version': intents.VERSION, 'session_id': account['session_id'], 'order_id': receipt['order_id']})
    assert intents.capture(engine, account['session_id'])['intents'] == report['intents']
    assert durable(engine, account) == before
    assert report['trading_enabled'] is report['automatic_replay'] is report['in_flight_submission_supported'] is False


def test_identical_snapshots_have_account_scoped_intent_ids(database):
    engine, _, _ = database
    first, bars = setup(engine); second, _ = setup(engine)
    assert first['manifest']['snapshot_sha256'] == second['manifest']['snapshot_sha256']
    arrive(engine, first, bars, 2); arrive(engine, second, bars, 2)
    a = intents.capture(engine, first['session_id'])['intents'][0]
    b = intents.capture(engine, second['session_id'])['intents'][0]
    assert a['order_id'] == b['order_id']
    assert a['intent_id'] != b['intent_id']


def test_concurrent_duplicate_acceptance_persists_one_independent_receipt(database):
    engine, _, _ = database; account, bars = setup(engine)
    save_candles(engine, [bars[2]])
    def accept(_):
        return streams.advance(engine, account['session_id'], observed_at=bars[2].close_time + timedelta(seconds=1))
    with ThreadPoolExecutor(max_workers=4) as pool:
        assert sum(pool.map(accept, range(8))) == 1
    report = intents.capture(engine, account['session_id'])
    assert report['revision'] == report['durable_orders'] == report['ledger_orders'] == 1
    assert len(streams.read(engine, account['session_id'])['fills']) == 1


def test_flush_fault_rolls_back_intent_and_ledger_together(database):
    engine, _, _ = database; account, bars = setup(engine)
    save_candles(engine, [bars[2]]); before = durable(engine, account); seen = []
    def fail(session):
        if session.get_bind() is engine:
            if session.get(Stream, account['session_id']).revision == before['revision']:
                return
            session.flush()
            seen.append(len(list(session.scalars(select(Intent)))))
            raise RuntimeError('Injected intent transaction commit failure')
    event.listen(Session, 'before_commit', fail)
    try:
        with pytest.raises(RuntimeError, match='commit failure'):
            streams.advance(engine, account['session_id'], observed_at=bars[2].close_time + timedelta(seconds=1))
    finally:
        event.remove(Session, 'before_commit', fail)
    assert seen == [1]
    assert durable(engine, account) == before
    assert intents.capture(engine, account['session_id'])['durable_orders'] == 0
    assert streams.advance(engine, account['session_id'], observed_at=bars[2].close_time + timedelta(seconds=1))
    assert intents.capture(engine, account['session_id'])['durable_orders'] == 1


@pytest.mark.parametrize('statement', ["UPDATE paper_order_intents SET origin='HISTORICAL_MATERIALIZATION'", 'DELETE FROM paper_order_intents'])
def test_database_forbids_mutating_completed_intent(database, statement):
    engine, _, _ = database; account, bars = setup(engine); arrive(engine, account, bars, 2)
    before = intents.capture(engine, account['session_id'])
    with pytest.raises(DBAPIError, match='immutable'):
        with engine.begin() as conn:
            conn.execute(text(statement))
    assert intents.capture(engine, account['session_id'])['intents'] == before['intents']


def test_legacy_read_never_materializes_but_authorized_command_does(database):
    engine, _, _ = database; account, bars = setup(engine)
    legacy_execution(engine, account, bars)
    before = durable(engine, account)
    report = intents.capture(engine, account['session_id'])
    assert report['coverage'] == 'LEGACY_UNMATERIALIZED'
    assert report['ledger_orders'] == 1 and report['durable_orders'] == 0
    assert durable(engine, account) == before
    state = streams.command(engine, account['session_id'], 1, 'pause')
    assert state['status'] == 'PAUSED' and state['fills'] == before['ledger']['fills']
    report = intents.capture(engine, account['session_id'])
    assert report['coverage'] == 'VERIFIED' and report['durable_orders'] == 1
    assert report['intents'][0]['origin'] == 'HISTORICAL_MATERIALIZATION'


def test_declared_missing_receipt_fails_closed_without_self_repair(database):
    engine, _, settings = database; account, bars = setup(engine)
    legacy_execution(engine, account, bars, marker=intents.VERSION)
    before = durable(engine, account)
    with pytest.raises(ValueError, match='coverage'):
        intents.capture(engine, account['session_id'])
    with pytest.raises(ValueError, match='coverage'):
        streams.command(engine, account['session_id'], 1, 'pause')
    with pytest.raises(ValueError, match='coverage'):
        streams.advance(engine, account['session_id'], observed_at=bars[3].close_time)
    with TestClient(create_app(settings)) as client:
        assert client.get('/api/v1/paper/streams/' + account['session_id'] + '/intents').status_code == 409
    assert durable(engine, account) == before
    with Session(engine) as db:
        assert list(db.scalars(select(Intent))) == []


def test_intent_api_inspection_is_get_only_and_unknown_account_is_404(database):
    engine, _, settings = database; account, bars = setup(engine); arrive(engine, account, bars, 2)
    before = durable(engine, account)
    url = '/api/v1/paper/streams/' + account['session_id'] + '/intents'
    with TestClient(create_app(settings)) as client:
        response = client.get(url)
        assert response.status_code == 200
        assert response.json()['intents'] == intents.capture(engine, account['session_id'])['intents']
        assert intents.verify(response.json()) == {'coverage': 'VERIFIED', 'orders': 1}
        assert client.post(url, json={}).status_code == 405
        assert client.get('/api/v1/paper/streams/' + str(uuid4()) + '/intents').status_code == 404
    assert durable(engine, account) == before


def test_offline_receipt_verifier_rejects_transition_payload_and_clock_tampering(database):
    engine, _, _ = database; account, bars = setup(engine); arrive(engine, account, bars, 2)
    report = intents.capture(engine, account['session_id'])
    assert intents.verify(report) == {'coverage': 'VERIFIED', 'orders': 1}
    for field in ['transitions', 'payload', 'recorded_at']:
        bad = deepcopy(report)
        if field == 'transitions':
            bad['inputs']['stored'][0][field] = ['CREATED', 'FILLED']
        elif field == 'payload':
            bad['inputs']['stored'][0][field]['order']['quantity'] = '999999'
        else:
            bad['inputs']['stored'][0][field] = '2999-01-01T00:00:00+00:00'
        bad['inputs_sha256'] = digest(bad['inputs'])
        with pytest.raises(ValueError):
            intents.verify(bad)


@pytest.mark.parametrize('change', ['unknown_marker', 'orphan', 'missing', 'bound'])
def test_input_compatibility_coverage_and_export_bound_fail_closed(database, monkeypatch, change):
    engine, _, _ = database; account, bars = setup(engine); arrive(engine, account, bars, 2)
    inputs = deepcopy(intents.capture(engine, account['session_id'])['inputs'])
    if change == 'unknown_marker': inputs['record']['intent_version'] = 'future-version'
    elif change == 'orphan': inputs['stored'][0]['order_id'] = 'a' * 64
    elif change == 'missing': inputs['stored'] = []
    else: monkeypatch.setattr(intents, 'MAX_BYTES', 1)
    with pytest.raises(ValueError): intents.evaluate(inputs)


def test_legacy_marker_with_existing_rows_is_not_silently_repaired(database):
    engine, _, _ = database; account, bars = setup(engine); arrive(engine, account, bars, 2)
    with Session(engine) as db, db.begin():
        db.get(Stream, account['session_id']).intent_version = None
    before = durable(engine, account)
    with pytest.raises(ValueError, match='Legacy'):
        intents.capture(engine, account['session_id'])
    with pytest.raises(ValueError, match='Legacy'):
        streams.command(engine, account['session_id'], 1, 'pause')
    assert durable(engine, account) == before


def test_legacy_materialization_commit_failure_rolls_back_marker_and_receipts(database):
    engine, _, _ = database; account, bars = setup(engine); legacy_execution(engine, account, bars)
    before = durable(engine, account); flushed = []
    def fail(session):
        if session.get_bind() is engine:
            session.flush()
            flushed.append((session.get(Stream, account['session_id']).intent_version,
                len(list(session.scalars(select(Intent))))))
            raise RuntimeError('Injected legacy materialization commit failure')
    event.listen(Session, 'before_commit', fail)
    try:
        with pytest.raises(RuntimeError, match='materialization'):
            streams.command(engine, account['session_id'], 1, 'pause')
    finally:
        event.remove(Session, 'before_commit', fail)
    assert flushed == [(intents.VERSION, 1)]
    assert durable(engine, account) == before
    assert intents.capture(engine, account['session_id'])['coverage'] == 'LEGACY_UNMATERIALIZED'
    with Session(engine) as db: assert list(db.scalars(select(Intent))) == []
