"""Fenced local inspection by original client identity; never remote query/retry."""
import re
from copy import deepcopy
from sqlalchemy import text
from core.paper import requested_dispatch as dispatch, requested_ownership as ownership, requested_journal as journal
from core.paper.fault_adapter import sha, encoded

VERSION = 'paper-requested-dispatch-query-v1'


def evaluate(evidence, request_id, client_id, owner, ownership_token):
    report = dispatch.verify(evidence)
    if any(type(value) is not str or re.fullmatch('[0-9a-f]{64}', value) is None for value in (request_id, client_id)):
        raise ValueError('Invalid request/client identity')
    if type(owner) is not str or not 1 <= len(owner) <= 128 or type(ownership_token) is not int or not 1 <= ownership_token <= 64:
        raise ValueError('Invalid ownership identity')
    leases = report['ownership_evidence']['leases']
    lease = next((value for value in leases if value['request_id'] == request_id), None)
    slot = next((value for value in report['dispatches'] if value['request_id'] == request_id), None)
    if lease is None or slot is None or slot['dispatch'] is None or slot['dispatch']['client_id'] != client_id:
        raise ValueError('Original dispatch identity unavailable or different')
    claim = lease['latest_claim']
    if not lease['unexpired_at_observation'] or claim['owner'] != owner or claim['token'] != ownership_token:
        raise ValueError('Query requires current observed lease')
    selected = next(value for value in report['recovery'] if value['request_id'] == request_id)
    body = {'version': VERSION, 'dispatch_evidence': report, 'request_id': request_id,
            'client_id': client_id, 'owner': owner, 'ownership_token': ownership_token,
            'selected': deepcopy(selected), 'read_only': True, 'resubmission_allowed': False,
            'external_query_supported': False}
    result = {**body, 'sha256': sha(body)}
    if len(encoded(result).encode()) > dispatch.sources.contract.MAX_BYTES:
        raise ValueError('Query report exceeds32MiB')
    return result


def capture(engine, account_id, request_id, client_id, owner, ownership_token):
    with journal.transaction(engine) as db:
        db.execute(text("SELECT set_config('lock_timeout','2000ms',true)"))
        row = journal.lock(db, account_id)
        financial = journal.audit(db, row)
        if not any(value['request']['request_id'] == request_id for value in financial['requests']):
            raise ValueError('Request does not belong to account')
        controlled = ownership.controls.check(db, row, financial)
        ownership._owned(db, request_id, owner, ownership_token)
        result = evaluate(dispatch.snapshot(db, row, financial, controlled), request_id, client_id, owner, ownership_token)
        ownership._owned(db, request_id, owner, ownership_token)
        return result


def verify(report):
    keys = {'version', 'dispatch_evidence', 'request_id', 'client_id', 'owner', 'ownership_token',
            'selected', 'read_only', 'resubmission_allowed', 'external_query_supported', 'sha256'}
    if not isinstance(report, dict) or set(report) != keys or report['version'] != VERSION:
        raise ValueError('Invalid dispatch query report')
    if len(encoded(report).encode()) > dispatch.sources.contract.MAX_BYTES:
        raise ValueError('Query report exceeds32MiB')
    expected = evaluate(report['dispatch_evidence'], report['request_id'], report['client_id'], report['owner'], report['ownership_token'])
    if encoded(report) != encoded(expected):
        raise ValueError('Query report differs from replay')
    return expected
