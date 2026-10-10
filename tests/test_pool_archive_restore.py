import time
import pytest
from sqlalchemy import text
from core.paper import requested_pool_archive as pool_archive, requested_archive as archive
from core.paper import requested_ownership as ownership, shared_capital_admission as admission
from scripts import backup_drill
from tests.test_integration import database
from tests.test_recovery_integrations import risk_ready
from tests.test_shared_capital_admission import proposed
from tests.test_requested_execution import event, fill


def test_populated_pool_archive_replays_after_backup_restore_and_reopen(database, tmp_path, monkeypatch):
    engine, _, _ = database
    risk_ready(engine)
    proposal, digest = proposed(engine, 'a')
    accepted = admission.prepare(engine, 'pool', proposal, digest)
    req = accepted['local_preview']['request']
    from core.paper import requested_inbox as inbox, requested_dispatch as dispatch, requested_attempts as attempts
    ownership.claim(engine, 'a', req['request_id'], 'worker', 60)
    staged = dict(account_id='a', request_id=req['request_id'], expected_financial_revision=1,
                  owner='worker', ownership_token=1, source={'kind':'LOCAL_PAPER_OPERATOR_INPUT','source_id':'fixture'}, event=event(req,0,'SUBMIT'))
    receipt = inbox.stage(engine, staged)
    inbox.apply(engine, dict(account_id='a', ordinal=0, staging_sha256=receipt['sha256'], expected_financial_revision=1,
                             expected_control_revision=2, owner='worker', ownership_token=1))
    inbox.stage(engine, {**staged, 'expected_financial_revision':2, 'event':fill(req,1)})
    dispatched = dispatch.capture(engine, 'a')
    attempts.record(engine, dict(account_id='a', request_id=req['request_id'], client_id=dispatched['dispatches'][0]['dispatch']['client_id'],
                                 owner='worker', ownership_token=1, attempt_id='restore-timeout', failure='TIMEOUT',
                                 expected_financial_revision=2, expected_control_revision=2))
    # Stable observation time makes before/after snapshots comparable; stored
    # event/claim/admission clocks are preserved and independently verified.
    observed_us = time.time_ns() // 1000
    monkeypatch.setattr(ownership, 'clock', lambda db:observed_us)
    expected = pool_archive.capture(engine, 'pool')
    packed = archive.pack(expected)
    engine.dispose()
    assert archive.verify(archive.pack(pool_archive.capture(engine, 'pool'))) == expected
    seen = []
    def validate(restored):
        restored.dispose()
        actual = pool_archive.capture(restored, 'pool')
        assert archive.verify(archive.pack(actual)) == archive.verify(packed)
        assert actual == expected
        seen.append(True)
        return dict(scope=pool_archive.SCOPE, complete_fixture_replay=True, pending_reservations_preserved=True)
    report = backup_drill.drill(tmp_path / 'backups', verify_restored=validate)
    assert seen == [True]
    assert report['isolated_restore_verified'] and report['row_content_verified']
    assert report['additional_verification']['complete_fixture_replay']
    assert pool_archive.capture(engine, 'pool') == expected


def test_restore_semantic_failure_propagates_and_cleans_clone(database, tmp_path):
    engine, _, _ = database
    def reject(restored):
        raise ValueError('Fixture semantic mismatch')
    with pytest.raises(ValueError, match='semantic mismatch'):
        backup_drill.drill(tmp_path / 'failed', verify_restored=reject)
    targets = [p.stem for p in (tmp_path / 'failed').glob('*.dump')]
    assert len(targets) == 1
    with engine.connect() as db:
        assert db.scalar(text('SELECT count(*) FROM pg_database WHERE datname=:target'), {'target':targets[0]}) == 0
        assert db.scalar(text('SELECT version_num FROM alembic_version')) == '0026'
