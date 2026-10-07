"""Execution authorization evidence and faults, using isolated PostgreSQL."""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import event, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from apps.api.main import create_app
from core.backtest.spot import BacktestConfig, digest
from core.market_data.storage import save_candles
from core.paper import authorization, preparation, streams
from core.storage.models import PaperPreparationRecord as Preparation, PaperStreamRecord as Stream
from tests.test_integration import database
from tests.test_paper_streams import setup, arrive


def candidate(engine, **updates):
    account, bars = setup(engine, **updates)
    save_candles(engine, [bars[2]])
    stamp = bars[2].close_time + timedelta(seconds=1)
    return account, bars, stamp


def evidence(engine, id):
    return authorization.capture(engine, id)['batches'][-1]['authorizations']


def test_prepared_projection_is_not_execution_and_exactly_matches_consumed_fill(database):
    engine, _, _ = database
    config = BacktestConfig(initial_cash='1000', period=2, fee_rate='.01', slippage_rate='.001', allocation='1', participation='.1').model_dump(mode='json')
    account, bars, stamp = candidate(engine, config=config)
    before = streams.read(engine, account['session_id'])
    pid = preparation.prepare(engine, account['session_id'], observed_at=stamp)
    authorized = evidence(engine, account['session_id'])
    assert len(authorized) == 1
    value = authorized[0]['payload']
    assert value['projection_only'] and not value['external_submission_allowed']
    assert value['decision'] == 'ALLOW_SIMULATION'
    assert Decimal(value['projected_fill']['price']) == Decimal('14.02')
    assert Decimal(value['projected_fill']['fee']) > 0
    current = streams.read(engine, account['session_id'])
    for key in ['revision', 'observations', 'orders', 'fills', 'account']:
        assert current[key] == before[key]
    assert preparation.consume(engine, account['session_id'], pid, observed_at=stamp)
    consumed = streams.read(engine, account['session_id'])
    assert value['proposed_order'] == consumed['orders'][0]
    assert value['projected_fill'] == consumed['fills'][0]
    assert evidence(engine, account['session_id']) == authorized


@pytest.mark.parametrize('limit,reason', [('max_order_quote','MAX_ORDER_QUOTE'), ('max_position_quote','MAX_POSITION_QUOTE')])
def test_denied_projection_preserves_original_risk_reason(database, limit, reason):
    engine, _, _ = database
    risk = {'max_order_quote':'10000','max_position_quote':'10000','max_drawdown':'1',limit:'100'}
    account, _, stamp = candidate(engine, risk=risk)
    pid = preparation.prepare(engine, account['session_id'], observed_at=stamp)
    value = evidence(engine, account['session_id'])[0]['payload']
    assert value['decision'] == 'DENY_SIMULATION' and value['reason'] == reason
    assert value['projected_fill'] is None
    assert preparation.consume(engine, account['session_id'], pid, observed_at=stamp)
    assert streams.read(engine, account['session_id'])['fills'] == []


@pytest.mark.parametrize('command,reason', [('halt','MANUAL_HALT'), ('pause','PAUSED')])
def test_account_gate_denies_new_entries(database, command, reason):
    engine, _, _ = database
    account, _, stamp = candidate(engine)
    streams.command(engine, account['session_id'], 0, command)
    pid = preparation.prepare(engine, account['session_id'], observed_at=stamp)
    report = authorization.capture(engine, account['session_id'])
    assert not report['account_gate']['buy_allowed']
    values = evidence(engine, account['session_id'])
    assert all(v['payload']['decision'] == 'DENY_SIMULATION' for v in values)
    if values: assert values[0]['payload']['reason'] == reason
    assert preparation.consume(engine, account['session_id'], pid, observed_at=stamp)
    assert streams.read(engine, account['session_id'])['fills'] == []


def test_manual_halt_allows_inventory_exit_and_historical_plan_survives_controls(database):
    engine, _, _ = database
    account, bars = setup(engine)
    state = arrive(engine, account, bars, 2)
    state = streams.command(engine, account['session_id'], state['revision'], 'halt')
    state = arrive(engine, account, bars, 3)
    save_candles(engine, [bars[4]])
    stamp = bars[4].close_time + timedelta(seconds=1)
    pid = preparation.prepare(engine, account['session_id'], observed_at=stamp)
    frozen = evidence(engine, account['session_id'])
    sell = next(v['payload'] for v in frozen if v['payload']['proposed_order']['side'] == 'SELL')
    assert not sell['account_gate']['buy_allowed'] and sell['account_gate']['sell_allowed']
    assert sell['decision'] == 'ALLOW_SIMULATION'
    assert preparation.consume(engine, account['session_id'], pid, observed_at=stamp)
    state = streams.read(engine, account['session_id'])
    assert sell['projected_fill'] == state['fills'][-1]
    streams.command(engine, account['session_id'], state['revision'], 'pause')
    arrive(engine, account, bars, 5)
    report = authorization.capture(engine, account['session_id'])
    batch = next(b for b in report['batches'] if b['preparation_id'] == pid)
    assert batch['authorizations'] == frozen and batch['coverage'] == 'VERIFIED'
    assert not report['account_gate']['sell_allowed']


@pytest.mark.parametrize('corruption', ['missing','extra','conflicting'])
def test_authorization_coverage_failure_rolls_back_consumption(database, monkeypatch, corruption):
    engine, _, _ = database
    account, _, stamp = candidate(engine)
    pid = preparation.prepare(engine, account['session_id'], observed_at=stamp)
    before = streams.read(engine, account['session_id'])
    original = authorization.rows
    def altered(db, preparation_id):
        values = original(db, preparation_id)
        if corruption == 'missing': return []
        if corruption == 'extra': return [*values, values[0]]
        row = SimpleNamespace(**{k:deepcopy(getattr(values[0], k)) for k in ['authorization_id','order_id','payload','payload_sha256','recorded_at']})
        row.payload['decision'] = 'DENY_SIMULATION'
        row.payload_sha256 = digest(row.payload)
        return [row]
    monkeypatch.setattr(authorization, 'rows', altered)
    with pytest.raises(ValueError): preparation.consume(engine, account['session_id'], pid, observed_at=stamp)
    monkeypatch.setattr(authorization, 'rows', original)
    assert streams.read(engine, account['session_id']) == before
    assert preparation.capture(engine, account['session_id'])['records'][0]['status'] == 'PREPARED'


@pytest.mark.parametrize('marker', [None, 'unknown-authorization-v999'])
def test_legitimate_legacy_or_unknown_preparation_never_executes(database, monkeypatch, marker):
    engine, _, _ = database
    account, _, stamp = candidate(engine)
    # Build a legacy row directly: never weaken or bypass an immutable trigger.
    with Session(engine) as db, db.begin():
        record = db.get(Stream, account['session_id'])
        observations = streams.advance_locked(db, record, stamp, prepare_only=True)
        payload = {'version':preparation.VERSION,'session_id':account['session_id'],'base':preparation.base(record),'observations':observations}
        pid = digest({'version':preparation.VERSION,'payload':payload})
        db.add(Preparation(preparation_id=pid, session_id=account['session_id'], created_at=datetime.now(timezone.utc), payload=payload, payload_sha256=digest(payload), status='PREPARED', authorization_version=marker))
    if marker is None:
        assert not preparation.consume(engine, account['session_id'], pid, observed_at=stamp)
        row = preparation.capture(engine, account['session_id'])['records'][0]
        assert (row['status'], row['reason']) == ('CANCELLED','AUTHORIZATION_MISSING')
        assert authorization.capture(engine, account['session_id'])['batches'][0]['coverage'] == 'LEGACY_UNAVAILABLE'
    else:
        with pytest.raises(ValueError, match='Unsupported'): preparation.consume(engine, account['session_id'], pid, observed_at=stamp)
        with pytest.raises(ValueError): authorization.capture(engine, account['session_id'])
    assert streams.read(engine, account['session_id'])['fills'] == []


@pytest.mark.parametrize('statement', ["UPDATE paper_authorizations SET payload='{}'::jsonb", 'DELETE FROM paper_authorizations'])
def test_authorization_database_evidence_is_immutable(database, statement):
    engine, _, _ = database
    account, _, stamp = candidate(engine)
    preparation.prepare(engine, account['session_id'], observed_at=stamp)
    before = authorization.capture(engine, account['session_id'])
    with pytest.raises(DBAPIError):
        with engine.begin() as conn: conn.execute(text(statement))
    assert authorization.capture(engine, account['session_id']) == before


@pytest.mark.parametrize('phase', ['prepare','consume'])
def test_commit_fault_keeps_authorization_and_economic_facts_atomic(database, phase):
    engine, _, _ = database
    account, _, stamp = candidate(engine)
    pid = preparation.prepare(engine, account['session_id'], observed_at=stamp) if phase == 'consume' else None
    def fail(session):
        if session.get_bind() is engine:
            session.flush()
            raise RuntimeError('Injected authorization commit fault')
    event.listen(Session, 'before_commit', fail)
    try:
        with pytest.raises(RuntimeError, match='commit fault'):
            if phase == 'prepare': preparation.prepare(engine, account['session_id'], observed_at=stamp)
            else: preparation.consume(engine, account['session_id'], pid, observed_at=stamp)
    finally: event.remove(Session, 'before_commit', fail)
    batches = authorization.capture(engine, account['session_id'])['batches']
    assert len(batches) == (1 if phase == 'consume' else 0)
    if batches: assert batches[0]['status'] == 'PREPARED' and len(batches[0]['authorizations']) == 1
    assert streams.read(engine, account['session_id'])['fills'] == []


def test_concurrent_consumption_keeps_one_authorized_receipt(database):
    engine, _, _ = database
    account, _, stamp = candidate(engine)
    pid = preparation.prepare(engine, account['session_id'], observed_at=stamp)
    frozen = evidence(engine, account['session_id'])
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(lambda _: preparation.consume(engine, account['session_id'], pid, observed_at=stamp), range(8)))
    assert sum(results) == 1
    before = streams.read(engine, account['session_id'])
    assert not preparation.consume(engine, account['session_id'], pid, observed_at=stamp)
    assert streams.read(engine, account['session_id']) == before
    assert len(before['fills']) == 1 and evidence(engine, account['session_id']) == frozen


def test_authorization_api_is_read_only_and_response_is_bounded(database, monkeypatch):
    engine, _, settings = database
    account, _, stamp = candidate(engine)
    preparation.prepare(engine, account['session_id'], observed_at=stamp)
    before = authorization.capture(engine, account['session_id'])
    path = '/api/v1/paper/streams/' + account['session_id'] + '/authorizations'
    with TestClient(create_app(settings)) as client:
        assert client.get(path).json() == before
        assert client.get(path).status_code == 200
        assert client.post(path, json={}).status_code == 405
        assert client.get('/api/v1/paper/streams/' + str(uuid4()) + '/authorizations').status_code == 404
        monkeypatch.setattr(authorization, 'MAX_BYTES', 100)
        assert client.get(path).status_code == 409
    with pytest.raises(ValueError, match='32 MiB'): authorization.capture(engine, account['session_id'])
    assert streams.read(engine, account['session_id'])['fills'] == []


def test_volume_limited_projection_authorizes_only_partial_fill(database, monkeypatch):
    engine, _, _ = database
    from tests import test_paper_streams
    original_liquid = test_paper_streams.liquid
    monkeypatch.setattr(test_paper_streams, 'liquid', lambda values: [b.model_copy(update={'volume':Decimal('10')}) for b in original_liquid(values)])
    account, bars = setup(engine)
    save_candles(engine, [bars[2]])
    stamp = bars[2].close_time + timedelta(seconds=1)
    pid = preparation.prepare(engine, account['session_id'], observed_at=stamp)
    value = evidence(engine, account['session_id'])[0]['payload']
    assert value['proposed_order']['status'] == 'PARTIAL_CANCELLED'
    assert Decimal(value['projected_fill']['quantity']) == Decimal('1')
    assert value['decision'] == 'ALLOW_SIMULATION'
    assert preparation.consume(engine, account['session_id'], pid, observed_at=stamp)
    assert streams.read(engine, account['session_id'])['fills'][0] == value['projected_fill']


def test_multibar_batch_retains_stale_and_final_entry_risk_denials(database):
    engine, _, _ = database
    account, bars = setup(engine, values=[10,12,14,16,18,20,22], risk={'max_order_quote':'100','max_position_quote':'10000','max_drawdown':'1'})
    save_candles(engine, bars[2:5])
    stamp = bars[4].close_time + timedelta(seconds=1)
    pid = preparation.prepare(engine, account['session_id'], observed_at=stamp)
    values = evidence(engine, account['session_id'])
    assert len(values) == 3
    values.sort(key=lambda v:v['payload']['proposed_order']['execution_at'])
    reasons = [v['payload']['reason'] for v in values]
    assert reasons == ['STALE_BAR','STALE_BAR','MAX_ORDER_QUOTE']
    assert all(v['payload']['decision'] == 'DENY_SIMULATION' and v['payload']['projected_fill'] is None for v in values)
    assert preparation.consume(engine, account['session_id'], pid, observed_at=stamp)
    state = streams.read(engine, account['session_id'])
    assert state['fills'] == []
    assert state['orders'] == [v['payload']['proposed_order'] for v in values]


def test_commit_then_lost_ack_retries_without_reauthorizing_or_refilling(database):
    engine, _, _ = database
    account, _, stamp = candidate(engine)
    pid = preparation.prepare(engine, account['session_id'], observed_at=stamp)
    frozen = evidence(engine, account['session_id'])
    def consume_then_lose_ack():
        assert preparation.consume(engine, account['session_id'], pid, observed_at=stamp)
        raise ConnectionError('Injected authorization commit acknowledgement loss')
    with pytest.raises(ConnectionError, match='acknowledgement loss'):
        consume_then_lose_ack()
    before = streams.read(engine, account['session_id'])
    assert not preparation.consume(engine, account['session_id'], pid, observed_at=stamp)
    assert streams.read(engine, account['session_id']) == before
    assert len(before['fills']) == 1
    assert evidence(engine, account['session_id']) == frozen


def test_consumed_marker_without_actual_observation_is_rejected(database):
    from datetime import datetime,timezone
    engine,_,_=database;account,bars=setup(engine)
    save_candles(engine,[bars[2]])
    stamp=bars[2].close_time+timedelta(seconds=1)
    pid=preparation.prepare(engine,account['session_id'],observed_at=stamp)
    with Session(engine) as db,db.begin():
        prepared=db.get(Preparation,pid)
        prepared.status='CONSUMED';prepared.finished_at=datetime.now(timezone.utc)
    with pytest.raises(ValueError,match='lacks accepted observations'):
        authorization.capture(engine,account['session_id'])
    assert streams.read(engine,account['session_id'])['fills']==[]


def test_historic_authorization_after_finish_clock_is_rejected(database,monkeypatch):
    from datetime import datetime,timezone
    from types import SimpleNamespace
    engine,_,_=database;account,bars=setup(engine)
    save_candles(engine,[bars[2]])
    stamp=bars[2].close_time+timedelta(seconds=1)
    pid=preparation.prepare(engine,account['session_id'],observed_at=stamp)
    preparation.consume(engine,account['session_id'],pid,observed_at=stamp)
    with Session(engine) as db:
        original=authorization.rows(db,pid)[0]
        row=SimpleNamespace(**{k:getattr(original,k) for k in ['authorization_id','order_id','payload','payload_sha256']},recorded_at=datetime.now(timezone.utc))
    monkeypatch.setattr(authorization,'rows',lambda db,prepared_id:[row])
    with pytest.raises(ValueError,match='after preparation finished'):
        authorization.capture(engine,account['session_id'])


def test_history_scope_is_explicit_and_bounded_without_deleting_older_records(database):
    engine,_,_=database;account,bars,stamp=candidate(engine)
    for _ in range(21):
        preparation.prepare(engine,account['session_id'],observed_at=stamp)
        state=streams.read(engine,account['session_id'])
        streams.command(engine,account['session_id'],state['revision'],'pause' if state['status']=='RUNNING' else 'resume')
    before=streams.read(engine,account['session_id'])
    report=authorization.capture(engine,account['session_id'])
    assert report['total_batches']==21 and report['window_limit']==20 and report['has_older'] is True
    assert len(report['batches'])==20
    assert len(preparation.capture(engine,account['session_id'])['records'])==21
    assert streams.read(engine,account['session_id'])==before
