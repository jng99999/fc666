import pytest
from decimal import Decimal
import os
import subprocess
import sys
from scripts.paper_archive import read
from core.paper import requested_operational_archive as operational, requested_archive as archive
from core.paper.fault_adapter import sha
from tests.test_integration import database
from tests.test_requested_health import ready


def test_coherent_database_snapshot_replays_all_declared_scopes(database, tmp_path):
    engine, _, _ = database
    ready(engine)
    value = operational.capture(engine, 'account')
    assert archive.verify(archive.pack(value)) == value
    assert value['health']['policy'] is not None
    assert value['dispatch']['ownership_evidence']['source_evidence']['controls']['coverage'] == 'CONTROLLED'
    assert value['excluded_evidence'] == ['ATTEMPTS', 'INBOX', 'POOL']
    assert value['retention_authorized'] is False
    path = tmp_path / 'operational.json'
    env = {**os.environ, 'DATABASE_URL': engine.url.render_as_string(hide_password=False)}
    result = subprocess.run([sys.executable, '-m', 'scripts.paper_archive', 'capture',
                             '--account-id', 'account', '--operational', '--output', str(path)],
                            env=env, capture_output=True)
    assert result.returncode == 0
    captured = archive.verify(read(path))
    assert captured['health'] == value['health']
    assert captured['scope'] == operational.SCOPE


def test_owned_pending_request_preserves_lease_dispatch_and_holds(database):
    from tests.test_five_block_foundations import staged
    engine, _, _ = database
    ready(engine, enroll=False)
    proposal = staged(engine)
    value = archive.verify(archive.pack(operational.capture(engine, 'account')))
    owned = value['dispatch']['ownership_evidence']
    financial = owned['source_evidence']['journal']['journal']
    assert financial['active_request_id'] == proposal['request_id']
    assert Decimal(financial['requests'][0]['summary']['funding']['reserved_cash']) == 404
    assert owned['claims'][0]['token'] == 1
    assert len(value['dispatch']['dispatches']) == 1


def test_separately_valid_control_checkpoints_cannot_be_mixed(database):
    from core.paper import requested_controls as controls
    engine, _, _ = database
    ready(engine)
    before = operational.capture(engine, 'account')
    # A second valid control checkpoint on the same financial state still differs.
    from tests.test_requested_execution import STAMP
    controls.command(engine, 'account', 'stop', 2, 'STOP', STAMP.isoformat())
    after = operational.capture(engine, 'account')
    assert before['health']['journal'] == after['health']['journal']
    with pytest.raises(ValueError):
        operational.seal(before['dispatch'], after['health'])


@pytest.mark.parametrize('field,value', [('scope','COMPLETE'), ('retention_authorized',True), ('external_submission_allowed',True), ('excluded_evidence',[])])
def test_rehashed_manifest_cannot_expand_scope_or_authority(database, field, value):
    engine, _, _ = database
    ready(engine)
    report = operational.capture(engine, 'account')
    report[field] = value
    report['sha256'] = sha({k:v for k,v in report.items() if k != 'sha256'})
    with pytest.raises(ValueError):
        operational.verify(report)
