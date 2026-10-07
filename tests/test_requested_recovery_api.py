"""Read-only PostgreSQL recovery; synthetic histories stay in isolated databases."""
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from apps.api.main import create_app
from apps.api.settings import Settings
from core.paper import requested_recovery as recovery, requested_sources as sources, requested_journal as journal, requested_finalization as finalization
from tests.test_integration import database
from tests.test_requested_sources import prepared, reviewed, accept
from tests.test_requested_controls import command
from tests.test_requested_execution import event, fill
from tests.test_requested_finalization import time

PATH = '/api/v1/paper-requested/recovery'


def test_recovery_read_preserves_unknown_hold_and_original_submission_after_stop(database):
    engine, _, settings = database; req = prepared(engine)
    for value in [event(req, 0, 'SUBMIT'), event(req, 1, 'UNKNOWN_SUBMISSION')]:
        body, _ = reviewed(engine, req, value); accept(engine, body)
    before = sources.capture(engine, 'account')
    with TestClient(create_app(settings)) as client:
        response = client.get(PATH, params={'account_id': 'account'})
        assert response.status_code == 200 and response.headers['cache-control'] == 'no-store'
        report = recovery.verify(response.json()); row = report['requests'][0]
        assert row['action'] == 'WAIT_LOCAL_RESULT' and row['funding']['reserved_cash'].startswith('404.')
        assert client.post(PATH, json={'action':'submit'}).status_code == 405
        assert sources.capture(engine, 'account') == before
        command(engine, 'stop', 'STOP', 2)
        value, _ = reviewed(engine, req, fill(req, 2)); accept(engine, value)
        after = client.get(PATH, params={'account_id': 'account'}).json()
        assert after['requests'][0]['submission_attribution'] == row['submission_attribution']
        assert after['requests'][0]['action'] == 'RECONCILE_LOCAL_PREFIX'
        assert recovery.verify(after)['account']['cash'] == '909.10'


def test_empty_legacy_and_void_v2_are_explicit_without_invented_submission(database):
    engine, _, settings = database; req = prepared(engine)
    with TestClient(create_app(settings)) as client:
        pending = client.get(PATH, params={'account_id':'account'}).json()
        assert pending['requests'][0]['action'] == 'NOT_SUBMITTED_LOCAL'
        finalization.finalize(engine, 'account', req['request_id'], 'void', 1, time(1))
        void = client.get(PATH, params={'account_id':'account'}).json()
        assert recovery.verify(void)['requests'][0]['action'] == 'LOCAL_VOIDED'
        assert void['requests'][0]['submission_attribution'] is None and void['active_request_id'] is None
        journal.create(engine, 'empty', 'fixture:spot', req['base'], time(0))
        assert recovery.verify(client.get(PATH, params={'account_id':'empty'}).json())['requests'] == []


def test_missing_corrupt_and_unavailable_return_no_partial_recovery(database):
    engine, _, settings = database; req = prepared(engine)
    value, _ = reviewed(engine, req, event(req, 0, 'SUBMIT')); accept(engine, value)
    with TestClient(create_app(settings)) as client:
        response = client.get(PATH, params={'account_id':'missing'})
        assert response.status_code == 404 and response.headers['cache-control'] == 'no-store'
        for params in [{}, {'account_id':''}, {'account_id':'x'*129}]:
            assert client.get(PATH, params=params).status_code == 422
        with engine.begin() as db:
            db.execute(text('ALTER TABLE requested_paper_sources DISABLE TRIGGER requested_paper_sources_immutable'))
            db.execute(text('DELETE FROM requested_paper_sources'))
            db.execute(text('ALTER TABLE requested_paper_sources ENABLE TRIGGER requested_paper_sources_immutable'))
        response = client.get(PATH, params={'account_id':'account'})
        assert response.status_code == 409 and response.json() == {'detail':'Explicit Paper recovery evidence cannot be verified'}
    unavailable = Settings(database_url='postgresql+psycopg://invalid:invalid@127.0.0.1:1/no_db')
    with TestClient(create_app(unavailable)) as client:
        response = client.get(PATH, params={'account_id':'account'})
        assert response.status_code == 503 and set(response.json()) == {'detail'}


def test_lock_timeout_is_generic_and_does_not_mutate(database):
    engine, _, settings = database; prepared(engine); before = sources.capture(engine, 'account')
    with engine.connect() as conn:
        transaction = conn.begin()
        conn.execute(text("SELECT account_id FROM requested_paper_accounts WHERE account_id='account' FOR UPDATE"))
        with TestClient(create_app(settings)) as client:
            response = client.get(PATH, params={'account_id':'account'})
            assert response.status_code == 503 and response.headers['cache-control'] == 'no-store'
        transaction.rollback()
    assert sources.capture(engine, 'account') == before
