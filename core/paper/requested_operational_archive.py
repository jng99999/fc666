"""Coherent read-only financial/control/source/lease/dispatch/health evidence."""
from copy import deepcopy
from sqlalchemy import text
from core.paper import requested_journal as journal, requested_controls as controls
from core.paper import requested_dispatch as dispatch, requested_health as health
from core.paper.fault_adapter import encoded, sha

VERSION = 'paper-requested-operational-evidence-v1'
SCOPE = 'FINANCIAL_CONTROLS_SOURCES_OWNERSHIP_DISPATCH_HEALTH'
MAX_BYTES = 32 * 1024 * 1024
VERSION_V2 = 'paper-requested-operational-evidence-v2'
SCOPE_V2 = SCOPE + '_ATTEMPTS_INBOX'


def seal_receipts(dispatched, healthy, attempt_groups, staged):
    from core.paper import requested_attempts as attempts, requested_inbox as inbox
    base = seal(dispatched, healthy)
    owned = base['dispatch']['ownership_evidence']
    source = owned['source_evidence']
    financial = source['journal']['journal']
    controlled = source['controls']
    entries = financial['requests']
    if not isinstance(attempt_groups, list) or len(attempt_groups) != len(entries):
        raise ValueError('Incomplete attempt request coverage')
    for entry, group in zip(entries, attempt_groups):
        if not isinstance(group, dict) or set(group) != {'request_id', 'receipts'} or group['request_id'] != entry['request']['request_id']:
            raise ValueError('Attempt request order differs')
        attempts.evaluate_export(dispatched, group['request_id'], group['receipts'])
    if not isinstance(staged, list) or len(staged) > inbox.LIMIT or len(encoded(staged)) > inbox.MAX_BYTES:
        raise ValueError('Inbox evidence exceeds bounds')
    lookup = {e['request']['request_id']: e for e in entries}
    claims = {(c['request_id'], c['token']): c for c in owned['claims']}
    identities = set(); sequences = set(); observations = []
    for ordinal, receipt in enumerate(staged):
        receipt = inbox.verify(receipt); proposal = receipt['proposal']; event = proposal['event']
        entry = lookup.get(proposal['request_id'])
        claim = claims.get((proposal['request_id'], proposal['ownership_token']))
        revision = receipt['control_revision']
        if (entry is None or claim is None or receipt['ordinal'] != ordinal or
            proposal['account_id'] != financial['opening']['account_id'] or
            receipt['request_sha256'] != sha(entry['request']) or receipt['claim_sha256'] != sha(claim) or
            claim['owner'] != proposal['owner'] or not claim['acquired_us'] <= receipt['received_us'] < claim['expires_us'] or
            not claim['journal_revision'] <= proposal['expected_financial_revision'] <= financial['revision'] or
            not claim['control_revision'] <= revision <= controlled['revision'] or
            controlled['records'][revision - 1]['journal_revision'] > proposal['expected_financial_revision']):
            raise ValueError('Inbox historical checkpoint differs')
        identity = (proposal['request_id'], event['event_id'])
        sequence = (proposal['request_id'], event['sequence'])
        if identity in identities or sequence in sequences:
            raise ValueError('Duplicate staged identity or sequence')
        identities.add(identity); sequences.add(sequence)
        actual = next((e for e in entry['events'] if e['event_id'] == event['event_id']), None)
        state = 'NOT_PRESENT_IN_JOURNAL' if actual is None else 'PRESENT_IN_JOURNAL' if encoded(actual) == encoded(event) else 'CONFLICTING_JOURNAL_EVENT'
        observations.append(dict(ordinal=ordinal, journal_state=state))
    body = {**{k:v for k,v in base.items() if k != 'sha256'},
            'version': VERSION_V2, 'scope': SCOPE_V2, 'excluded_evidence': ['POOL'],
            'attempt_groups': deepcopy(attempt_groups), 'inbox': deepcopy(staged),
            'application_observations': observations}
    result = {**body, 'sha256': sha(body)}
    if len(encoded(result)) > MAX_BYTES:
        raise ValueError('Operational evidence exceeds bounds')
    return result


def seal(dispatched, healthy):
    dispatched = dispatch.verify(dispatched)
    healthy = health.verify(healthy)
    source = dispatched['ownership_evidence']['source_evidence']
    if any(encoded(source[key]) != encoded(healthy[key]) for key in ['journal', 'controls']):
        raise ValueError('Operational evidence checkpoint differs')
    body = dict(version=VERSION, scope=SCOPE, dispatch=dispatched, health=healthy,
                excluded_evidence=['ATTEMPTS', 'INBOX', 'POOL'],
                retention_authorized=False, external_submission_allowed=False)
    result = {**body, 'sha256': sha(body)}
    if len(encoded(result)) > MAX_BYTES:
        raise ValueError('Operational evidence exceeds bounds')
    return result


def verify(value):
    if isinstance(value, dict) and value.get('version') == VERSION_V2:
        keys = {'version', 'scope', 'dispatch', 'health', 'excluded_evidence', 'retention_authorized', 'external_submission_allowed', 'sha256', 'attempt_groups', 'inbox', 'application_observations'}
        if set(value) != keys or len(encoded(value)) > MAX_BYTES or encoded(value) != encoded(seal_receipts(value['dispatch'], value['health'], value['attempt_groups'], value['inbox'])):
            raise ValueError('Operational receipt evidence differs from replay')
        return deepcopy(value)
    if not isinstance(value, dict) or set(value) != {'version', 'scope', 'dispatch', 'health', 'excluded_evidence', 'retention_authorized', 'external_submission_allowed', 'sha256'}:
        raise ValueError('Invalid operational evidence')
    if len(encoded(value)) > MAX_BYTES or encoded(value) != encoded(seal(value['dispatch'], value['health'])):
        raise ValueError('Operational evidence differs from replay')
    return deepcopy(value)


def capture(engine, account_id, *, include_receipts=False):
    with journal.transaction(engine) as db:
        db.execute(text("SET LOCAL lock_timeout='2000ms'"))
        row = journal.lock(db, account_id)
        financial = journal.audit(db, row)
        controlled = controls.check(db, row, financial)
        dispatched = dispatch.snapshot(db, row, financial, controlled)
        healthy = health.snapshot(db, row, financial, controlled)
        if include_receipts:
            from core.paper import requested_attempts as attempts, requested_inbox as inbox
            groups = [dict(request_id=e['request']['request_id'], receipts=attempts.check(db, account_id, e['request']['request_id'], current=dispatched)) for e in financial['requests']]
            staged = [deepcopy(r.payload) for r in inbox.records(db, account_id)]
            return seal_receipts(dispatched, healthy, groups, staged)
        return seal(dispatched, healthy)
