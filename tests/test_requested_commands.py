"""Local operator grants use fixture credentials and isolated PostgreSQL only."""
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from datetime import timedelta
import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError
from apps.api.main import create_app
from apps.api.settings import Settings
from core.paper import requested_controls as controls, requested_journal as journal
from tests.test_integration import database
from tests.test_requested_controls import setup,command
from tests.test_requested_execution import STAMP,request

PATH='/api/v1/paper-requested/control-commands'
TOKEN='fixture-only-operator-token-not-production-0001'
HEADERS={'Authorization':'Bearer '+TOKEN}


def configured(settings,accounts=None,actions=None):
    return Settings(database_url=settings.database_url,paper_operator_token=TOKEN,
                    paper_operator_accounts=accounts or ['account'],paper_operator_actions=actions or ['PAUSE','HALT','STOP','RESUME'])


def payload(**updates):
    return {'account_id':'account','command_id':'pause','expected_control_revision':2,'expected_financial_revision':0,
            'action':'PAUSE','created_at':STAMP.isoformat(),**updates}


def test_default_disabled_and_denied_credentials_never_access_database(monkeypatch):
    settings=Settings(database_url='postgresql+psycopg://invalid:invalid@127.0.0.1:1/no_db')
    def forbidden(*args,**kwargs):raise AssertionError('Denied command touched storage')
    monkeypatch.setattr(controls,'command',forbidden)
    with TestClient(create_app(settings)) as client:
        response=client.post(PATH,json=payload(),headers=HEADERS)
        assert response.status_code==503 and response.headers['cache-control']=='no-store'
    with TestClient(create_app(configured(settings,actions=['STOP']))) as client:
        for headers in [{},{'Authorization':'Basic '+TOKEN},{'Authorization':'Bearer wrong'}]:
            response=client.post(PATH,json=payload(),headers=headers)
            assert response.status_code==401 and response.headers['www-authenticate']=='Bearer'
        for value in [payload(account_id='other'),payload(action='RESUME')]:
            response=client.post(PATH,json=value,headers=HEADERS)
            assert response.status_code==403 and response.headers['cache-control']=='no-store'
        assert client.post(PATH+'?token='+TOKEN,json=payload(action='STOP')).status_code==401
        for value in [payload(expected_control_revision=True),payload(expected_financial_revision='0'),payload(extra='ignored'),payload(action='SUBMIT')]:
            assert client.post(PATH,json=value,headers=HEADERS).status_code==422


@pytest.mark.parametrize('updates',[
 {'paper_operator_token':TOKEN}, {'paper_operator_accounts':['account']}, {'paper_operator_actions':['STOP']},
 {'paper_operator_token':'short','paper_operator_accounts':['account'],'paper_operator_actions':['STOP']},
 {'paper_operator_token':TOKEN,'paper_operator_accounts':['account','account'],'paper_operator_actions':['STOP']},
 {'paper_operator_token':TOKEN,'paper_operator_accounts':['account'],'paper_operator_actions':['STOP','STOP']},
])
def test_incomplete_or_invalid_server_grants_fail_configuration(updates):
    with pytest.raises(ValidationError) as caught:Settings(database_url='postgresql+psycopg://fixture',**updates)
    assert TOKEN not in str(caught.value)


def test_atomic_control_ack_and_exact_retry_after_later_work(database):
    engine,_,settings=database;setup(engine)
    with TestClient(create_app(configured(settings))) as client:
        one=client.post(PATH,json=payload(),headers=HEADERS)
        assert one.status_code==200 and one.headers['cache-control']=='no-store'
        accepted=one.json()['accepted_command']
        assert accepted['journal_revision']==0 and accepted['to_state']=='PAUSED'
        assert one.json()['controls']['revision']==3 and not one.json()['external_submission_allowed']
        command(engine,'resume-again','RESUME');journal.prepare(engine,request())
        retry=client.post(PATH,json=payload(),headers=HEADERS)
        assert retry.status_code==200 and retry.json()['accepted_command']==accepted
        assert retry.json()['controls']['state']=='ACTIVE'
        before=controls.capture(engine,'account')
        for value in [payload(expected_financial_revision=1),payload(expected_control_revision=3),payload(action='STOP')]:
            assert client.post(PATH,json=value,headers=HEADERS).status_code==409
        assert controls.capture(engine,'account')==before
        assert journal.read(engine,'account')['active_request_id'] is not None
        assert TOKEN not in str(one.json()) and TOKEN not in str(retry.json())


def test_stale_financial_and_control_revision_denied_without_effect(database):
    engine,_,settings=database;setup(engine);journal.prepare(engine,request())
    before=controls.capture(engine,'account')
    with TestClient(create_app(configured(settings))) as client:
        for value in [payload(),payload(expected_control_revision=1,expected_financial_revision=1),payload(created_at='invalid',expected_financial_revision=1)]:
            assert client.post(PATH,json=value,headers=HEADERS).status_code==409
        assert controls.capture(engine,'account')==before
        response=client.post(PATH,json=payload(expected_financial_revision=1),headers=HEADERS)
        assert response.status_code==200 and response.json()['accepted_command']['journal_revision']==1
        assert journal.read(engine,'account')==before['journal']['journal']


def test_two_concurrent_control_commands_have_one_revision_winner(database):
    engine,_,settings=database;setup(engine);barrier=Barrier(2)
    def attempt(identity):
        with TestClient(create_app(configured(settings))) as client:
            barrier.wait(timeout=5)
            return client.post(PATH,json=payload(command_id=identity),headers=HEADERS).status_code
    with ThreadPoolExecutor(2) as pool:results=list(pool.map(attempt,['one','two']))
    assert sorted(results)==[200,409]
    assert controls.verify(controls.capture(engine,'account'))['revision']==3
    assert journal.read(engine,'account')['revision']==0


def test_control_vs_prepare_race_checks_financial_revision_inside_account_lock(database):
    engine,_,settings=database;setup(engine);barrier=Barrier(2)
    def pause():
        with TestClient(create_app(configured(settings))) as client:
            barrier.wait(timeout=5);return client.post(PATH,json=payload(),headers=HEADERS).status_code
    def prepare():
        barrier.wait(timeout=5)
        try:journal.prepare(engine,request());return True
        except ValueError:return False
    with ThreadPoolExecutor(2) as pool:
        one=pool.submit(pause);two=pool.submit(prepare);results=(one.result(),two.result())
    assert results in [(200,False),(409,True)]
    controls.verify(controls.capture(engine,'account'))


def test_granted_errors_are_generic_and_commands_require_enrollment(database):
    engine,_,settings=database
    from tests.test_requested_execution import BASE
    journal.create(engine,'account','fixture:spot',BASE,STAMP.isoformat())
    with TestClient(create_app(configured(settings,accounts=['account','missing']))) as client:
        assert client.post(PATH,json=payload(),headers=HEADERS).status_code==409
        assert client.post(PATH,json=payload(account_id='missing'),headers=HEADERS).status_code==404
    unavailable=Settings(database_url='postgresql+psycopg://invalid:invalid@127.0.0.1:1/no_db')
    with TestClient(create_app(configured(unavailable))) as client:
        response=client.post(PATH,json=payload(),headers=HEADERS)
        assert response.status_code==503 and set(response.json())=={'detail'}
        assert response.headers['cache-control']=='no-store'


def test_fenced_command_account_lock_timeout_returns_retryable_error(database):
    from sqlalchemy import text
    engine,_,settings=database;setup(engine);before=controls.capture(engine,'account')
    with engine.begin() as conn:
        conn.execute(text("SELECT account_id FROM requested_paper_accounts WHERE account_id='account' FOR UPDATE"))
        with TestClient(create_app(configured(settings))) as client:
            response=client.post(PATH,json=payload(),headers=HEADERS)
            assert response.status_code==503 and response.headers['cache-control']=='no-store'
            assert response.json()=={'detail':'Explicit Paper command temporarily unavailable'}
    assert controls.capture(engine,'account')==before
