from copy import deepcopy
import os
import subprocess
import sys
import pytest
from core.paper import requested_operational_archive as operational, requested_archive as archive
from core.paper import requested_inbox as inbox, requested_attempts as attempts
from core.paper.fault_adapter import sha
from tests.test_integration import database
from tests.test_requested_dispatch import submitted
from tests.test_requested_execution import fill
from scripts.paper_archive import read


@pytest.fixture
def evidence(database):
    engine, _, _ = database
    req, _, _, _ = submitted(engine)
    proposal = dict(account_id='account', request_id=req['request_id'], expected_financial_revision=2,
                    owner='first', ownership_token=1, source={'kind':'LOCAL_PAPER_OPERATOR_INPUT','source_id':'fixture'},
                    event=fill(req, 1))
    inbox.stage(engine, proposal)
    current = operational.capture(engine, 'account')
    slot = current['dispatch']['dispatches'][0]
    cmd = dict(account_id='account', request_id=proposal['request_id'],
               client_id=slot['dispatch']['client_id'], owner='first', ownership_token=1,
               attempt_id='timeout-1', failure='TIMEOUT', expected_financial_revision=2,
               expected_control_revision=2)
    attempts.record(engine, cmd)
    return operational.capture(engine, 'account', include_receipts=True)


def test_populated_receipt_archive_preserves_staging_and_no_remote_send(evidence, database):
    result = archive.verify(archive.pack(evidence))
    assert result == evidence
    assert result['excluded_evidence'] == ['POOL']
    assert result['attempt_groups'][0]['receipts'][0]['remote_send_performed'] is False
    assert result['inbox'][0]['event_committed'] is False
    assert result['application_observations'] == [{'ordinal':0, 'journal_state':'NOT_PRESENT_IN_JOURNAL'}]
    engine, _, _ = database
    receipt = evidence['inbox'][0]
    inbox.apply(engine, dict(account_id='account', ordinal=0, staging_sha256=receipt['sha256'],
                             expected_financial_revision=2, expected_control_revision=2,
                             owner='first', ownership_token=1))
    after = operational.capture(engine, 'account', include_receipts=True)
    assert archive.verify(archive.pack(after))['application_observations'] == [{'ordinal':0, 'journal_state':'PRESENT_IN_JOURNAL'}]
    assert after['inbox'] == evidence['inbox']
    assert after['attempt_groups'] == evidence['attempt_groups']


@pytest.mark.parametrize('mutation', [
    lambda v: v['attempt_groups'].clear(),
    lambda v: v['attempt_groups'][0].update(request_id='0'*64),
    lambda v: v['application_observations'][0].update(journal_state='PRESENT_IN_JOURNAL'),
    lambda v: v['inbox'].append(deepcopy(v['inbox'][0])),
])
def test_resealed_missing_coverage_or_invented_application_rejected(evidence, mutation):
    mutation(evidence)
    evidence['sha256'] = sha({k:v for k,v in evidence.items() if k != 'sha256'})
    with pytest.raises(ValueError):
        operational.verify(evidence)


def test_resealed_staging_claim_binding_rejected(evidence):
    receipt = evidence['inbox'][0]
    receipt['proposal']['owner'] = 'other'
    receipt['sha256'] = sha({k:v for k,v in receipt.items() if k != 'sha256'})
    evidence['sha256'] = sha({k:v for k,v in evidence.items() if k != 'sha256'})
    with pytest.raises(ValueError, match='historical checkpoint'):
        operational.verify(evidence)


def test_cli_receipt_scope_is_explicit_and_replays_populated_database(evidence, database, tmp_path):
    engine, _, _ = database
    path = tmp_path / 'receipts.json'
    env = {**os.environ, 'DATABASE_URL':engine.url.render_as_string(hide_password=False)}
    args = [sys.executable, '-m', 'scripts.paper_archive', 'capture', '--account-id', 'account',
            '--output', str(path), '--receipts']
    assert subprocess.run(args, env=env, capture_output=True).returncode == 1
    assert not path.exists()
    assert subprocess.run([*args, '--operational'], env=env, capture_output=True).returncode == 0
    result = archive.verify(read(path))
    assert result['attempt_groups'] == evidence['attempt_groups']
    assert result['inbox'] == evidence['inbox']
    assert result['scope'] == operational.SCOPE_V2
