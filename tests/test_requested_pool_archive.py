from copy import deepcopy
import os
import subprocess
import sys
import pytest
from core.paper import requested_pool_archive as pool_archive, requested_archive as archive
from core.paper import shared_capital_admission as admission
from core.paper.fault_adapter import sha
from scripts.paper_archive import read
from tests.test_integration import database
from tests.test_recovery_integrations import risk_ready
from tests.test_shared_capital_admission import proposed


@pytest.fixture
def populated(database):
    engine, _, _ = database
    risk_ready(engine)
    proposal, digest = proposed(engine, 'a')
    admission.prepare(engine, 'pool', proposal, digest)
    return pool_archive.capture(engine, 'pool')


def test_whole_pool_pending_holds_and_admissions_replay(populated):
    value = archive.verify(archive.pack(populated))
    assert value == populated
    assert [a['account_id'] for a in value['accounts']] == ['a','b']
    assert len(value['accounts'][0]['admissions']) == 1
    assert value['pool']['preview']['reserved_quote'].startswith('404')
    assert value['retention_authorized'] is False


@pytest.mark.parametrize('mutation', [
    lambda v:v['accounts'].pop(),
    lambda v:v['accounts'].reverse(),
    lambda v:v['accounts'][0]['admissions'].clear(),
    lambda v:v.update(risk_policy=None),
    lambda v:v.update(retention_authorized=True),
])
def test_resealed_missing_pool_coverage_or_authority_rejected(populated, mutation):
    mutation(populated)
    populated['sha256'] = sha({k:v for k,v in populated.items() if k != 'sha256'})
    with pytest.raises(ValueError):
        pool_archive.verify(populated)


def test_current_pool_and_member_operational_checkpoints_must_agree(populated, database):
    engine, _, _ = database
    old = deepcopy(populated)
    proposal, digest = proposed(engine, 'b')
    # Quantity policy requires a smaller candidate than the existing four-unit hold.
    proposal['requested_quantity'] = '1'
    from core.paper import requested_controls as controls, requested_preview as preview
    fin, ctrl = controls.read(engine, 'b')
    digest = preview.evaluate(fin, ctrl, proposal)['sha256']
    admission.prepare(engine, 'pool', proposal, digest)
    now = pool_archive.capture(engine, 'pool')
    with pytest.raises(ValueError):
        pool_archive.seal(now['pool'], now['risk_policy'], old['accounts'])


def test_cli_whole_pool_capture_and_full_scope_authorization(populated, database, tmp_path):
    engine, _, _ = database
    with pytest.raises(ValueError):
        pool_archive.capture(engine, 'pool', authorized_accounts=['a'])
    path = tmp_path / 'pool.json'
    env = {**os.environ, 'DATABASE_URL':engine.url.render_as_string(hide_password=False)}
    result = subprocess.run([sys.executable, '-m', 'scripts.paper_archive', 'capture-pool', '--pool-id', 'pool', '--output', str(path)], env=env, capture_output=True)
    assert result.returncode == 0
    value = archive.verify(read(path))
    assert value['accounts'][0]['admissions'] == populated['accounts'][0]['admissions']
    assert value['scope'] == pool_archive.SCOPE
