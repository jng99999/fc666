"""Read-only local submission attribution/recovery; never authorizes dispatch."""
from copy import deepcopy
from core.paper import requested_sources as sources, requested_execution as contract
from core.paper.fault_adapter import encoded, sha

VERSION = 'paper-requested-recovery-v1'


def evaluate(source_report):
    evidence = sources.verify(source_report)
    financial = evidence['journal']['journal']
    slots = {(item['request_id'], item['sequence']): item for item in evidence['sources']}
    requests = []
    for entry in financial['requests']:
        request, events, summary = entry['request'], entry['events'], entry['summary']
        submit = events[0] if events else None
        receipt = slots[(request['request_id'], 0)]['receipt'] if submit else None
        attribution = None
        if receipt is not None:
            attribution = {
                'submission_id': sha({'version': VERSION, 'request_id': request['request_id'],
                                      'submit_event_sha256': receipt['event_sha256']}),
                'request_sha256': sha(request), 'submit_event_id': submit['event_id'],
                'submit_receipt_sha256': sha(receipt), 'source': deepcopy(receipt['source']),
                'original_financial_revision': receipt['expected_financial_revision'],
                'original_control_revision': receipt['expected_control_revision'],
            }
        void = entry.get('void')
        if void is not None:
            action = 'LOCAL_VOIDED'
        elif submit is None:
            action = 'NOT_SUBMITTED_LOCAL'
        elif receipt is None:
            action = 'UNVERIFIED_SUBMISSION_PROVENANCE'
        elif summary['local_source_sealed']:
            action = 'LOCAL_SEALED'
        elif summary['state'] in ('SUBMITTED', 'UNKNOWN_SUBMISSION'):
            action = 'WAIT_LOCAL_RESULT'
        else:
            action = 'RECONCILE_LOCAL_PREFIX'
        requests.append({
            'request_id': request['request_id'], 'state': summary['state'], 'action': action,
            'submission_attribution': attribution,
            'coverage': 'LOCAL_DECLARED' if receipt else 'UNLABELED' if submit else 'NO_SUBMISSION',
            'event_count': len(events), 'unlabeled_event_count': sum(
                slots[(request['request_id'], event['sequence'])]['receipt'] is None for event in events),
            'local_source_sealed': summary['local_source_sealed'],
            'funding': deepcopy(summary['funding']),
            'ownership': {'coverage': 'NOT_IMPLEMENTED', 'owner': None, 'fencing_token': None},
            'submission_allowed': False, 'resubmission_allowed': False,
            'external_query_supported': False, 'external_submission_allowed': False,
        })
    body = {'version': VERSION, 'source_evidence': deepcopy(source_report),
            'account_id': financial['opening']['account_id'],
            'financial_revision': financial['revision'],
            'control_revision': evidence['controls']['revision'],
            'active_request_id': financial['active_request_id'], 'account': deepcopy(financial['account']),
            'requests': requests, 'read_only': True, 'external_submission_allowed': False}
    result = {**body, 'sha256': sha(body)}
    if len(encoded(result).encode()) > contract.MAX_BYTES:
        raise ValueError('Recovery export exceeds 32 MiB')
    return result


def capture(engine, account_id):
    return evaluate(sources.capture(engine, account_id))


def verify(report):
    keys = {'version', 'source_evidence', 'account_id', 'financial_revision', 'control_revision',
            'active_request_id', 'account', 'requests', 'read_only', 'external_submission_allowed', 'sha256'}
    if (not isinstance(report, dict) or set(report) != keys or report['version'] != VERSION
            or len(encoded(report).encode()) > contract.MAX_BYTES):
        raise ValueError('Invalid recovery envelope/version/bounds')
    expected = evaluate(report['source_evidence'])
    if encoded(report) != encoded(expected):
        raise ValueError('Recovery differs from complete evidence replay')
    return deepcopy(expected)
