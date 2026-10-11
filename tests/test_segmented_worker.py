from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
import time
import pytest
from sqlalchemy import text, event as sql_event
from sqlalchemy.exc import DBAPIError
from alembic import command as migration
from core.paper import segmented_ledger as ledger, segmented_worker as worker
from core.paper import segmented_ledger_archive as archive
from tests.test_integration import database
from tests.test_segmented_ledger import ID, opening, command
from tests.test_requested_execution import request, event, fill


def setup(engine, ttl=30):
    root = ledger.create(engine, opening())
    enrollment = worker.enroll(engine, ID, 'enroll')
    claim = worker.claim(engine, ID, 'claim', 'worker-a', expected_sha256=root['node_sha256'], expected_token=0, ttl_seconds=ttl)
    return root, enrollment, claim


def prepare(root):
    req = request(account_id=ID)
    return req, command(root, 'prepare', 'PREPARE', req)


def test_bound_entry_rejects_bypass_and_retry_is_atomic(database):
    engine, _, _ = database; root, enrolled, claim = setup(engine); req, cmd = prepare(root)
    with pytest.raises(ValueError, match='current worker lease'): ledger.execute(engine, ID, cmd)
    first = worker.execute(engine, ID, 'worker-a', 1, cmd)
    assert worker.execute(engine, ID, 'worker-a', 1, cmd) == first
    current = worker.execute(engine, ID, 'worker-a', 1, command(first, 'batch', 'APPEND',
        {'request_id': req['request_id'], 'batch': [event(req, 0, 'SUBMIT'), fill(req, 1)]}))
    assert worker.execute(engine, ID, 'worker-a', 1, cmd) == first
    assert ledger.read(engine, ID) == current
    assert worker.enroll(engine, ID, 'enroll') == enrolled
    assert worker.claim(engine, ID, 'claim', 'worker-a', expected_sha256=root['node_sha256'], expected_token=0) == claim
    assert archive.capture(engine, ID)['commits'][-1]['report'] == current['journal']
    with engine.connect() as db:
        assert db.scalar(text("SELECT count(*) FROM segmented_paper_worker_records WHERE kind='ACCEPT'")) == 2
        assert db.scalar(text('SELECT count(*) FROM requested_paper_accounts')) == 0


@pytest.mark.parametrize('owner,token', [('other', 1), ('worker-a', 0), ('worker-a', True), ('worker-a', '1')])
def test_wrong_owner_or_token_cannot_write(database, owner, token):
    engine, _, _ = database; root, _, _ = setup(engine); _, cmd = prepare(root)
    with pytest.raises(ValueError): worker.execute(engine, ID, owner, token, cmd)
    assert ledger.read(engine, ID) == root


def test_real_expiry_takeover_fences_old_owner_and_preserves_hold(database):
    engine, _, _ = database; root, _, claim = setup(engine, ttl=1); req, cmd = prepare(root)
    before = worker.execute(engine, ID, 'worker-a', 1, cmd)
    time.sleep(1.05)
    with pytest.raises(ValueError): worker.execute(engine, ID, 'worker-a', 1, cmd)
    with pytest.raises(ValueError): worker.claim(engine, ID, 'claim', 'worker-a', expected_sha256=root['node_sha256'], expected_token=0, ttl_seconds=1)
    new = worker.claim(engine, ID, 'takeover', 'worker-b', expected_sha256=before['node_sha256'], expected_token=1)
    assert new['payload']['token'] == 2
    with pytest.raises(ValueError): worker.execute(engine, ID, 'worker-a', 1, cmd)
    assert worker.execute(engine, ID, 'worker-b', 2, cmd) == before
    current = worker.execute(engine, ID, 'worker-b', 2, command(before, 'fill', 'APPEND',
        {'request_id': req['request_id'], 'batch': [event(req, 0, 'SUBMIT'), fill(req, 1)]}))
    assert current['journal']['revision'] == 3


def test_concurrent_claim_and_identical_write_have_one_effect(database):
    engine, _, _ = database; root = ledger.create(engine, opening()); worker.enroll(engine, ID, 'enroll')
    def acquire(owner):
        try:
            return worker.claim(engine, ID, owner, owner, expected_sha256=root['node_sha256'], expected_token=0)
        except ValueError: return None
    with ThreadPoolExecutor(2) as pool: results = list(pool.map(acquire, ['a', 'b']))
    winners = [value for value in results if value is not None]; assert len(winners) == 1
    _, cmd = prepare(root); owner = winners[0]['payload']['owner']
    with ThreadPoolExecutor(2) as pool: writes = list(pool.map(lambda _: worker.execute(engine, ID, owner, 1, cmd), range(2)))
    assert writes[0] == writes[1] and writes[0]['journal']['revision'] == 1
    with engine.connect() as db:
        assert db.scalar(text("SELECT count(*) FROM segmented_paper_worker_records WHERE kind='ACCEPT'")) == 1


def test_receipt_failure_rolls_back_financial_effect(database):
    engine, _, _ = database; root, _, _ = setup(engine); _, cmd = prepare(root)
    def fail(conn, cursor, statement, parameters, context, many):
        if statement.startswith('INSERT INTO segmented_paper_worker_records'): raise RuntimeError('Injected receipt failure')
    sql_event.listen(engine, 'after_cursor_execute', fail)
    try:
        with pytest.raises(RuntimeError): worker.execute(engine, ID, 'worker-a', 1, cmd)
    finally: sql_event.remove(engine, 'after_cursor_execute', fail)
    assert ledger.read(engine, ID) == root
    assert worker.execute(engine, ID, 'worker-a', 1, cmd)['journal']['revision'] == 1


def test_expiry_during_replay_rolls_back_financial_effect(database, monkeypatch):
    engine, _, _ = database; root, _, claim = setup(engine); _, cmd = prepare(root)
    times = iter([claim['recorded_us'] + 1, claim['payload']['expires_us']])
    monkeypatch.setattr(worker, 'clock', lambda _: next(times))
    with pytest.raises(ValueError, match='during settlement'): worker.execute(engine, ID, 'worker-a', 1, cmd)
    assert ledger.read(engine, ID) == root


def test_stale_acquisition_and_conflicting_claim_retry(database):
    engine, _, _ = database; root, _, _ = setup(engine)
    for changes in [dict(owner='other'), dict(ttl_seconds=29), dict(expected_sha256='0' * 64)]:
        args = dict(command_id='claim', owner='worker-a', expected_sha256=root['node_sha256'], expected_token=0)
        args.update(changes)
        with pytest.raises(ValueError): worker.claim(engine, ID, **args)
    with pytest.raises(ValueError): worker.claim(engine, ID, 'new', 'worker-b', expected_sha256=root['node_sha256'], expected_token=1)


def test_immutable_records_and_populated_downgrade_refusal(database):
    engine, config, _ = database; setup(engine)
    for statement in ['DELETE FROM segmented_paper_worker_records', 'UPDATE segmented_paper_worker_records SET kind=kind']:
        with pytest.raises(DBAPIError):
            with engine.begin() as db: db.execute(text(statement))
    with pytest.raises(ValueError, match='populated worker'): migration.downgrade(config, '0027')


def test_missing_admission_rejects_read_write_and_archive(database):
    engine, _, _ = database; root, _, _ = setup(engine); _, cmd = prepare(root)
    worker.execute(engine, ID, 'worker-a', 1, cmd)
    with engine.begin() as db:
        db.execute(text('ALTER TABLE segmented_paper_worker_records DISABLE TRIGGER USER'))
        db.execute(text("DELETE FROM segmented_paper_worker_records WHERE kind='ACCEPT'"))
        db.execute(text('ALTER TABLE segmented_paper_worker_records ENABLE TRIGGER USER'))
    for operation in [lambda: ledger.read(engine, ID), lambda: worker.execute(engine, ID, 'worker-a', 1, cmd), lambda: archive.capture(engine, ID)]:
        with pytest.raises(ValueError): operation()


def test_enrollment_is_explicit_and_never_retrospective(database):
    engine, _, _ = database; root = ledger.create(engine, opening()); _, cmd = prepare(root)
    with pytest.raises(ValueError): worker.claim(engine, ID, 'claim', 'owner', expected_sha256=root['node_sha256'], expected_token=0)
    with pytest.raises(ValueError): worker.execute(engine, ID, 'owner', 1, cmd)
    ledger.execute(engine, ID, cmd)
    with pytest.raises(ValueError, match='retrospectively'): worker.enroll(engine, ID, 'enroll')


def test_worker_capacity_keeps_prior_commit_and_exact_retry(database, monkeypatch):
    engine, _, _ = database; root, _, _ = setup(engine); req, cmd = prepare(root)
    before = worker.execute(engine, ID, 'worker-a', 1, cmd)
    monkeypatch.setattr(worker, 'RECORDS', 3)
    assert worker.execute(engine, ID, 'worker-a', 1, cmd) == before
    with pytest.raises(ValueError): worker.execute(engine, ID, 'worker-a', 1, command(before, 'batch', 'APPEND',
        {'request_id': req['request_id'], 'batch': [event(req, 0, 'SUBMIT')]}))
    assert ledger.read(engine, ID) == before


def test_missing_entire_binding_chain_cannot_reenable_unmanaged_writes(database):
    engine, _, _ = database; root, _, _ = setup(engine); _, cmd = prepare(root)
    with engine.begin() as db:
        db.execute(text('ALTER TABLE segmented_paper_worker_records DISABLE TRIGGER USER'))
        db.execute(text('DELETE FROM segmented_paper_worker_records'))
        db.execute(text('ALTER TABLE segmented_paper_worker_records ENABLE TRIGGER USER'))
    with pytest.raises(ValueError, match='Missing'): ledger.read(engine, ID)
    with pytest.raises(ValueError): ledger.execute(engine, ID, cmd)
    with pytest.raises(ValueError): worker.enroll(engine, ID, 'enroll')
    with pytest.raises(DBAPIError):
        with engine.begin() as db: db.execute(text('UPDATE segmented_paper_ledgers SET worker_version=NULL'))


@pytest.mark.parametrize('phase', ['before', 'after'])
def test_real_process_kill_keeps_financial_commit_and_admission_atomic(database, tmp_path, phase):
    import json, selectors, signal, subprocess, sys
    engine, _, _ = database; root, _, _ = setup(engine); _, cmd = prepare(root)
    path = tmp_path / 'command.json'; path.write_text(json.dumps(cmd))
    code = '''import json,os,signal,sys
from sqlalchemy import create_engine,event
from sqlalchemy.orm import Session
from apps.api.settings import Settings
from core.paper.segmented_worker import execute
engine=create_engine(Settings().database_url.get_secret_value(),hide_parameters=True)
def pause(conn,cursor,statement,parameters,context,many):
 if statement.startswith('INSERT INTO segmented_paper_worker_records'):
  print('boundary',flush=True);os.kill(os.getpid(),signal.SIGSTOP)
def committed(session):
 print('boundary',flush=True);os.kill(os.getpid(),signal.SIGSTOP)
if sys.argv[2]=='before':event.listen(engine,'after_cursor_execute',pause)
else:event.listen(Session,'after_commit',committed)
execute(engine,'segmented:account','worker-a',1,json.load(open(sys.argv[1])))
'''
    process = subprocess.Popen([sys.executable, '-c', code, str(path), phase], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    try:
        with selectors.DefaultSelector() as selector:
            selector.register(process.stdout, selectors.EVENT_READ)
            assert selector.select(timeout=10)
        assert process.stdout.readline().strip() == b'boundary'
        process.kill(); process.wait(timeout=5)
        assert process.returncode == -signal.SIGKILL
    finally:
        if process.poll() is None: process.kill(); process.wait(timeout=5)
    assert ledger.read(engine, ID)['journal']['revision'] == (0 if phase == 'before' else 1)
    after = worker.execute(engine, ID, 'worker-a', 1, cmd)
    assert after['journal']['revision'] == 1
    assert worker.execute(engine, ID, 'worker-a', 1, cmd) == after
    with engine.connect() as db:
        assert db.scalar(text("SELECT count(*) FROM segmented_paper_worker_records WHERE kind='ACCEPT'")) == 1


def test_populated_restore_preserves_worker_fences_and_admission(database, tmp_path):
    from scripts.backup_drill import drill
    engine, _, _ = database; root, _, _ = setup(engine); _, cmd = prepare(root)
    after = worker.execute(engine, ID, 'worker-a', 1, cmd)
    with engine.connect() as db:
        expected = db.execute(text('SELECT payload_sha256 FROM segmented_paper_worker_records ORDER BY sequence')).scalars().all()
    def verify(restored):
        assert ledger.read(restored, ID) == after
        with restored.connect() as db:
            assert db.execute(text('SELECT payload_sha256 FROM segmented_paper_worker_records ORDER BY sequence')).scalars().all() == expected
        with pytest.raises(ValueError): ledger.execute(restored, ID, cmd)
        return {'worker_fences_equal': True, 'worker_records': len(expected), 'financial_revision': 1}
    result = drill(tmp_path / 'restore', verify_restored=verify)
    assert result['additional_verification']['worker_fences_equal']
    assert ledger.read(engine, ID) == after
