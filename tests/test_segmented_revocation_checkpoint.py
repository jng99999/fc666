from copy import deepcopy
import pytest
from sqlalchemy import text,event as sql_event
from sqlalchemy.orm import Session
from sqlalchemy.exc import DBAPIError
from alembic import command as migration
from fastapi.testclient import TestClient
from core.paper import segmented_revocations as revocations
from core.paper.fault_adapter import sha
from core.storage.models import SegmentedOperatorRevocationRecord as Record,SegmentedOperatorRevocationHead as Head
from apps.api.main import create_app
from tests.test_integration import database
from tests.test_segmented_operator_api import TOKEN,ID,READ
from tests.test_requested_commands import configured,HEADERS,PATH,payload,TOKEN as LEGACY_TOKEN

FRESH='unrevoked_test_only_operator_token_123456789'


@pytest.mark.parametrize('loss',['tail','all','checkpoint','head_hash'])
def test_missing_retained_evidence_or_checkpoint_cannot_restore_access(database,loss):
    engine,_,base=database
    revocations.revoke(engine,TOKEN,'one',ledger_id=ID,action=READ)
    revocations.revoke(engine,TOKEN,'two')
    with revocations.guard(engine,FRESH,ID,READ):pass
    with engine.begin() as db:
        if loss in ['tail','all']:
            db.execute(text('ALTER TABLE segmented_operator_revocations DISABLE TRIGGER USER'))
            db.execute(text('DELETE FROM segmented_operator_revocations'+(' WHERE sequence=1' if loss=='tail' else '')))
            db.execute(text('ALTER TABLE segmented_operator_revocations ENABLE TRIGGER USER'))
        else:
            db.execute(text('ALTER TABLE segmented_operator_revocation_head DISABLE TRIGGER USER'))
            if loss=='checkpoint':db.execute(text('DELETE FROM segmented_operator_revocation_head'))
            else:db.execute(text("UPDATE segmented_operator_revocation_head SET head_sha256=:value"),{'value':'0'*64})
            db.execute(text('ALTER TABLE segmented_operator_revocation_head ENABLE TRIGGER USER'))
    with pytest.raises(ValueError,match='checkpoint'):
        with revocations.guard(engine,FRESH,ID,READ):pass
    with pytest.raises(ValueError,match='checkpoint'):revocations.revoke(engine,TOKEN,'two')
    with TestClient(create_app(configured(base))) as client:
        response=client.post(PATH,json=payload(),headers=HEADERS)
        assert response.status_code==409 and LEGACY_TOKEN not in response.text
        if loss=='checkpoint':assert client.get('/health/ready').status_code==503


def test_checkpoint_update_failure_rolls_back_new_record_and_head(database):
    engine,_,_=database;original=revocations.revoke(engine,TOKEN,'one')
    def fail(conn,cursor,statement,parameters,context,many):
        if statement.startswith('UPDATE segmented_operator_revocation_head'):raise RuntimeError('Injected checkpoint failure')
    sql_event.listen(engine,'after_cursor_execute',fail)
    try:
        with pytest.raises(RuntimeError):revocations.revoke(engine,TOKEN,'two',ledger_id=ID,action=READ)
    finally:sql_event.remove(engine,'after_cursor_execute',fail)
    with Session(engine) as db:
        assert db.get(Head,'global').revision==1
        assert db.get(Head,'global').head_sha256==original['sha256']
        assert len(list(db.query(Record)))==1
    second=revocations.revoke(engine,TOKEN,'two',ledger_id=ID,action=READ)
    with Session(engine) as db:
        assert db.get(Head,'global').revision==2 and db.get(Head,'global').root_sha256==original['sha256']
        assert db.get(Head,'global').head_sha256==second['sha256']


def test_checkpoint_is_nonremovable_monotonic_and_populated_downgrade_refuses(database):
    engine,configuration,_=database;revocations.revoke(engine,TOKEN,'one')
    for statement in ['DELETE FROM segmented_operator_revocation_head','UPDATE segmented_operator_revocation_head SET revision=0,root_sha256=NULL,head_sha256=NULL','UPDATE segmented_operator_revocation_head SET revision=revision+2']:
        with pytest.raises(DBAPIError):
            with engine.begin() as db:db.execute(text(statement))
    with pytest.raises(ValueError,match='populated'):migration.downgrade(configuration,'0029')


def seed_legacy(engine,corrupt=False):
    body=dict(version=revocations.VERSION,sequence=0,parent_sha256=None,command_id='legacy',subject_sha256=revocations.subject(TOKEN),
              ledger_id='*',action='*',recorded_us=1)
    value={**body,'sha256':sha(body)}
    with Session(engine) as db,db.begin():
        db.add(Record(sequence=0,command_id='legacy',subject_sha256=value['subject_sha256'],ledger_id='*',action='*',
                      payload=value,payload_sha256='0'*64 if corrupt else value['sha256']))
    return value


def test_populated_0029_upgrade_anchors_exact_evidence_without_rewriting(database):
    engine,configuration,_=database;migration.downgrade(configuration,'0029');before=seed_legacy(engine)
    migration.upgrade(configuration,'head')
    with Session(engine) as db:
        head=db.get(Head,'global');assert head.revision==1 and head.root_sha256==before['sha256'] and head.head_sha256==before['sha256']
        assert db.get(Record,0).payload==before
    assert revocations.revoke(engine,TOKEN,'legacy')==before
    with pytest.raises(revocations.Revoked):
        with revocations.guard(engine,TOKEN,ID,READ):pass


def test_corrupt_0029_upgrade_refuses_to_create_a_trusted_checkpoint(database):
    engine,configuration,_=database;migration.downgrade(configuration,'0029');seed_legacy(engine,True)
    with pytest.raises(ValueError,match='migration'):migration.upgrade(configuration,'head')
    with engine.connect() as db:
        assert db.scalar(text('SELECT version_num FROM alembic_version'))=='0029'
        assert db.scalar(text("SELECT to_regclass('public.segmented_operator_revocation_head')")) is None
        assert db.scalar(text('SELECT count(*) FROM segmented_operator_revocations'))==1
