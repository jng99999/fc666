"""Production-path projected funding, using isolated PostgreSQL fixtures only."""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from datetime import timedelta, datetime, timezone
from decimal import Decimal
from uuid import uuid4
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import event, text, select
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session
from apps.api.main import create_app
from core.backtest.spot import BacktestConfig, digest
from core.market_data.storage import save_candles
from core.paper import funding, preparation, streams, authorization
from core.storage.models import (PaperFundingReservationRecord as Reservation, PaperFundingOutcomeRecord as Outcome,
    PaperPreparationRecord as Preparation, PaperStreamRecord as Stream, InstrumentRecord)
from tests.test_integration import database
from tests.test_paper_authorization import candidate
from tests.test_paper_streams import arrive


def report(engine,account):
    return funding.capture(engine,account['session_id'])


def test_reserve_before_effect_and_atomic_consumed_settlement(database):
    engine,_,_=database
    account,_,stamp=candidate(engine)
    before=streams.read(engine,account['session_id'])
    pid=preparation.prepare(engine,account['session_id'],observed_at=stamp)
    pending=report(engine,account)
    value=pending['batches'][-1]['reservation']
    assert pending['batches'][-1]['outcome'] is None
    assert Decimal(value['reserved_cash'])>0 and value['reserved_quantity']=='0'
    assert Decimal(pending['available_cash'])+Decimal(pending['reserved_cash'])==Decimal(before['account']['cash'])
    for key in ['revision','observations','orders','fills','account']:
        assert streams.read(engine,account['session_id'])[key]==before[key]
    assert preparation.consume(engine,account['session_id'],pid,observed_at=stamp)
    finished=report(engine,account)
    current=streams.read(engine,account['session_id'])
    assert finished['reserved_cash']==finished['reserved_quantity']=='0'
    assert finished['batches'][-1]['reservation']==value
    settled=finished['batches'][-1]['outcome']
    assert settled['economic_effects_applied'] and settled['cash_after']==current['account']['cash']
    assert settled['applied_ledger_sha256']==digest({key:current[key] for key in ['account','orders','fills','equity','signals','risk','pending']})


def test_partial_fill_reservation_includes_exact_fees(database):
    engine,_,_=database
    config=BacktestConfig(initial_cash='1000',period=2,fee_rate='.01',slippage_rate='.001',allocation='1',participation='.001').model_dump(mode='json')
    account,_,stamp=candidate(engine,config=config)
    pid=preparation.prepare(engine,account['session_id'],observed_at=stamp)
    value=report(engine,account)['batches'][-1]['reservation']
    with Session(engine) as db:
        auth=authorization.rows(db,pid)[0].payload
    fill=auth['projected_fill']
    assert auth['proposed_order']['status']=='PARTIAL_CANCELLED'
    assert Decimal(value['reserved_cash'])==Decimal(fill['quantity'])*Decimal(fill['price'])+Decimal(fill['fee'])
    assert preparation.consume(engine,account['session_id'],pid,observed_at=stamp)
    assert report(engine,account)['batches'][-1]['outcome']['release_cash']==value['reserved_cash']


def test_sell_reserves_inventory_without_cash_debit(database):
    engine,_,_=database
    account,bars,_=candidate(engine)
    arrive(engine,account,bars,2)
    before=arrive(engine,account,bars,3)
    save_candles(engine,[bars[4]])
    stamp=bars[4].close_time+timedelta(seconds=1)
    pid=preparation.prepare(engine,account['session_id'],observed_at=stamp)
    pending=report(engine,account)
    assert Decimal(pending['reserved_cash'])==0
    assert Decimal(pending['reserved_quantity'])==Decimal(before['account']['quantity'])
    assert Decimal(pending['available_quantity'])==0
    assert preparation.consume(engine,account['session_id'],pid,observed_at=stamp)
    after=report(engine,account)
    assert after['reserved_quantity']=='0' and Decimal(after['available_quantity'])==0


def test_risk_denied_orders_reserve_nothing(database):
    engine,_,_=database
    account,_,stamp=candidate(engine,risk={'max_order_quote':'1','max_position_quote':'10000','max_drawdown':'1'})
    pid=preparation.prepare(engine,account['session_id'],observed_at=stamp)
    pending=report(engine,account)
    assert pending['reserved_cash']==pending['reserved_quantity']=='0'
    assert preparation.consume(engine,account['session_id'],pid,observed_at=stamp)
    assert streams.read(engine,account['session_id'])['fills']==[]
    assert not report(engine,account)['batches'][-1]['outcome']['economic_effects_applied']


@pytest.mark.parametrize('cause',['control','expiry','source'])
def test_cancellation_releases_without_economic_effect(database,cause):
    engine,_,_=database
    account,_,stamp=candidate(engine)
    pid=preparation.prepare(engine,account['session_id'],observed_at=stamp)
    before=streams.read(engine,account['session_id'])['account']
    if cause=='control':
        streams.command(engine,account['session_id'],0,'pause')
    else:
        if cause=='source':
            with Session(engine) as db,db.begin():
                instrument=db.scalars(select(InstrumentRecord)).first()
                instrument.tick_size=Decimal('.02')
        assert not preparation.consume(engine,account['session_id'],pid,observed_at=stamp+timedelta(seconds=16) if cause=='expiry' else stamp)
    value=report(engine,account)
    assert value['reserved_cash']==value['reserved_quantity']=='0'
    assert not value['batches'][-1]['outcome']['economic_effects_applied']
    assert value['batches'][-1]['outcome']['applied_ledger_sha256'] is None
    assert streams.read(engine,account['session_id'])['account']==before


@pytest.mark.parametrize('table',['paper_funding_reservations','paper_funding_outcomes'])
@pytest.mark.parametrize('action',['UPDATE','DELETE'])
def test_immutable_funding_rows(database,table,action):
    engine,_,_=database
    account,_,stamp=candidate(engine)
    pid=preparation.prepare(engine,account['session_id'],observed_at=stamp)
    preparation.consume(engine,account['session_id'],pid,observed_at=stamp)
    with Session(engine) as db:
        with pytest.raises(DBAPIError):
            db.execute(text(f'DELETE FROM {table}' if action=='DELETE' else f'UPDATE {table} SET payload_sha256=payload_sha256'))


@pytest.mark.parametrize('phase',['prepare','consume'])
def test_flush_then_failure_rolls_back_every_effect(database,phase):
    engine,_,_=database
    account,_,stamp=candidate(engine)
    pid=preparation.prepare(engine,account['session_id'],observed_at=stamp) if phase=='consume' else None
    before=streams.read(engine,account['session_id'])
    table='paper_funding_reservations' if phase=='prepare' else 'paper_funding_outcomes'
    def fail(conn,cursor,statement,parameters,context,executemany):
        if statement.startswith('INSERT INTO '+table):raise RuntimeError('After funding insert before commit')
    event.listen(engine,'after_cursor_execute',fail)
    try:
        with pytest.raises(RuntimeError):
            if phase=='prepare':preparation.prepare(engine,account['session_id'],observed_at=stamp)
            else:preparation.consume(engine,account['session_id'],pid,observed_at=stamp)
    finally:event.remove(engine,'after_cursor_execute',fail)
    assert streams.read(engine,account['session_id'])==before
    with Session(engine) as db:
        assert db.scalar(select(Outcome)) is None
        if phase=='prepare':assert db.scalar(select(Preparation)) is None
        else:assert db.get(Preparation,pid).status=='PREPARED' and db.get(Reservation,pid) is not None


def test_concurrent_consume_and_lost_response_settle_once(database):
    engine,_,_=database
    account,_,stamp=candidate(engine)
    pid=preparation.prepare(engine,account['session_id'],observed_at=stamp)
    def consume(_):
        result=preparation.consume(engine,account['session_id'],pid,observed_at=stamp)
        if result:raise ConnectionError('Lost committed response')
        return result
    with ThreadPoolExecutor(max_workers=4) as pool:
        futures=[pool.submit(consume,index) for index in range(4)]
        winners=0
        for future in futures:
            try:assert future.result() is False
            except ConnectionError:winners+=1
    assert winners==1
    assert not preparation.consume(engine,account['session_id'],pid,observed_at=stamp)
    assert len(streams.read(engine,account['session_id'])['fills'])==1
    with Session(engine) as db:assert len(list(db.scalars(select(Outcome))))==1


def test_missing_declared_reservation_never_repairs_or_accepts(database):
    engine,_,_=database
    account,_,stamp=candidate(engine)
    pid=preparation.prepare(engine,account['session_id'],observed_at=stamp)
    with engine.begin() as conn:
        conn.execute(text('ALTER TABLE paper_funding_reservations DISABLE TRIGGER paper_funding_reservations_immutable'))
        conn.execute(text('DELETE FROM paper_funding_reservations WHERE preparation_id=:pid'),{'pid':pid})
        conn.execute(text('ALTER TABLE paper_funding_reservations ENABLE TRIGGER paper_funding_reservations_immutable'))
    with pytest.raises(ValueError):funding.capture(engine,account['session_id'])
    with pytest.raises(ValueError):preparation.consume(engine,account['session_id'],pid,observed_at=stamp)
    assert streams.read(engine,account['session_id'])['fills']==[]


def test_corrupt_reservation_and_missing_outcome_fail_closed(database):
    engine,_,_=database
    account,_,stamp=candidate(engine)
    pid=preparation.prepare(engine,account['session_id'],observed_at=stamp)
    preparation.consume(engine,account['session_id'],pid,observed_at=stamp)
    with engine.begin() as conn:
        conn.execute(text('ALTER TABLE paper_funding_outcomes DISABLE TRIGGER paper_funding_outcomes_immutable'))
        conn.execute(text('DELETE FROM paper_funding_outcomes WHERE preparation_id=:pid'),{'pid':pid})
        conn.execute(text('ALTER TABLE paper_funding_outcomes ENABLE TRIGGER paper_funding_outcomes_immutable'))
    with pytest.raises(ValueError):report(engine,account)


def test_legacy_pending_and_terminal_do_not_invent_reservations(database):
    engine,_,_=database
    account,_,stamp=candidate(engine)
    pid=preparation.prepare(engine,account['session_id'],observed_at=stamp)
    # Simulate a pre-deployment preparation in an isolated database only.
    with engine.begin() as conn:
        conn.execute(text('ALTER TABLE paper_preparations DISABLE TRIGGER paper_preparation_guard'))
        conn.execute(text('ALTER TABLE paper_funding_reservations DISABLE TRIGGER paper_funding_reservations_immutable'))
        conn.execute(text('DELETE FROM paper_funding_reservations WHERE preparation_id=:pid'),{'pid':pid})
        conn.execute(text('UPDATE paper_preparations SET funding_version=NULL WHERE preparation_id=:pid'),{'pid':pid})
        conn.execute(text('ALTER TABLE paper_preparations ENABLE TRIGGER paper_preparation_guard'))
        conn.execute(text('ALTER TABLE paper_funding_reservations ENABLE TRIGGER paper_funding_reservations_immutable'))
    pending=report(engine,account)
    assert pending['available_cash'] is pending['available_quantity'] is None
    assert pending['batches'][-1]['coverage']=='LEGACY_UNAVAILABLE'
    preparation.consume(engine,account['session_id'],pid,observed_at=stamp)
    assert report(engine,account)['batches'][-1]['coverage']=='LEGACY_UNAVAILABLE'
    with Session(engine) as db:assert db.get(Reservation,pid) is None


def test_read_only_api_unknown_method_and_conflict(database):
    engine,_,_=database
    account,_,stamp=candidate(engine)
    pid=preparation.prepare(engine,account['session_id'],observed_at=stamp)
    client=TestClient(create_app())
    url='/api/v1/paper/streams/'+account['session_id']+'/funding'
    before=streams.read(engine,account['session_id'])
    result=client.get(url)
    assert result.status_code==200 and result.json()['window_limit']==20
    assert not result.json()['trading_enabled']
    assert client.post(url,json={}).status_code==405
    assert client.get('/api/v1/paper/streams/'+str(uuid4())+'/funding').status_code==404
    assert streams.read(engine,account['session_id'])==before
    with engine.begin() as conn:
        conn.execute(text('ALTER TABLE paper_funding_reservations DISABLE TRIGGER paper_funding_reservations_immutable'))
        conn.execute(text("UPDATE paper_funding_reservations SET payload_sha256='invalid' WHERE preparation_id=:pid"),{'pid':pid})
        conn.execute(text('ALTER TABLE paper_funding_reservations ENABLE TRIGGER paper_funding_reservations_immutable'))
    assert client.get(url).status_code==409


def test_rehashed_changed_amount_is_not_authorization(database):
    engine,_,_=database
    account,_,stamp=candidate(engine)
    pid=preparation.prepare(engine,account['session_id'],observed_at=stamp)
    with Session(engine) as db:
        value=deepcopy(db.get(Reservation,pid).payload)
    value['reserved_cash']='0'
    with engine.begin() as conn:
        conn.execute(text('ALTER TABLE paper_funding_reservations DISABLE TRIGGER paper_funding_reservations_immutable'))
        with Session(bind=conn) as db:
            row=db.get(Reservation,pid);row.payload=value;row.payload_sha256=digest(value);db.flush()
        conn.execute(text('ALTER TABLE paper_funding_reservations ENABLE TRIGGER paper_funding_reservations_immutable'))
    with pytest.raises(ValueError):preparation.consume(engine,account['session_id'],pid,observed_at=stamp)
    assert streams.read(engine,account['session_id'])['fills']==[]


def test_window_is_latest20_with_explicit_older_scope(database):
    engine,_,_=database
    account,_,stamp=candidate(engine)
    pid=preparation.prepare(engine,account['session_id'],observed_at=stamp)
    with Session(engine) as db,db.begin():
        current=db.get(Preparation,pid)
        for index in range(21):
            payload=deepcopy(current.payload);payload['base']['revision']=index+1
            identity=digest({'version':preparation.VERSION,'payload':payload})
            created=datetime.now(timezone.utc)-timedelta(minutes=index+2)
            db.add(Preparation(preparation_id=identity,session_id=account['session_id'],created_at=created,finished_at=created+timedelta(seconds=1),
                payload=payload,payload_sha256=digest(payload),status='CANCELLED',reason='EXPIRED',authorization_version=None,funding_version=None))
    value=report(engine,account)
    assert value['total_batches']==22 and value['has_older'] and len(value['batches'])==20
    assert value['batches'][-1]['preparation_id']==pid
    assert Decimal(value['reserved_cash'])>0
    assert sum(row['coverage']=='LEGACY_UNAVAILABLE' for row in value['batches'])==19


def test_separate_accounts_never_share_simulated_capital(database):
    engine,_,_=database
    first,_,stamp=candidate(engine)
    second,_,_=candidate(engine)
    preparation.prepare(engine,first['session_id'],observed_at=stamp)
    before=streams.read(engine,second['session_id'])
    preparation.prepare(engine,second['session_id'],observed_at=stamp)
    a,b=report(engine,first),report(engine,second)
    assert a['reserved_cash']==b['reserved_cash']
    assert streams.read(engine,second['session_id'])==before
    assert a['batches'][-1]['reservation']['session_id']!=b['batches'][-1]['reservation']['session_id']


def test_funding_cannot_recreate_missing_original_creation(database):
    engine,_,_=database
    account,_,stamp=candidate(engine)
    pid=preparation.prepare(engine,account['session_id'],observed_at=stamp)
    with engine.begin() as conn:
        conn.execute(text('ALTER TABLE paper_lifecycle_events DISABLE TRIGGER paper_lifecycle_events_immutable'))
        conn.execute(text('ALTER TABLE paper_lifecycle_coverage DISABLE TRIGGER paper_lifecycle_coverage_immutable'))
        conn.execute(text('DELETE FROM paper_lifecycle_events WHERE authorization_id IN (SELECT authorization_id FROM paper_authorizations WHERE preparation_id=:pid)'),{'pid':pid})
        conn.execute(text('DELETE FROM paper_lifecycle_coverage WHERE preparation_id=:pid'),{'pid':pid})
        conn.execute(text('ALTER TABLE paper_lifecycle_events ENABLE TRIGGER paper_lifecycle_events_immutable'))
        conn.execute(text('ALTER TABLE paper_lifecycle_coverage ENABLE TRIGGER paper_lifecycle_coverage_immutable'))
    with pytest.raises(ValueError):preparation.prepare(engine,account['session_id'],observed_at=stamp)
    with pytest.raises(ValueError):preparation.consume(engine,account['session_id'],pid,observed_at=stamp)
    assert streams.read(engine,account['session_id'])['fills']==[]
    with Session(engine) as db:
        from core.storage.models import PaperLifecycleCoverageRecord
        assert db.get(PaperLifecycleCoverageRecord,pid) is None
