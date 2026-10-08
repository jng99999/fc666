"""Offline failure assessment. No transport, observation ingestion or send capability."""
from copy import deepcopy
from core.paper import requested_dispatch_query as query
from core.paper.requested_execution import MAX_BYTES
from core.paper.fault_adapter import encoded, sha

VERSION = 'paper-requested-transport-boundary-v1'
FAILURES = ('TIMEOUT', 'DISCONNECTED', 'EMPTY_RESULT', 'CONTRADICTORY_RESULT', 'UNSUPPORTED_TRANSPORT')


def evaluate(query_report, failure):
    evidence = query.verify(query_report)
    if type(failure) is not str or failure not in FAILURES:
        raise ValueError('Unsupported failure classification')
    selected = evidence['selected']
    if selected['action'] not in ('WAIT_LOCAL_RESULT', 'RECONCILE_LOCAL_PREFIX', 'LOCAL_SEALED'):
        raise ValueError('Unsupported original dispatch assessment')
    # Failure descriptions never supply order facts or prove remote absence.
    body = {'version': VERSION, 'query_evidence': evidence, 'failure': failure,
            'request_id': evidence['request_id'], 'client_id': evidence['client_id'],
            'local_action': selected['action'], 'funding': deepcopy(selected['funding']),
            'next_step': 'INSPECT_LOCAL_EVIDENCE' if selected['action'] == 'LOCAL_SEALED' else 'AWAIT_VERIFIED_LOCAL_INPUT',
            'remote_outcome': 'NOT_IMPLEMENTED', 'remote_absence_proven': False,
            'transport_available': False, 'submission_allowed': False, 'resubmission_allowed': False,
            'release_funding_allowed': False, 'observation_committed': False, 'read_only': True}
    result = {**body, 'sha256': sha(body)}
    if len(encoded(result).encode()) > MAX_BYTES:
        raise ValueError('Boundary report exceeds32MiB')
    return result


def verify(report):
    keys = {'version','query_evidence','failure','request_id','client_id','local_action','funding',
            'next_step','remote_outcome','remote_absence_proven','transport_available','submission_allowed',
            'resubmission_allowed','release_funding_allowed','observation_committed','read_only','sha256'}
    if not isinstance(report, dict) or set(report) != keys or report['version'] != VERSION:
        raise ValueError('Invalid transport boundary report')
    if len(encoded(report).encode()) > MAX_BYTES:
        raise ValueError('Boundary report exceeds32MiB')
    expected = evaluate(report['query_evidence'], report['failure'])
    if encoded(report) != encoded(expected):
        raise ValueError('Boundary assessment differs from complete replay')
    return expected
