"""Local recovery classifications from complete immutable evidence."""
from copy import deepcopy
import json
import subprocess
import sys
import pytest
from core.paper import requested_recovery as recovery, requested_sources as sources, requested_inspection as inspection
from core.paper.fault_adapter import sha
from tests.test_requested_event_preview import snapshot
from tests.test_requested_execution import request, event, fill, cumulative


def source_report(events=None, *, unlabeled=False, stopped=False):
    req, financial, controlled = snapshot(events, stopped=stopped)
    slots = []
    for seq, value in enumerate(events or []):
        receipt = None if unlabeled else sources.receipt(sources.reconstruct(
            financial, controlled, 0, seq, {'kind': 'LOCAL_PAPER_OPERATOR_INPUT', 'source_id': 'fixture'}))
        slots.append({'request_id': req['request_id'], 'sequence': seq, 'event_id': value['event_id'],
                      'source_version': None if unlabeled else sources.VERSION, 'receipt': receipt})
    body = {'version': sources.EXPORT_VERSION, 'journal': inspection.seal(financial),
            'controls': controlled, 'sources': slots}
    return {**body, 'sha256': sha(body)}


@pytest.mark.parametrize('kinds,action', [
    ([], 'NOT_SUBMITTED_LOCAL'), (['SUBMIT'], 'WAIT_LOCAL_RESULT'),
    (['SUBMIT', 'UNKNOWN_SUBMISSION'], 'WAIT_LOCAL_RESULT'),
    (['SUBMIT', 'ACK'], 'RECONCILE_LOCAL_PREFIX'),
    (['SUBMIT', 'REJECT'], 'RECONCILE_LOCAL_PREFIX'),
    (['SUBMIT', 'CANCEL_REQUEST'], 'RECONCILE_LOCAL_PREFIX'),
    (['SUBMIT', 'CANCEL_REQUEST', 'CANCEL_ACK'], 'RECONCILE_LOCAL_PREFIX'),
])
def test_local_recovery_never_authorizes_submission(kinds, action):
    req = request(); events = [event(req, seq, kind) for seq, kind in enumerate(kinds)]
    evidence = source_report(events); before = deepcopy(evidence)
    report = recovery.evaluate(evidence); row = report['requests'][0]
    assert row['action'] == action and recovery.verify(report) == report
    assert row['ownership'] == {'coverage': 'NOT_IMPLEMENTED', 'owner': None, 'fencing_token': None}
    assert not row['resubmission_allowed'] and not row['submission_allowed'] and not row['external_query_supported']
    assert row['funding']['reserved_cash'] == '404.000000000000000000000000000000000000'
    assert evidence == before


def test_original_submission_attribution_survives_stop_and_late_fills():
    req = request(); submit = event(req, 0, 'SUBMIT')
    one = recovery.evaluate(source_report([submit]))
    later = recovery.evaluate(source_report([submit, event(req, 1, 'UNKNOWN_SUBMISSION'), fill(req, 2)], stopped=True))
    assert later['requests'][0]['submission_attribution'] == one['requests'][0]['submission_attribution']
    assert later['requests'][0]['action'] == 'RECONCILE_LOCAL_PREFIX'
    assert later['account']['cash'] == '909.10' and later['control_revision'] == 3


def test_legacy_events_do_not_gain_submission_origin_or_ownership():
    req = request(); values = [event(req, 0, 'SUBMIT'), event(req, 1, 'ACK')]
    row = recovery.evaluate(source_report(values, unlabeled=True))['requests'][0]
    assert row['action'] == 'UNVERIFIED_SUBMISSION_PROVENANCE' and row['submission_attribution'] is None
    assert row['coverage'] == 'UNLABELED' and row['unlabeled_event_count'] == 2


def test_cancellation_ack_retains_hold_until_local_seal():
    req = request(); values = [event(req, 0, 'SUBMIT'), fill(req, 1), event(req, 2, 'CANCEL_REQUEST'), event(req, 3, 'CANCEL_ACK')]
    before = recovery.evaluate(source_report(values))['requests'][0]
    assert before['funding']['reserved_cash'].startswith('303.')
    after = recovery.evaluate(source_report(values + [cumulative(req, 4, '1', '0.90', '90', seal=True)]))['requests'][0]
    assert after['action'] == 'LOCAL_SEALED' and after['funding']['reserved_cash'] == '0'
    assert after['submission_attribution'] == before['submission_attribution']


@pytest.mark.parametrize('change', [
    lambda r: r['requests'][0].update(resubmission_allowed=True),
    lambda r: r['requests'][0].update(action='RETRY_REMOTE'),
    lambda r: r['requests'][0]['ownership'].update(owner='fake'),
    lambda r: r['requests'][0]['submission_attribution'].update(source={'kind':'EXCHANGE_VERIFIED','source_id':'fake'}),
    lambda r: r.update(financial_revision=99), lambda r: r['account'].update(cash='10000'),
    lambda r: r.update(read_only=False), lambda r: r.update(version='future'),
    lambda r: r.update(requests=[]),
    lambda r: r['requests'][0]['funding'].update(reserved_cash='0'),
])
def test_rehashed_recovery_tampering_is_rejected(change):
    req = request(); report = recovery.evaluate(source_report([event(req, 0, 'SUBMIT')]))
    change(report); report['sha256'] = sha({key: value for key, value in report.items() if key != 'sha256'})
    with pytest.raises((ValueError, KeyError, TypeError)): recovery.verify(report)


def test_offline_cli_and_duplicate_keys(tmp_path, monkeypatch):
    req = request(); report = recovery.evaluate(source_report([event(req, 0, 'SUBMIT')]))
    monkeypatch.setenv('DATABASE_URL', 'postgresql+psycopg://invalid:invalid@127.0.0.1:1/no_db')
    path = tmp_path/'recovery.json'; path.write_text(json.dumps(report))
    command = [sys.executable, '-m', 'scripts.verify_requested_recovery', str(path)]
    result = subprocess.run(command, capture_output=True, text=True, timeout=10)
    assert result.returncode == 0 and 'WAIT_LOCAL_RESULT' in result.stdout, result.stderr
    path.write_text('{"version":"duplicate",' + json.dumps(report)[1:])
    assert subprocess.run(command, capture_output=True, timeout=10).returncode != 0


def test_complete_envelope_bounds_and_missing_declared_evidence(monkeypatch):
    req = request(); evidence = source_report([event(req, 0, 'SUBMIT')]); bad = deepcopy(evidence)
    bad['sources'][0]['receipt'] = None; bad['sha256'] = sha({k:v for k,v in bad.items() if k != 'sha256'})
    with pytest.raises(ValueError): recovery.evaluate(bad)
    monkeypatch.setattr(recovery.contract, 'MAX_BYTES', 100)
    with pytest.raises(ValueError): recovery.evaluate(evidence)


def test_full_fill_without_seal_does_not_claim_source_finality():
    req = request(); values = [event(req, 0, 'SUBMIT'), fill(req, 1, size='4', fee='3.60')]
    row = recovery.evaluate(source_report(values))['requests'][0]
    assert row['state'] == 'FILLED' and row['action'] == 'RECONCILE_LOCAL_PREFIX'
    assert not row['local_source_sealed'] and row['funding']['reserved_cash'].startswith('0')


def test_later_request_keeps_current_account_and_old_submission_identity():
    from core.paper import requested_controls as controls, requested_execution as contract
    req = request(); values = [event(req, 0, 'SUBMIT'), fill(req, 1, size='4', fee='3.60'), cumulative(req, 2, '4', '3.60', '360', seal=True)]
    old = recovery.evaluate(source_report(values))
    req, financial, controlled = snapshot(values)
    following = request(client_request_id='next', base=financial['account'], created_at=values[-1]['received_at'])
    financial = inspection.replay(financial['opening'], financial['requests'] + [
        {'request':following,'events':[],'summary':contract.reduce(following,[])}])
    gates = controlled['gates'] + [controls.decision(following, controlled['policy'], controlled['records'][-1], 'PREPARE', 4, following['created_at'])]
    controlled = controls.replay(controls.VERSION, controlled['policy'], controlled['records'], gates, financial)
    evidence = deepcopy(old['source_evidence']); evidence['journal'] = inspection.seal(financial); evidence['controls'] = controlled
    evidence['sha256'] = sha({k:v for k,v in evidence.items() if k != 'sha256'})
    report = recovery.evaluate(evidence)
    assert report['requests'][0]['submission_attribution'] == old['requests'][0]['submission_attribution']
    assert report['requests'][1]['action'] == 'NOT_SUBMITTED_LOCAL'
    assert report['active_request_id'] == following['request_id'] and report['financial_revision'] == 5
    assert report['account'] == financial['account'] and recovery.verify(report) == report
