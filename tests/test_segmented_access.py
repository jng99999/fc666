from concurrent.futures import ThreadPoolExecutor
from threading import Event
import pytest
from fastapi.testclient import TestClient
from apps.api.main import create_app
from apps.api.settings import Settings
from apps.api.segmented_access import Access, Denied
from core.paper import segmented_ledger as ledger
from tests.test_segmented_operator_api import TOKEN, AUTH, READ, ENROLL, ID, config, payload
from tests.test_segmented_ledger import opening
from tests.test_integration import database

NEW = 'rotated_test_only_operator_token_123456789'


def settings(token=TOKEN, actions=None):
    return Settings(database_url='postgresql://fixture', paper_operator_token=token,
                    paper_operator_accounts=[ID], paper_operator_actions=actions or [READ, ENROLL])


def test_rotation_revokes_old_token_and_preserves_revision():
    access = Access(settings())
    assert access.replace(settings(NEW), expected_revision=0) == 1
    with pytest.raises(Denied) as error: access.authenticate(TOKEN)
    assert error.value.status == 401
    access.authenticate(NEW)
    for revision in [0, True, '1']:
        with pytest.raises(ValueError): access.replace(settings(),expected_revision=revision)
    access.authenticate(NEW)


def test_policy_is_detached_and_invalid_replacement_preserves_current_access():
    value=settings(); access=Access(value)
    value.paper_operator_accounts.clear()
    with access.operation(TOKEN, ID, READ): pass
    with pytest.raises(ValueError): access.replace(value,expected_revision=0)
    with access.operation(TOKEN, ID, READ): pass
    assert TOKEN not in repr(access._policy)


def test_action_revocation_and_disable_are_immediate_for_next_admission():
    access=Access(settings());access.replace(settings(actions=[READ]),expected_revision=0)
    with pytest.raises(Denied) as error:
        with access.operation(TOKEN,ID,ENROLL): pass
    assert error.value.status==403
    access.replace(Settings(database_url='postgresql://fixture'),expected_revision=1)
    with pytest.raises(Denied) as error:access.authenticate(TOKEN)
    assert error.value.status==503


def test_replace_waits_for_admitted_operation_and_then_denies_old_token():
    access=Access(settings()); admitted=Event(); release=Event(); replacing=Event(); finished=Event()
    def operation():
        with access.operation(TOKEN,ID,READ):
            admitted.set();assert release.wait(5)
    def rotate():
        replacing.set();result=access.replace(settings(NEW),expected_revision=0);finished.set();return result
    with ThreadPoolExecutor(2) as pool:
        before=pool.submit(operation);assert admitted.wait(5)
        after=pool.submit(rotate);assert replacing.wait(5)
        assert not finished.wait(.05)
        release.set();before.result(timeout=5);assert after.result(timeout=5)==1
    with pytest.raises(Denied):
        with access.operation(TOKEN,ID,READ):pass


def test_same_thread_cannot_change_policy_inside_its_own_operation():
    access=Access(settings())
    with access.operation(TOKEN,ID,READ):
        with pytest.raises(ValueError):access.replace(settings(NEW),expected_revision=0)
    access.authenticate(TOKEN)
    assert access.replace(settings(NEW),expected_revision=0)==1


def test_failed_operation_releases_lock_and_allows_replacement():
    access=Access(settings())
    with pytest.raises(RuntimeError):
        with access.operation(TOKEN,ID,READ):raise RuntimeError('Injected fault')
    assert access.replace(settings(NEW),expected_revision=0)==1


def test_existing_http_client_observes_token_rotation_without_restart(database):
    engine,_,base=database; root=ledger.create(engine,opening());app=create_app(config(base))
    with TestClient(app) as client:
        assert client.get('/api/v1/paper-segmented/evidence',params={'ledger_id':ID},headers=AUTH).status_code==200
        changed=config(base);changed.paper_operator_token=NEW
        assert app.state.segmented_operator_access.replace(changed,expected_revision=0)==1
        assert client.get('/api/v1/paper-segmented/evidence',params={'ledger_id':ID},headers=AUTH).status_code==401
        assert client.post('/api/v1/paper-segmented/worker-enrollments',json=payload(root),headers=AUTH).status_code==401
        response=client.get('/api/v1/paper-segmented/evidence',params={'ledger_id':ID},headers={'Authorization':'Bearer '+NEW})
        assert response.status_code==200 and TOKEN not in response.text and NEW not in response.text
    assert ledger.read(engine,ID)==root


def test_current_permissions_rechecked_after_early_authentication(database):
    engine,_,base=database;root=ledger.create(engine,opening());app=create_app(config(base))
    with TestClient(app) as client:
        assert client.post('/api/v1/paper-segmented/worker-enrollments',json=payload(root),headers=AUTH).status_code==200
        app.state.segmented_operator_access.replace(config(base,actions=[READ]),expected_revision=0)
        assert client.post('/api/v1/paper-segmented/worker-enrollments',json=payload(root),headers=AUTH).status_code==403
        app.state.segmented_operator_access.replace(config(base,accounts=['segmented:other']),expected_revision=1)
        assert client.get('/api/v1/paper-segmented/evidence',params={'ledger_id':ID},headers=AUTH).status_code==403
        app.state.segmented_operator_access.replace(base,expected_revision=2)
        assert client.get('/api/v1/paper-segmented/evidence',params={'ledger_id':ID},headers=AUTH).status_code==503
    assert ledger.read(engine,ID)==root


def test_retained_secret_object_cannot_mutate_installed_credentials():
    value=settings(); access=Access(value)
    value.paper_operator_token._secret_value=NEW
    access.authenticate(TOKEN)
    with pytest.raises(Denied):access.authenticate(NEW)


def test_revocation_between_initial_authentication_and_enrollment_blocks_commit(database, monkeypatch):
    engine,_,base=database;root=ledger.create(engine,opening());app=create_app(config(base))
    access=app.state.segmented_operator_access;authenticated=Event();resume=Event()
    original=access.authenticate
    def pause(token):
        original(token);authenticated.set();assert resume.wait(5)
    monkeypatch.setattr(access,'authenticate',pause)
    with TestClient(app) as client, ThreadPoolExecutor(1) as pool:
        future=pool.submit(client.post,'/api/v1/paper-segmented/worker-enrollments',json=payload(root),headers=AUTH)
        try:
            assert authenticated.wait(5)
            access.replace(config(base,actions=[READ]),expected_revision=0)
        finally:resume.set()
        assert future.result(timeout=5).status_code==403
    from sqlalchemy import text
    with engine.connect() as db:
        assert db.scalar(text('SELECT count(*) FROM segmented_paper_worker_records'))==0
    assert ledger.read(engine,ID)==root
