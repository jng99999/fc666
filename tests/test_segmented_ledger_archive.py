from copy import deepcopy
import json
import subprocess
import sys
import pytest
from core.paper import segmented_ledger as ledger, segmented_ledger_archive as archive
from core.paper.fault_adapter import sha
from tests.test_segmented_ledger import opening, ID, setup, command, event, fill
from tests.test_integration import database


def evidence():
    root = ledger._node(ID, 0, None, {'kind': 'CREATE', 'opening': opening()}, ledger.accounts.replay(opening(), []))
    body = dict(version=archive.VERSION, ledger_id=ID, root_sha256=root['sha256'], head_sha256=root['sha256'],
                commits=[root], operator_worker_binding_supported=False, external_submission_allowed=False)
    return {**body, 'sha256': sha(body)}


def reseal(value):
    value['sha256'] = sha({key: item for key, item in value.items() if key != 'sha256'})


@pytest.mark.parametrize('mutation', ['balance', 'identity', 'index', 'authority', 'endpoint', 'empty'])
def test_resealed_corruption_is_rejected(mutation):
    value = evidence()
    if mutation == 'balance': value['commits'][0]['report']['account']['cash'] = '999'
    if mutation == 'identity': value['ledger_id'] = 'segmented:another'
    if mutation == 'index': value['commits'][0]['commit_index'] = True
    if mutation == 'authority': value['external_submission_allowed'] = True
    if mutation == 'endpoint': value['head_sha256'] = '0' * 64
    if mutation == 'empty': value['commits'] = []
    if value['commits']: reseal(value['commits'][0])
    reseal(value)
    with pytest.raises(ValueError): archive.verify(value)


def test_offline_cli_and_duplicate_json_rejection(tmp_path):
    value = evidence()
    assert archive.verify(value) == value
    path = tmp_path / 'archive.json'
    path.write_text(json.dumps(value))
    run = subprocess.run([sys.executable, '-m', 'scripts.verify_segmented_ledger', str(path)], capture_output=True, text=True)
    assert run.returncode == 0 and json.loads(run.stdout)['external_submission_allowed'] is False
    path.write_text('{"version":1,"version":2}')
    run = subprocess.run([sys.executable, '-m', 'scripts.verify_segmented_ledger', str(path)], capture_output=True, text=True)
    assert run.returncode == 1 and 'Invalid segmented ledger evidence' in run.stdout


def test_capture_replays_full_chain_and_rejects_missing_predecessor(database):
    engine, _, _ = database
    req, current, _, _ = setup(engine)
    current = ledger.execute(engine, ID, command(current, 'batch', 'APPEND',
        {'request_id': req['request_id'], 'batch': [event(req, 0, 'SUBMIT'), fill(req, 1)]}))
    value = archive.capture(engine, ID)
    assert len(value['commits']) == 3 and value['commits'][-1]['report'] == current['journal']
    assert ledger.read(engine, ID) == current
    bad = deepcopy(value); del bad['commits'][1]; reseal(bad)
    with pytest.raises(ValueError): archive.verify(bad)
    bad = deepcopy(value); bad['commits'][2]['command']['expected_revision'] = 0
    reseal(bad['commits'][2]); bad['head_sha256'] = bad['commits'][2]['sha256']; reseal(bad)
    with pytest.raises(ValueError): archive.verify(bad)


def test_archive_capacity_fails_closed(monkeypatch):
    value = evidence()
    monkeypatch.setattr(archive, 'MAX_BYTES', 1)
    with pytest.raises(ValueError): archive.verify(value)


def test_capture_file_is_private_and_never_overwrites_or_follows_symlink(tmp_path):
    from scripts.capture_segmented_ledger import write
    value = evidence(); path = tmp_path / 'evidence.json'
    write(path, value)
    assert path.stat().st_mode & 0o777 == 0o600
    original = path.read_bytes()
    with pytest.raises(FileExistsError): write(path, value)
    link = tmp_path / 'link'; link.symlink_to(path)
    with pytest.raises(OSError): write(link, value)
    assert path.read_bytes() == original
