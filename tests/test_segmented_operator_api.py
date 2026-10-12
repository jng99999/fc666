from copy import deepcopy
import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError
from sqlalchemy import text
from sqlalchemy.exc import OperationalError
from apps.api.main import create_app
from apps.api.settings import Settings
from core.paper import segmented_ledger as ledger, segmented_worker as worker
from core.paper import segmented_operational_archive as operational
from tests.test_integration import database
from tests.test_segmented_ledger import ID, opening, command
from tests.test_segmented_worker import prepare

TOKEN = 'isolated_test_only_operator_token_123456789'
AUTH = {'Authorization': 'Bearer ' + TOKEN}
READ = 'READ_SEGMENTED_EVIDENCE'
ENROLL = 'ENROLL_SEGMENTED_WORKER'


def config(settings, actions=None, accounts=None):
    return Settings(database_url=settings.database_url, redis_url=settings.redis_url,
                    paper_operator_token=TOKEN, paper_operator_accounts=accounts or [ID],
                    paper_operator_actions=actions or [READ, ENROLL])


def payload(root):
    return dict(ledger_id=ID, command_id='enroll', expected_root_sha256=root['node_sha256'])


def test_default_access_is_disabled_before_database_lookup(database):
    _, _, settings = database
    with TestClient(create_app(settings)) as client:
        response = client.get('/api/v1/paper-segmented/evidence', params={'ledger_id': ID})
        assert response.status_code == 503 and response.headers['cache-control'] == 'no-store'
        assert client.post('/api/v1/paper-segmented/worker-enrollments', json=payload({'node_sha256': '0'*64})).status_code == 503


@pytest.mark.parametrize('headers', [{}, {'Authorization': 'Bearer wrong'}, {'Authorization': 'Basic '+TOKEN}])
def test_unauthenticated_access_is_denied(database, headers):
    _, _, settings = database
    with TestClient(create_app(config(settings))) as client:
        response = client.get('/api/v1/paper-segmented/evidence', params={'ledger_id': ID}, headers=headers)
        assert response.status_code == 401 and response.headers['www-authenticate'] == 'Bearer'
        assert TOKEN not in response.text
        assert client.post('/api/v1/paper-segmented/worker-enrollments', headers=headers, json=payload({'node_sha256': '0'*64})).status_code == 401


def test_exact_account_and_action_grants_precede_ledger_lookup(database):
    _, _, settings = database
    with TestClient(create_app(config(settings, accounts=['segmented:other']))) as client:
        assert client.get('/api/v1/paper-segmented/evidence', params={'ledger_id': ID}, headers=AUTH).status_code == 403
        assert client.post('/api/v1/paper-segmented/worker-enrollments', json=payload({'node_sha256':'0'*64}), headers=AUTH).status_code == 403
    with TestClient(create_app(config(settings, actions=[READ]))) as client:
        assert client.post('/api/v1/paper-segmented/worker-enrollments', json=payload({'node_sha256':'0'*64}), headers=AUTH).status_code == 403
    with TestClient(create_app(config(settings, actions=[ENROLL]))) as client:
        assert client.get('/api/v1/paper-segmented/evidence', params={'ledger_id': ID}, headers=AUTH).status_code == 403


def test_authorized_enrollment_and_complete_evidence_preserve_finances(database):
    engine, _, settings = database; root = ledger.create(engine, opening())
    with TestClient(create_app(config(settings))) as client:
        response = client.get('/api/v1/paper-segmented/evidence', params={'ledger_id':ID}, headers=AUTH)
        assert response.status_code == 200 and response.json()['commits'][0]['report'] == root['journal']
        before = client.get('/api/v1/paper-segmented/evidence', params={'ledger_id':ID,'scope':'operational'}, headers=AUTH)
        assert before.status_code == 409
        accepted = client.post('/api/v1/paper-segmented/worker-enrollments', json=payload(root), headers=AUTH)
        assert accepted.status_code == 200 and accepted.headers['cache-control'] == 'no-store'
        assert accepted.json()['external_submission_allowed'] is False
        assert client.post('/api/v1/paper-segmented/worker-enrollments', json=payload(root), headers=AUTH).json() == accepted.json()
        response = client.get('/api/v1/paper-segmented/evidence', params={'ledger_id':ID,'scope':'operational'}, headers=AUTH)
        assert response.status_code == 200 and response.headers['cache-control'] == 'no-store'
        assert operational.verify(response.json()) == operational.capture(engine,ID)
        assert TOKEN not in response.text and TOKEN not in accepted.text
    assert ledger.read(engine,ID) == root
    with engine.connect() as db:
        assert db.scalar(text('SELECT count(*) FROM segmented_paper_commits')) == 1
        assert db.scalar(text('SELECT count(*) FROM segmented_paper_worker_records')) == 1


def test_missing_account_and_database_failure_are_sanitized(database,monkeypatch):
    _, _, settings = database
    with TestClient(create_app(config(settings))) as client:
        assert client.get('/api/v1/paper-segmented/evidence',params={'ledger_id':ID},headers=AUTH).status_code == 404
        from core.paper import segmented_ledger_archive
        def fail(*args): raise OperationalError('sensitive statement', {}, Exception(TOKEN))
        monkeypatch.setattr(segmented_ledger_archive,'capture',fail)
        result=client.get('/api/v1/paper-segmented/evidence',params={'ledger_id':ID},headers=AUTH)
        assert result.status_code == 503 and TOKEN not in result.text and 'sensitive' not in result.text


@pytest.mark.parametrize('change',[{'expected_root_sha256':'0'*64},{'command_id':'conflict'}])
def test_conflicting_enrollment_is_rejected_without_new_record(database,change):
    engine, _, settings = database; root=ledger.create(engine,opening());worker.enroll(engine,ID,'enroll')
    value={**payload(root),**change}
    with TestClient(create_app(config(settings))) as client:
        assert client.post('/api/v1/paper-segmented/worker-enrollments',json=value,headers=AUTH).status_code == 409
    assert ledger.read(engine,ID) == root
    with engine.connect() as db: assert db.scalar(text('SELECT count(*) FROM segmented_paper_worker_records')) == 1


def test_retrospective_enrollment_is_rejected(database):
    engine, _, settings = database; root=ledger.create(engine,opening());_,cmd=prepare(root)
    after=ledger.execute(engine,ID,cmd)
    with TestClient(create_app(config(settings))) as client:
        assert client.post('/api/v1/paper-segmented/worker-enrollments',json=payload(root),headers=AUTH).status_code == 409
    assert ledger.read(engine,ID) == after


@pytest.mark.parametrize('change',[{'expected_root_sha256':True},{'extra':'forbidden'},{'ledger_id':'legacy'}, {'command_id':123}])
def test_enrollment_requires_exact_strict_input(database,change):
    engine, _, settings=database;root=ledger.create(engine,opening())
    with TestClient(create_app(config(settings))) as client:
        assert client.post('/api/v1/paper-segmented/worker-enrollments',json={**payload(root),**change},headers=AUTH).status_code == 422
    assert ledger.read(engine,ID) == root


def test_read_permission_never_creates_or_grants_a_worker_lease(database):
    engine, _, settings=database;root=ledger.create(engine,opening());worker.enroll(engine,ID,'enroll')
    with TestClient(create_app(config(settings,actions=[READ]))) as client:
        assert client.get('/api/v1/paper-segmented/evidence',params={'ledger_id':ID,'scope':'operational'},headers=AUTH).status_code == 200
        for path in ['/api/v1/paper-segmented/claims','/api/v1/paper-segmented/commands','/api/v1/paper-segmented/ledgers']:
            assert client.post(path,json={},headers=AUTH).status_code == 404
    with engine.connect() as db: assert db.scalar(text("SELECT count(*) FROM segmented_paper_worker_records WHERE kind='CLAIM'")) == 0


def test_settings_never_accept_incomplete_operator_configuration():
    for values in [{'paper_operator_actions':[READ]}, {'paper_operator_token':TOKEN,'paper_operator_accounts':[ID]},
                   {'paper_operator_token':TOKEN,'paper_operator_accounts':[ID],'paper_operator_actions':[READ,READ]}]:
        with pytest.raises(ValidationError): Settings(database_url='postgresql://fixture',**values)
