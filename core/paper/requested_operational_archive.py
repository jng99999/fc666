"""Coherent read-only financial/control/source/lease/dispatch/health evidence."""
from copy import deepcopy
from sqlalchemy import text
from core.paper import requested_journal as journal, requested_controls as controls
from core.paper import requested_dispatch as dispatch, requested_health as health
from core.paper.fault_adapter import encoded, sha

VERSION = 'paper-requested-operational-evidence-v1'
SCOPE = 'FINANCIAL_CONTROLS_SOURCES_OWNERSHIP_DISPATCH_HEALTH'
MAX_BYTES = 32 * 1024 * 1024


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
    if not isinstance(value, dict) or set(value) != {'version', 'scope', 'dispatch', 'health', 'excluded_evidence', 'retention_authorized', 'external_submission_allowed', 'sha256'}:
        raise ValueError('Invalid operational evidence')
    if len(encoded(value)) > MAX_BYTES or encoded(value) != encoded(seal(value['dispatch'], value['health'])):
        raise ValueError('Operational evidence differs from replay')
    return deepcopy(value)


def capture(engine, account_id):
    with journal.transaction(engine) as db:
        db.execute(text("SET LOCAL lock_timeout='2000ms'"))
        row = journal.lock(db, account_id)
        financial = journal.audit(db, row)
        controlled = controls.check(db, row, financial)
        return seal(dispatch.snapshot(db, row, financial, controlled),
                    health.snapshot(db, row, financial, controlled))
