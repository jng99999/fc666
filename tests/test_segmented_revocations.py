from concurrent.futures import ThreadPoolExecutor
from threading import Event
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from alembic import command as migration
from core.paper import segmented_revocations as revocations, segmented_ledger as ledger
from apps.api.main import create_app
from tests.test_integration import database
from tests.test_segmented_operator_api import TOKEN,AUTH,READ,ENROLL,ID,config,payload
from tests.test_segmented_ledger import opening


def test_revocation_survives_new_application_and_disposed_connections(database):
    engine,_,base=database;root=ledger.create(engine,opening());settings=config(base)
    first=create_app(settings)
    with TestClient(first) as client:
        assert client.get('/api/v1/paper-segmented/evidence',params={'ledger_id':ID},headers=AUTH).status_code==200
        result=first.state.segmented_operator_access.revoke('revoke-token')
        assert TOKEN not in str(result)
        assert client.get('/api/v1/paper-segmented/evidence',params={'ledger_id':ID},headers=AUTH).status_code==401
    engine.dispose()
    with TestClient(create_app(settings)) as client:
        assert client.get('/api/v1/paper-segmented/evidence',params={'ledger_id':ID},headers=AUTH).status_code==401
        assert client.post('/api/v1/paper-segmented/worker-enrollments',json=payload(root),headers=AUTH).status_code==401
    assert ledger.read(engine,ID)==root


def test_scoped_revocation_preserves_other_actions_and_other_tokens(database):
    engine,_,base=database;root=ledger.create(engine,opening());app=create_app(config(base))
    with TestClient(app) as client:
        app.state.segmented_operator_access.revoke('deny-enrollment',ledger_id=ID,action=ENROLL)
        assert client.get('/api/v1/paper-segmented/evidence',params={'ledger_id':ID},headers=AUTH).status_code==200
        assert client.post('/api/v1/paper-segmented/worker-enrollments',json=payload(root),headers=AUTH).status_code==403
    fresh=config(base);from pydantic import SecretStr
    fresh.paper_operator_token=SecretStr('separate_test_only_operator_token_123456789')
    with TestClient(create_app(fresh)) as client:
        assert client.post('/api/v1/paper-segmented/worker-enrollments',json=payload(root),headers={'Authorization':'Bearer '+fresh.paper_operator_token.get_secret_value()}).status_code==200


def test_exact_retry_conflicts_and_cap_preserve_existing_deny(database,monkeypatch):
    engine,_,_=database;original=revocations.revoke(engine,TOKEN,'deny')
    assert revocations.revoke(engine,TOKEN,'deny')==original
    for kwargs in [dict(command_id='changed'),dict(command_id='deny',action=READ)]:
        with pytest.raises(ValueError):revocations.revoke(engine,TOKEN,**kwargs)
    monkeypatch.setattr(revocations,'LIMIT',1)
    with pytest.raises(ValueError):revocations.revoke(engine,TOKEN,'new',ledger_id=ID,action=READ)
    assert revocations.revoke(engine,TOKEN,'deny')==original
    with pytest.raises(revocations.Revoked):
        with revocations.guard(engine,TOKEN,ID,READ):pass


def test_cross_engine_revoke_waits_for_earlier_admission(database):
    engine,_,_=database;admitted=Event();release=Event();started=Event();done=Event()
    from sqlalchemy import create_engine
    second=create_engine(engine.url,hide_parameters=True)
    def operation():
        with revocations.guard(engine,TOKEN,ID,READ):
            admitted.set();assert release.wait(5)
    def revoke():
        started.set();value=revocations.revoke(second,TOKEN,'deny');done.set();return value
    try:
        with ThreadPoolExecutor(2) as pool:
            before=pool.submit(operation);assert admitted.wait(5)
            after=pool.submit(revoke);assert started.wait(5)
            assert not done.wait(.05);release.set();before.result(timeout=5);after.result(timeout=5)
        with pytest.raises(revocations.Revoked):
            with revocations.guard(engine,TOKEN,ID,READ):pass
    finally:release.set();second.dispose()


def test_concurrent_original_retry_has_one_immutable_effect(database):
    engine,_,_=database
    with ThreadPoolExecutor(2) as pool:values=list(pool.map(lambda _:revocations.revoke(engine,TOKEN,'deny'),range(2)))
    assert values[0]==values[1]
    with engine.connect() as db:assert db.scalar(text('SELECT count(*) FROM segmented_operator_revocations'))==1


def test_immutable_rows_and_populated_downgrade_refuse_loss(database):
    engine,configuration,_=database;revocations.revoke(engine,TOKEN,'deny')
    for statement in ['DELETE FROM segmented_operator_revocations','UPDATE segmented_operator_revocations SET action=action']:
        with pytest.raises(DBAPIError):
            with engine.begin() as db:db.execute(text(statement))
    with pytest.raises(ValueError,match='populated'):migration.downgrade(configuration,'0028')


def test_corrupt_evidence_denies_without_leaking_internal_error(database):
    engine,_,base=database;ledger.create(engine,opening());revocations.revoke(engine,TOKEN,'deny',ledger_id=ID,action=ENROLL)
    with engine.begin() as db:
        db.execute(text('ALTER TABLE segmented_operator_revocations DISABLE TRIGGER USER'))
        db.execute(text("UPDATE segmented_operator_revocations SET command_id='corrupted'"))
        db.execute(text('ALTER TABLE segmented_operator_revocations ENABLE TRIGGER USER'))
    with TestClient(create_app(config(base))) as client:
        response=client.get('/api/v1/paper-segmented/evidence',params={'ledger_id':ID},headers=AUTH)
        assert response.status_code==409 and TOKEN not in response.text and 'corrupted' not in response.text


def test_backup_restore_preserves_durable_revocation(database,tmp_path):
    from scripts.backup_drill import drill
    engine,_,_=database;original=revocations.revoke(engine,TOKEN,'deny')
    def verify(restored):
        assert revocations.revoke(restored,TOKEN,'deny')==original
        with pytest.raises(revocations.Revoked):
            with revocations.guard(restored,TOKEN,ID,READ):pass
        return {'segmented_revocation_preserved':True}
    result=drill(tmp_path/'restore',verify_restored=verify)
    assert result['additional_verification']['segmented_revocation_preserved']


def test_database_unavailable_cannot_bypass_revocation_guard(database,monkeypatch):
    engine,_,base=database;ledger.create(engine,opening())
    def fail(*args,**kwargs):raise DBAPIError('sensitive',{},Exception(TOKEN))
    monkeypatch.setattr(revocations,'audit',fail)
    with TestClient(create_app(config(base))) as client:
        response=client.get('/api/v1/paper-segmented/evidence',params={'ledger_id':ID},headers=AUTH)
        assert response.status_code==503 and TOKEN not in response.text and 'sensitive' not in response.text


@pytest.mark.parametrize('phase',['before','after'])
def test_real_process_kill_revocation_commit_boundary(database,phase):
    import selectors,signal,subprocess,sys
    engine,_,_=database
    code='''import os,signal,sys
from sqlalchemy import create_engine,event
from sqlalchemy.orm import Session
from apps.api.settings import Settings
from core.paper import segmented_revocations
from tests.test_segmented_operator_api import TOKEN
engine=create_engine(Settings().database_url.get_secret_value(),hide_parameters=True)
def pause(conn,cursor,statement,parameters,context,many):
 if statement.startswith('INSERT INTO segmented_operator_revocations'):
  print('boundary',flush=True);os.kill(os.getpid(),signal.SIGSTOP)
def committed(session):
 print('boundary',flush=True);os.kill(os.getpid(),signal.SIGSTOP)
if sys.argv[1]=='before':event.listen(engine,'after_cursor_execute',pause)
else:event.listen(Session,'after_commit',committed)
segmented_revocations.revoke(engine,TOKEN,'deny')
'''
    process=subprocess.Popen([sys.executable,'-c',code,phase],stdout=subprocess.PIPE,stderr=subprocess.PIPE)
    try:
        with selectors.DefaultSelector() as selector:
            selector.register(process.stdout,selectors.EVENT_READ);assert selector.select(timeout=10)
        assert process.stdout.readline().strip()==b'boundary'
        process.kill();process.wait(timeout=5);assert process.returncode==-signal.SIGKILL
    finally:
        if process.poll() is None:process.kill();process.wait(timeout=5)
    if phase=='before':
        with revocations.guard(engine,TOKEN,ID,READ):pass
    else:
        with pytest.raises(revocations.Revoked):
            with revocations.guard(engine,TOKEN,ID,READ):pass
    committed=revocations.revoke(engine,TOKEN,'deny')
    assert revocations.revoke(engine,TOKEN,'deny')==committed
    with engine.connect() as db:assert db.scalar(text('SELECT count(*) FROM segmented_operator_revocations'))==1
