"""Read-only inspection over real isolated PostgreSQL; no product seed data."""
from concurrent.futures import ThreadPoolExecutor
from threading import Event
from datetime import timedelta
from decimal import Decimal
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import event as sql_event, select, text
from sqlalchemy.orm import Session
from apps.api.main import create_app
from apps.api.settings import Settings
from core.paper import requested_journal as journal, requested_inspection as inspection
from core.storage.models import RequestedPaperAccountRecord as Account
from tests.test_integration import database
from tests.test_requested_execution import BASE, STAMP, request, event, fill, cumulative
from tests.test_requested_journal import setup

PATH='/api/v1/paper-requested/journal'


def test_api_complete_export_is_verified_and_inspection_changes_no_records(database):
    engine,_,settings=database;req=setup(engine)
    values=[event(req,0,'SUBMIT'),fill(req,1),event(req,2,'CANCEL_REQUEST'),event(req,3,'CANCEL_ACK'),cumulative(req,4,'1','0.90','90',seal=True)]
    for value in values:journal.accept(engine,'account',req['request_id'],value)
    before=journal.read(engine,'account')
    with TestClient(create_app(settings)) as client:
        one=client.get(PATH,params={'account_id':'account'})
        two=client.get(PATH,params={'account_id':'account'})
        assert one.status_code==two.status_code==200 and one.json()==two.json()
        assert one.headers['cache-control']=='no-store'
        report=one.json();assert inspection.verify(report)==before
        assert Decimal(report['journal']['account']['cash'])==Decimal('909.10')
        assert report['journal']['revision']==6 and report['journal']['active_request_id'] is None
        assert len(report['journal']['requests'][0]['events'])==5
        assert not report['journal']['external_submission_allowed']
        assert client.get('/health/ready').status_code==200
        assert client.get('/api/v1/paper/streams').status_code==200
        assert client.get('/api/v1/paper/sessions').status_code==200
        assert client.get('/api/v1/live-orders').status_code==404
        assert client.post(PATH,params={'account_id':'account'},json={'action':'submit'}).status_code==405
    assert journal.read(engine,'account')==before


def test_api_empty_pending_and_opaque_account_identity_are_supported(database):
    engine,_,settings=database
    identity='本地 / account:1'
    empty=journal.create(engine,identity,'fixture:spot',BASE,STAMP.isoformat())
    req=request(account_id=identity);journal.prepare(engine,req)
    with TestClient(create_app(settings)) as client:
        report=client.get(PATH,params={'account_id':identity}).json()
        view=inspection.verify(report)
        assert view['opening']['account_id']==identity and view['revision']==1
        assert view['requests'][0]['summary']['state']=='CREATED'
        assert Decimal(view['requests'][0]['summary']['funding']['reserved_cash'])==404
        journal.create(engine,'empty','fixture:spot',BASE,STAMP.isoformat())
        fresh=client.get(PATH,params={'account_id':'empty'})
        assert fresh.status_code==200 and inspection.verify(fresh.json())['revision']==0
        assert empty['revision']==0


def test_api_missing_validation_and_unavailable_errors_are_distinct(database):
    engine,_,settings=database
    with TestClient(create_app(settings)) as client:
        response=client.get(PATH,params={'account_id':'not-found'})
        assert response.status_code==404 and set(response.json())=={'detail'}
        assert response.headers['cache-control']=='no-store'
        for params in [{},{'account_id':''},{'account_id':'x'*129}]:
            assert client.get(PATH,params=params).status_code==422
    unavailable=Settings(database_url='postgresql+psycopg://invalid:invalid@127.0.0.1:1/unavailable')
    with TestClient(create_app(unavailable)) as client:
        response=client.get(PATH,params={'account_id':'account'})
        assert response.status_code==503 and response.json()=={'detail':'Explicit Paper journal temporarily unavailable'}


def test_api_corrupt_cache_returns_only_generic_failure(database):
    engine,_,settings=database;setup(engine)
    with engine.begin() as conn:
        conn.execute(text("UPDATE requested_paper_accounts SET revision=revision+1 WHERE account_id='account'"))
    with TestClient(create_app(settings)) as client:
        response=client.get(PATH,params={'account_id':'account'})
        assert response.status_code==409 and response.json()=={'detail':'Explicit Paper journal cannot be verified'}
        assert response.headers['cache-control']=='no-store'
    with engine.connect() as conn:assert conn.execute(text('SELECT revision FROM requested_paper_accounts')).scalar()==2


def test_api_capacity_failure_has_no_partial_export(database,monkeypatch):
    engine,_,settings=database;setup(engine)
    before=journal.read(engine,'account')
    monkeypatch.setattr(inspection.contract,'MAX_BYTES',100)
    with TestClient(create_app(settings)) as client:
        response=client.get(PATH,params={'account_id':'account'})
        assert response.status_code==409 and set(response.json())=={'detail'}
    monkeypatch.undo()
    assert journal.read(engine,'account')==before


def test_api_inspection_waits_for_atomic_fill_commit_and_returns_one_revision(database):
    engine,_,settings=database;req=setup(engine)
    journal.accept(engine,'account',req['request_id'],event(req,0,'SUBMIT'))
    writer_ready=Event();release=Event();reader_started=Event()
    def hold(conn,cursor,statement,parameters,context,executemany):
        if statement.startswith('INSERT INTO requested_paper_events'):
            writer_ready.set()
            if not release.wait(10):raise RuntimeError('Fixture release timed out')
    sql_event.listen(engine,'after_cursor_execute',hold)
    app=create_app(settings)
    def observe(conn,cursor,statement,parameters,context,executemany):
        if 'requested_paper_accounts' in statement and 'FOR UPDATE' in statement:reader_started.set()
    sql_event.listen(app.state.engine,'before_cursor_execute',observe)
    try:
        with TestClient(app) as client,ThreadPoolExecutor(2) as pool:
            writer=pool.submit(journal.accept,engine,'account',req['request_id'],fill(req,1))
            assert writer_ready.wait(5)
            reader=pool.submit(client.get,PATH,params={'account_id':'account'})
            try:
                assert reader_started.wait(5) and not reader.done()
            finally:release.set()
            writer.result(timeout=10);response=reader.result(timeout=10)
            assert response.status_code==200
            view=inspection.verify(response.json())
            assert view['revision']==3 and view['total_events']==2
            assert Decimal(view['account']['cash'])==Decimal('909.10')
            assert view==journal.read(engine,'account')
    finally:
        release.set();sql_event.remove(engine,'after_cursor_execute',hold)
        sql_event.remove(app.state.engine,'before_cursor_execute',observe)


def test_api_busy_account_times_out_without_changes_then_recovers(database):
    engine,_,settings=database;setup(engine)
    before=journal.read(engine,'account')
    with TestClient(create_app(settings)) as client:
        with Session(engine) as db,db.begin():
            db.scalar(select(Account).where(Account.account_id=='account').with_for_update())
            response=client.get(PATH,params={'account_id':'account'})
            assert response.status_code==503 and set(response.json())=={'detail'}
        response=client.get(PATH,params={'account_id':'account'})
        assert response.status_code==200 and inspection.verify(response.json())==before
    assert journal.read(engine,'account')==before


def test_ready_detects_missing_new_journal_table(database):
    engine,_,settings=database;setup(engine)
    # Preserve dependent source evidence while making the required relation unavailable.
    with engine.begin() as conn:conn.execute(text('ALTER TABLE requested_paper_events RENAME TO unavailable_requested_paper_events'))
    with TestClient(create_app(settings)) as client:
        assert client.get('/health/ready').status_code==503
        response=client.get(PATH,params={'account_id':'account'})
        assert response.status_code==503 and set(response.json())=={'detail'}


@pytest.mark.parametrize('value',[0,True,5001,'2000'])
def test_library_rejects_unbounded_or_noninteger_read_timeout(value):
    with pytest.raises(ValueError):journal.read(None,'account',lock_timeout_ms=value)
