from copy import deepcopy
import json
import os
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
import pytest
from sqlalchemy import text
from core.paper import segmented_ledger as ledger, segmented_worker as worker
from core.paper import segmented_ledger_archive as financial, segmented_operational_archive as archive
from core.paper.fault_adapter import sha
from tests.test_segmented_ledger_archive import evidence, reseal
from tests.test_segmented_ledger import ID, command
from tests.test_segmented_worker import setup, prepare
from tests.test_requested_execution import request, event, fill
from tests.test_integration import database


def sample():
    transcript = evidence(); root = transcript['commits'][0]
    req = request(account_id=ID)
    first = command(ledger._view(root), 'prepare', 'PREPARE', req)
    prepared = ledger._node(ID, 1, root['sha256'], first, ledger._transition(root['report'], first, root['sha256']))
    second = command(ledger._view(prepared), 'fill', 'APPEND',
                     {'request_id': req['request_id'], 'batch': [event(req, 0, 'SUBMIT'), fill(req, 1)]})
    filled = ledger._node(ID, 2, prepared['sha256'], second, ledger._transition(prepared['report'], second, prepared['sha256']))
    transcript['commits'] += [prepared, filled]; transcript['head_sha256'] = filled['sha256']; reseal(transcript)
    records = []
    def record(kind, identity, index, now, payload):
        value = dict(version=worker.VERSION, ledger_id=ID, sequence=len(records),
                     parent_sha256=records[-1]['sha256'] if records else None,
                     kind=kind, command_id=identity, commit_index=index,
                     node_sha256=transcript['commits'][index]['sha256'], recorded_us=now, payload=payload)
        reseal(value); records.append(value)
    record('ENROLL', 'enroll', 0, 999999, deepcopy(worker.SCOPE))
    record('CLAIM', 'claim1', 0, 1000000, dict(owner='worker-a', token=1, ttl_seconds=1, expires_us=2000000))
    record('ACCEPT', 'prepare', 1, 1000010, dict(owner='worker-a', token=1))
    record('CLAIM', 'claim2', 1, 2000000, dict(owner='worker-b', token=2, ttl_seconds=30, expires_us=32000000))
    record('ACCEPT', 'fill', 2, 2000010, dict(owner='worker-b', token=2))
    value = dict(version=archive.VERSION, scope=archive.SCOPE, ledger_id=ID, financial=transcript,
                 worker_version=worker.VERSION, worker_records=records,
                 worker_root_sha256=records[0]['sha256'], worker_head_sha256=records[-1]['sha256'],
                 local_worker_evidence_included=True, operator_worker_binding_supported=False,
                 external_submission_allowed=False)
    reseal(value)
    return value


def reseal_worker_chain(value):
    parent = None
    for index, record in enumerate(value['worker_records']):
        record['sequence'] = index; record['parent_sha256'] = parent; reseal(record); parent = record['sha256']
    if value['worker_records']:
        value['worker_root_sha256'] = value['worker_records'][0]['sha256']
        value['worker_head_sha256'] = value['worker_records'][-1]['sha256']
    reseal(value)


def test_complete_offline_takeover_and_settlement():
    value = sample(); original = deepcopy(value)
    assert archive.verify(value) == original and value == original
    assert value['financial']['commits'][-1]['report']['revision'] == 3
    assert value['worker_records'][-1]['payload']['token'] == 2


@pytest.mark.parametrize('mutation', [
    'missing_enrollment', 'missing_claim', 'missing_admission', 'truncate', 'reorder', 'expired',
    'stale_token', 'token_gap', 'other_ledger', 'checkpoint', 'future_claim', 'authority',
    'strip_all', 'early_takeover', 'double_admission', 'duplicate_claim_id', 'financial_cash',
    'financial_missing', 'undeclared_scope', 'bool_timestamp', 'worker_endpoint',
])
def test_resealed_operational_forgery_is_rejected(mutation):
    value = sample(); records = value['worker_records']
    if mutation == 'missing_enrollment': del records[0]
    elif mutation == 'missing_claim': del records[1]
    elif mutation == 'missing_admission': del records[2]
    elif mutation == 'truncate': records.pop()
    elif mutation == 'reorder': records[1], records[2] = records[2], records[1]
    elif mutation == 'expired': records[2]['recorded_us'] = 2000000
    elif mutation == 'stale_token': records[-1]['payload']['token'] = 1
    elif mutation == 'token_gap': records[3]['payload']['token'] = 3; records[4]['payload']['token'] = 3
    elif mutation == 'other_ledger': records[-1]['ledger_id'] = 'segmented:other'
    elif mutation == 'checkpoint': records[3]['node_sha256'] = '0' * 64
    elif mutation == 'future_claim': records[3]['commit_index'] = 2; records[3]['node_sha256'] = value['financial']['head_sha256']
    elif mutation == 'authority': value['external_submission_allowed'] = True
    elif mutation == 'strip_all': value['worker_records'] = []; value['worker_version'] = None
    elif mutation == 'early_takeover': records[3]['recorded_us'] = 1999999; records[3]['payload']['expires_us'] = 31999999
    elif mutation == 'double_admission': records.append(deepcopy(records[-1]))
    elif mutation == 'duplicate_claim_id': records[3]['command_id'] = records[1]['command_id']
    elif mutation == 'financial_cash':
        node = value['financial']['commits'][-1]; node['report']['account']['cash'] = '999'; reseal(node)
        value['financial']['head_sha256'] = node['sha256']; reseal(value['financial'])
        records[-1]['node_sha256'] = node['sha256']
    elif mutation == 'financial_missing':
        value['financial']['commits'].pop(); value['financial']['head_sha256'] = value['financial']['commits'][-1]['sha256']; reseal(value['financial'])
    elif mutation == 'undeclared_scope': value['scope'] = 'FINANCIAL_ONLY'
    elif mutation == 'bool_timestamp': records[-1]['recorded_us'] = True
    elif mutation == 'worker_endpoint':
        reseal_worker_chain(value); value['worker_head_sha256'] = '0' * 64; reseal(value)
        with pytest.raises(ValueError): archive.verify(value)
        return
    reseal_worker_chain(value)
    with pytest.raises(ValueError): archive.verify(value)


@pytest.mark.parametrize('limit', ['archive_bytes', 'records', 'record_bytes'])
def test_operational_capacity_is_enforced(limit, monkeypatch):
    value = sample()
    if limit == 'archive_bytes': monkeypatch.setattr(archive, 'MAX_BYTES', 1)
    elif limit == 'records': monkeypatch.setattr(worker, 'RECORDS', 4)
    else: monkeypatch.setattr(worker, 'RECORD_BYTES', 1)
    with pytest.raises(ValueError): archive.verify(value)


def test_cli_verifies_offline_with_invalid_database_configuration_and_rejects_duplicate_json(tmp_path):
    from scripts.capture_segmented_ledger import write
    path = tmp_path / 'operational.json'; value = sample(); write(path, value)
    assert path.stat().st_mode & 0o777 == 0o600
    args = [sys.executable, '-m', 'scripts.verify_segmented_ledger', str(path)]
    env = {**os.environ, 'DATABASE_URL': 'invalid://offline-verification-must-not-connect'}
    result = subprocess.run(args, capture_output=True, text=True, env=env, timeout=10)
    assert result.returncode == 0
    output = json.loads(result.stdout)
    assert output['worker_records'] == 5 and output['verified'] is True and output['external_submission_allowed'] is False
    with pytest.raises(FileExistsError): write(path, value)
    link = tmp_path / 'link'; link.symlink_to(path)
    with pytest.raises(OSError): write(link, value)
    for raw in ['{"version":1,"version":2}', '{"version":NaN}']:
        path.write_text(raw)
        result = subprocess.run(args, capture_output=True, text=True, env=env, timeout=10)
        assert result.returncode == 1 and 'Invalid segmented ledger evidence' in result.stdout


def test_capture_cli_has_complete_evidence_without_financial_writes(database, tmp_path):
    engine, _, _ = database; root, _, _ = setup(engine); _, cmd = prepare(root)
    current = worker.execute(engine, ID, 'worker-a', 1, cmd)
    before = archive.capture(engine, ID)
    path = tmp_path / 'capture.json'
    result = subprocess.run([sys.executable, '-m', 'scripts.capture_segmented_ledger', '--ledger-id', ID,
                             '--output', str(path), '--operational'], capture_output=True, text=True, timeout=15)
    assert result.returncode == 0 and path.stat().st_mode & 0o777 == 0o600
    assert archive.verify(json.loads(path.read_text())) == before
    assert ledger.read(engine, ID) == current and archive.capture(engine, ID) == before
    with engine.connect() as db:
        assert db.scalar(text('SELECT count(*) FROM segmented_paper_commits')) == 2
        assert db.scalar(text('SELECT count(*) FROM segmented_paper_worker_records')) == 3
        assert db.scalar(text('SELECT count(*) FROM requested_paper_accounts')) == 0


def test_concurrent_capture_cannot_mix_commit_and_worker_receipt(database):
    engine, _, _ = database; root, _, _ = setup(engine); req, cmd = prepare(root)
    before = worker.execute(engine, ID, 'worker-a', 1, cmd)
    append = command(before, 'fill', 'APPEND', {'request_id': req['request_id'], 'batch': [event(req, 0, 'SUBMIT'), fill(req, 1)]})
    with ThreadPoolExecutor(2) as pool:
        write = pool.submit(worker.execute, engine, ID, 'worker-a', 1, append)
        captured = pool.submit(archive.capture, engine, ID)
        current = write.result(); value = archive.verify(captured.result())
    head = value['financial']['commits'][-1]['commit_index']
    assert head in [1, 2] and len(value['worker_records']) == head + 2
    assert value['worker_records'][-1]['node_sha256'] == value['financial']['head_sha256']
    assert ledger.read(engine, ID) == current


def test_scope_requires_declared_enrollment(database):
    engine, _, _ = database
    from tests.test_segmented_ledger import opening
    ledger.create(engine, opening())
    with pytest.raises(ValueError, match='enrollment'): archive.capture(engine, ID)
    assert financial.capture(engine, ID)['version'] == financial.VERSION


def test_corrupt_database_worker_metadata_is_not_accepted_as_offline_evidence(database):
    engine, _, _ = database; root, _, _ = setup(engine); _, cmd = prepare(root)
    worker.execute(engine, ID, 'worker-a', 1, cmd)
    with engine.begin() as db:
        db.execute(text('ALTER TABLE segmented_paper_worker_records DISABLE TRIGGER USER'))
        db.execute(text("UPDATE segmented_paper_worker_records SET command_id='changed' WHERE kind='ACCEPT'"))
        db.execute(text('ALTER TABLE segmented_paper_worker_records ENABLE TRIGGER USER'))
    with pytest.raises(ValueError, match='metadata'): archive.capture(engine, ID)
    with pytest.raises(ValueError): ledger.read(engine, ID)


def test_populated_restore_replays_identical_complete_operational_archive(database, tmp_path):
    from scripts.backup_drill import drill
    engine, _, _ = database; root, _, _ = setup(engine); _, cmd = prepare(root)
    current = worker.execute(engine, ID, 'worker-a', 1, cmd)
    expected = archive.capture(engine, ID)
    def verify(restored):
        assert archive.capture(restored, ID) == expected
        assert ledger.read(restored, ID) == current
        return {'segmented_operational_archive_equal': True, 'worker_records': 3, 'financial_revision': 1}
    result = drill(tmp_path / 'restore', verify_restored=verify)
    assert result['additional_verification']['segmented_operational_archive_equal']
    assert archive.capture(engine, ID) == expected
