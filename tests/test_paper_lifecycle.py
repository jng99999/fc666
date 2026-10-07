"""Local Paper lifecycle facts, isolated PostgreSQL and fixture-only reducers."""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import event, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from apps.api.main import create_app
from core.backtest.spot import digest
from core.paper import authorization, lifecycle, preparation, streams
from core.storage.models import CandleRecord, PaperLifecycleEventRecord as Event, PaperPreparationRecord as Preparation, PaperStreamRecord as Stream
from tests.test_integration import database
from tests.test_paper_authorization import candidate


def orders(engine, account):
    return lifecycle.capture(engine, account['session_id'])['batches'][-1]['orders']


def redelivery(row, sequence=None, kind=None, payload=None):
    return {'recorded_at': row['recorded_at'], **lifecycle.descriptor(
        row['authorization_id'], row['sequence'] if sequence is None else sequence,
        row['kind'] if kind is None else kind, row['payload'] if payload is None else payload)}


def test_creation_commits_before_effect_and_terminal_receipts_match(database):
    engine, _, _ = database
    account, _, stamp = candidate(engine)
    before = streams.read(engine, account['session_id'])
    pid = preparation.prepare(engine, account['session_id'], observed_at=stamp)
    pending = orders(engine, account)[0]
    assert pending['summary']['state'] == 'AWAITING_CONSUMPTION'
    assert pending['summary']['cumulative_quantity'] == '0'
    assert [r['kind'] for r in pending['events']] == ['CREATED']
    for key in ['revision','observations','orders','fills','account']:
        assert streams.read(engine, account['session_id'])[key] == before[key]
    assert preparation.consume(engine, account['session_id'], pid, observed_at=stamp)
    item = orders(engine, account)[0]
    state = streams.read(engine, account['session_id'])
    fill = state['fills'][0]
    assert item['events'][0] == pending['events'][0]
    assert item['summary']['state'] == 'FILLED'
    assert Decimal(item['summary']['cumulative_quantity']) == Decimal(fill['quantity'])
    assert Decimal(item['summary']['cumulative_fees']) == Decimal(fill['fee'])
    assert Decimal(item['summary']['cumulative_notional']) == Decimal(fill['quantity'])*Decimal(fill['price'])
    assert item['events'][-1]['payload']['receipt'] == {'order':state['orders'][0],'fill':fill}
    assert lifecycle.reduce(list(reversed(item['events'])) + item['events']) == item['summary']


@pytest.mark.parametrize('terminal', ['PARTIAL_CANCELLED','REJECTED'])
def test_partial_or_risk_denied_lifecycle_preserves_original_receipt(database, monkeypatch, terminal):
    engine, _, _ = database
    if terminal == 'PARTIAL_CANCELLED':
        from tests import test_paper_streams
        original = test_paper_streams.liquid
        monkeypatch.setattr(test_paper_streams, 'liquid', lambda values: [r.model_copy(update={'volume':Decimal('10')}) for r in original(values)])
        account, _, stamp = candidate(engine)
    else:
        account, _, stamp = candidate(engine, risk={'max_order_quote':'100','max_position_quote':'10000','max_drawdown':'1'})
    pid = preparation.prepare(engine, account['session_id'], observed_at=stamp)
    assert preparation.consume(engine, account['session_id'], pid, observed_at=stamp)
    item = orders(engine, account)[0]
    state = streams.read(engine, account['session_id'])
    assert item['summary']['state'] == terminal
    assert item['events'][-1]['payload']['receipt'] == {'order':state['orders'][0],'fill':state['fills'][0] if state['fills'] else None}
    assert Decimal(item['summary']['cumulative_quantity']) == (Decimal(state['fills'][0]['quantity']) if state['fills'] else 0)
    assert Decimal(item['summary']['cumulative_fees']) == (Decimal(state['fills'][0]['fee']) if state['fills'] else 0)


@pytest.mark.parametrize('reason', ['CONTROL_CHANGED','EXPIRED','SOURCE_CHANGED'])
def test_cancellation_has_no_fill_and_retains_creation(database, reason):
    engine, _, _ = database
    account, bars, stamp = candidate(engine)
    pid = preparation.prepare(engine, account['session_id'], observed_at=stamp)
    created = orders(engine, account)[0]['events'][0]
    if reason == 'CONTROL_CHANGED':
        streams.command(engine, account['session_id'], 0, 'pause')
    elif reason == 'SOURCE_CHANGED':
        with Session(engine) as db, db.begin():
            db.get(CandleRecord, (bars[2].instrument_id,bars[2].timeframe,bars[2].open_time)).volume += 1
    else:
        stamp = bars[2].close_time + timedelta(seconds=30)
    assert not preparation.consume(engine, account['session_id'], pid, observed_at=stamp)
    item = orders(engine, account)[0]
    assert item['events'][0] == created
    assert [r['kind'] for r in item['events']] == ['CREATED','OUTCOME']
    assert item['summary']['state'] == 'CANCELLED'
    assert item['summary']['cumulative_quantity'] == '0'
    assert item['events'][-1]['payload']['reason'] == reason
    assert item['events'][-1]['payload']['receipt'] is None
    assert streams.read(engine, account['session_id'])['fills'] == []
    snapshot = lifecycle.capture(engine, account['session_id'])
    assert not preparation.consume(engine, account['session_id'], pid, observed_at=stamp)
    assert lifecycle.capture(engine, account['session_id']) == snapshot


def test_fixture_split_fills_reduce_cumulative_quantity_fee_and_notional(database):
    engine, _, _ = database
    account, _, stamp = candidate(engine)
    pid = preparation.prepare(engine, account['session_id'], observed_at=stamp)
    preparation.consume(engine, account['session_id'], pid, observed_at=stamp)
    item = orders(engine, account)[0]
    created, filled, outcome = item['events']
    first = deepcopy(filled['payload']); second = deepcopy(first)
    # Distinct 1/4 and 3/4 fill copies exercise cumulative accounting.
    for key in ['quantity','fee']:
        total = Decimal(filled['payload']['fill'][key])
        first['fill'][key] = str(total/4)
        second['fill'][key] = str(total-total/4)
    values = [created,redelivery(filled,1,payload=first),redelivery(filled,2,payload=second),redelivery(outcome,3)]
    summary = lifecycle.reduce(values)
    assert summary['unique_fills'] == 2
    assert summary['state'] == item['summary']['state']
    for key in ['cumulative_quantity','cumulative_fees','cumulative_notional']:
        assert Decimal(summary[key]) == Decimal(item['summary'][key])
    # Pure reducer supports split fixture receipts; persisted producer is restricted to 0..2.
    with pytest.raises(DBAPIError):
        with Session(engine) as db, db.begin():
            db.add(Event(recorded_at=datetime.now(timezone.utc),**lifecycle.descriptor(
                outcome['authorization_id'],3,'OUTCOME',outcome['payload'])))
    assert orders(engine,account)[0] == item


@pytest.mark.parametrize('fault', ['same_id_conflict','same_sequence','gap','repeated_fill','overflow','late_fill','denied_fill'])
def test_reducer_rejects_ambiguous_or_unauthorized_delivery(database, fault):
    engine, _, _ = database
    risk = {'max_order_quote':'100','max_position_quote':'10000','max_drawdown':'1'} if fault == 'denied_fill' else None
    account, _, stamp = candidate(engine, **({'risk':risk} if risk else {}))
    pid = preparation.prepare(engine, account['session_id'], observed_at=stamp)
    preparation.consume(engine, account['session_id'], pid, observed_at=stamp)
    values = deepcopy(orders(engine, account)[0]['events'])
    if fault == 'same_id_conflict':
        extra = deepcopy(values[0]); extra['recorded_at'] = datetime.now(timezone.utc).isoformat(); values.append(extra)
    elif fault == 'same_sequence':
        extra = deepcopy(values[1]['payload']); extra['fill']['fee'] = '0'; values.append(redelivery(values[1],payload=extra))
    elif fault == 'gap': values.pop(1)
    elif fault == 'repeated_fill': values = [values[0],values[1],redelivery(values[1],2)]
    elif fault == 'overflow':
        extra = deepcopy(values[1]['payload']); extra['fill']['quantity'] = str(Decimal(extra['fill']['quantity'])+1)
        values[1] = redelivery(values[1],payload=extra)
    elif fault == 'late_fill': values.append(redelivery(values[1],3))
    else:
        auth = values[0]['payload']['authorization']; order = auth['proposed_order']
        fill = {**order,'quantity':'1','price':'14','fee':'0'}
        values = [values[0],redelivery(values[0],1,'FILL',{'version':lifecycle.VERSION,'authorization_id':values[0]['authorization_id'],'fill':fill})]
    with pytest.raises(ValueError): lifecycle.reduce(values)


@pytest.mark.parametrize('table', ['paper_lifecycle_events','paper_lifecycle_coverage'])
@pytest.mark.parametrize('operation', ['UPDATE','DELETE'])
def test_lifecycle_database_evidence_is_immutable(database, table, operation):
    engine, _, _ = database
    account, _, stamp = candidate(engine)
    pid = preparation.prepare(engine, account['session_id'], observed_at=stamp)
    preparation.consume(engine, account['session_id'], pid, observed_at=stamp)
    before = lifecycle.capture(engine, account['session_id'])
    statement = f'DELETE FROM {table}' if operation == 'DELETE' else f"UPDATE {table} SET recorded_at=recorded_at+interval '1 second'"
    with pytest.raises(DBAPIError):
        with engine.begin() as conn: conn.execute(text(statement))
    assert lifecycle.capture(engine, account['session_id']) == before


@pytest.mark.parametrize('phase', ['prepare','consume'])
def test_transaction_failure_preserves_only_independently_committed_creation(database, phase):
    engine, _, _ = database
    account, _, stamp = candidate(engine)
    pid = preparation.prepare(engine, account['session_id'], observed_at=stamp) if phase == 'consume' else None
    before = lifecycle.capture(engine, account['session_id'])
    def fail(session):
        if session.get_bind() is engine:
            session.flush()
            raise RuntimeError('Injected lifecycle commit failure')
    event.listen(Session,'before_commit',fail)
    try:
        with pytest.raises(RuntimeError,match='commit failure'):
            if phase == 'prepare': preparation.prepare(engine, account['session_id'], observed_at=stamp)
            else: preparation.consume(engine, account['session_id'], pid, observed_at=stamp)
    finally: event.remove(Session,'before_commit',fail)
    assert lifecycle.capture(engine, account['session_id']) == before
    assert streams.read(engine, account['session_id'])['fills'] == []
    if phase == 'consume': assert orders(engine, account)[0]['summary']['state'] == 'AWAITING_CONSUMPTION'


def test_concurrent_and_lost_ack_retries_keep_event_identities(database):
    engine, _, _ = database
    account, _, stamp = candidate(engine)
    pid = preparation.prepare(engine, account['session_id'], observed_at=stamp)
    created = orders(engine, account)[0]['events'][0]
    def consume_then_lose_ack():
        result = preparation.consume(engine,account['session_id'],pid,observed_at=stamp)
        if result: raise ConnectionError('Lost local committed acknowledgement')
        return result
    lost = 0
    with ThreadPoolExecutor(max_workers=4) as pool:
        futures = [pool.submit(consume_then_lose_ack) for _ in range(8)]
        for future in futures:
            try: assert future.result() is False
            except ConnectionError: lost += 1
    assert lost == 1
    before = lifecycle.capture(engine, account['session_id'])
    assert not preparation.consume(engine,account['session_id'],pid,observed_at=stamp)
    assert lifecycle.capture(engine, account['session_id']) == before
    assert orders(engine, account)[0]['events'][0] == created
    assert len(streams.read(engine, account['session_id'])['fills']) == 1


def legacy_preparation(engine, account, stamp, authorized):
    with Session(engine) as db, db.begin():
        record = db.get(Stream,account['session_id'])
        observations = streams.advance_locked(db,record,stamp,prepare_only=True)
        payload = {'version':preparation.VERSION,'session_id':account['session_id'],'base':preparation.base(record),'observations':observations}
        pid = digest({'version':preparation.VERSION,'payload':payload})
        prepared = Preparation(preparation_id=pid,session_id=account['session_id'],created_at=datetime.now(timezone.utc),payload=payload,payload_sha256=digest(payload),status='PREPARED',authorization_version=authorization.VERSION if authorized else None)
        db.add(prepared); db.flush()
        if authorized: authorization.persist(db,record,prepared)
    return pid


def test_legacy_pending_requires_separate_enrollment_without_rewriting_evidence(database):
    engine, _, _ = database
    account, _, stamp = candidate(engine)
    pid = legacy_preparation(engine,account,stamp,True)
    assert lifecycle.capture(engine,account['session_id'])['batches'][0]['coverage'] == 'LEGACY_UNAVAILABLE'
    with pytest.raises(ValueError,match='independently enrolled'):
        preparation.consume(engine,account['session_id'],pid,observed_at=stamp)
    assert streams.read(engine,account['session_id'])['fills'] == []
    assert preparation.prepare(engine,account['session_id'],observed_at=stamp) == pid
    batch = lifecycle.capture(engine,account['session_id'])['batches'][0]
    assert batch['origin'] == 'LEGACY_PENDING_ENROLLMENT'
    assert batch['orders'][0]['summary']['state'] == 'AWAITING_CONSUMPTION'
    assert preparation.consume(engine,account['session_id'],pid,observed_at=stamp)
    assert orders(engine,account)[0]['summary']['state'] == 'FILLED'


def test_legacy_historical_cancellation_remains_explicitly_unavailable(database):
    engine, _, _ = database
    account, _, stamp = candidate(engine)
    pid = legacy_preparation(engine,account,stamp,False)
    assert not preparation.consume(engine,account['session_id'],pid,observed_at=stamp)
    batch = lifecycle.capture(engine,account['session_id'])['batches'][0]
    assert batch['status'] == 'CANCELLED' and batch['coverage'] == 'LEGACY_UNAVAILABLE'
    assert batch['orders'] == [] and batch['origin'] is None


def test_missing_declared_events_fail_without_self_repair(database,monkeypatch):
    engine, _, _ = database
    account, _, stamp = candidate(engine)
    pid = preparation.prepare(engine,account['session_id'],observed_at=stamp)
    before = lifecycle.capture(engine,account['session_id'])
    original = lifecycle.event_rows
    monkeypatch.setattr(lifecycle,'event_rows',lambda db,aid:[])
    with pytest.raises(ValueError,match='coverage differs'): lifecycle.capture(engine,account['session_id'])
    with pytest.raises(ValueError,match='coverage differs'): preparation.prepare(engine,account['session_id'],observed_at=stamp)
    with pytest.raises(ValueError,match='coverage differs'): preparation.consume(engine,account['session_id'],pid,observed_at=stamp)
    monkeypatch.setattr(lifecycle,'event_rows',original)
    assert lifecycle.capture(engine,account['session_id']) == before
    assert streams.read(engine,account['session_id'])['fills'] == []


def test_read_only_api_missing_post_bounds_and_history_window(database,monkeypatch):
    engine, _, settings = database
    account, _, stamp = candidate(engine)
    for _ in range(21):
        preparation.prepare(engine,account['session_id'],observed_at=stamp)
        current = streams.read(engine,account['session_id'])
        streams.command(engine,account['session_id'],current['revision'],'pause' if current['status']=='RUNNING' else 'resume')
    before = streams.read(engine,account['session_id'])
    report = lifecycle.capture(engine,account['session_id'])
    assert report['total_batches'] == 21 and report['window_limit'] == 20 and report['has_older']
    assert len(report['batches']) == 20
    assert len(preparation.capture(engine,account['session_id'])['records']) == 21
    url = '/api/v1/paper/streams/'+account['session_id']+'/lifecycle'
    with TestClient(create_app(settings)) as client:
        assert client.get(url).status_code == 200
        assert client.get(url).json() == report
        assert client.post(url,json={}).status_code == 405
        assert client.get('/api/v1/paper/streams/'+str(uuid4())+'/lifecycle').status_code == 404
        monkeypatch.setattr(lifecycle,'MAX_BYTES',100)
        assert client.get(url).status_code == 409
    assert streams.read(engine,account['session_id']) == before
