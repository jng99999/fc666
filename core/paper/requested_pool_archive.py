"""Coherent whole-pool evidence; offline consistency, never restore authority."""
from copy import deepcopy
from sqlalchemy import text
from core.paper import shared_capital_pool as pools, shared_capital_admission as admission
from core.paper import requested_operational_archive as operational, requested_journal as journal
from core.paper import requested_controls as controls, requested_preparation as preparation
from core.paper import requested_ownership as ownership
from core.paper.requested_history_binding import equal, prefix
from core.paper.fault_adapter import encoded, sha
from core.portfolio import pool_risk

VERSION = 'paper-requested-pool-evidence-v1'
SCOPE = 'WHOLE_POOL_FINANCIAL_OPERATIONAL_RISK_ADMISSIONS'
MAX_BYTES = 32 * 1024 * 1024


def seal(pool_capture, policy, accounts):
    if len(encoded({'pool':pool_capture, 'policy':policy, 'accounts':accounts})) > MAX_BYTES:
        raise ValueError('Whole-pool archive input exceeds bounds')
    pool = pools.verify(pool_capture)
    definition = pool['definition']
    if pool['preview']['candidate'] is not None:
        raise ValueError('Archive requires an observed pool, not a candidate')
    if policy is not None:
        policy = pool_risk.validate(policy)
        if policy['pool_id'] != definition['pool_id'] or set(policy['max_quantity_by_instrument']) != {a['instrument_id'] for a in definition['allocations']}:
            raise ValueError('Risk policy differs from pool membership')
    if not isinstance(accounts, list) or len(accounts) != len(definition['allocations']):
        raise ValueError('Incomplete operational pool membership')
    latest = {}
    for allocation, report, account in zip(definition['allocations'], pool['preview']['journals'], accounts):
        if not isinstance(account, dict) or set(account) != {'account_id', 'operational', 'admissions'} or account['account_id'] != allocation['account_id']:
            raise ValueError('Operational pool account order differs')
        evidence = operational.verify(account['operational'])
        if evidence['version'] != operational.VERSION_V2:
            raise ValueError('Pool archive requires receipt coverage')
        if evidence['dispatch']['ownership_evidence']['observed_us'] > pool['observed_us']:
            raise ValueError('Operational checkpoint exceeds pool observation')
        source = evidence['dispatch']['ownership_evidence']['source_evidence']
        equal(source['journal'], report)
        latest[allocation['account_id']] = source
    for account in accounts:
        source = latest[account['account_id']]
        financial = source['journal']['journal']
        receipts = account['admissions']
        if not isinstance(receipts, list) or len(receipts) != len(financial['requests']):
            raise ValueError('Incomplete pool admission coverage')
        for index, (entry, receipt) in enumerate(zip(financial['requests'], receipts)):
            value = admission.verify(receipt)
            equal(value['pool_capture']['definition'], definition)
            if (policy is None) != (value['version'] == admission.VERSION):
                raise ValueError('Mandatory pool policy coverage differs')
            if policy is not None:
                equal(value['risk_decision']['policy'], policy)
            local = value['local_preview']
            equal(local['request'], entry['request'])
            equal(local['journal']['journal'], preparation.prefix(financial, index))
            prefix(local['controls']['records'], source['controls']['records'])
            if value['pool_capture']['observed_us'] > pool['observed_us']:
                raise ValueError('Admission pool checkpoint is from the future')
            for historical in value['pool_capture']['preview']['journals']:
                old = historical['journal']
                now = latest[old['opening']['account_id']]['journal']['journal']
                equal(old['opening'], now['opening'])
                if old['revision'] > now['revision'] or len(old['requests']) > len(now['requests']):
                    raise ValueError('Admission financial checkpoint is from the future')
                for prior, current in zip(old['requests'], now['requests']):
                    equal(prior['request'], current['request'])
                    prefix(prior['events'], current['events'])
                    if prior.get('void') is not None:
                        equal(prior['void'], current.get('void'))
    body = dict(version=VERSION, scope=SCOPE, pool=pool, risk_policy=policy,
                accounts=deepcopy(accounts), retention_authorized=False,
                external_submission_allowed=False)
    result = {**body, 'sha256': sha(body)}
    if len(encoded(result)) > MAX_BYTES:
        raise ValueError('Whole-pool archive exceeds bounds')
    return result


def verify(value):
    keys = {'version', 'scope', 'pool', 'risk_policy', 'accounts', 'retention_authorized', 'external_submission_allowed', 'sha256'}
    if not isinstance(value, dict) or set(value) != keys or len(encoded(value)) > MAX_BYTES:
        raise ValueError('Invalid bounded whole-pool archive')
    expected = seal(value['pool'], value['risk_policy'], value['accounts'])
    equal(value, expected)
    return deepcopy(expected)


def capture(engine, pool_id, *, authorized_accounts=None):
    with journal.transaction(engine) as db:
        db.execute(text("SET LOCAL lock_timeout='2000ms'"))
        definition = pools.check(db, pools.lock(db, pool_id))
        pools.authorize(definition, authorized_accounts)
        reports = pools.journals(db, definition)
        policy = pool_risk.check(db, pool_id)
        accounts = []
        total = len(encoded(reports)) + len(encoded(definition)) + len(encoded(policy))
        for allocation, report in zip(definition['allocations'], reports):
            row = journal.lock(db, allocation['account_id'])
            financial = report['journal']
            controlled = controls.check(db, row, financial)
            account = dict(account_id=row.account_id,
                                 operational=operational.snapshot(db, row, financial, controlled, include_receipts=True),
                                 admissions=admission.check(db, row, financial, controlled))
            total += len(encoded(account))
            if total > MAX_BYTES:
                raise ValueError('Whole-pool archive input exceeds bounds')
            accounts.append(account)
        return seal(pools.evaluate(definition, reports, ownership.clock(db)), policy, accounts)
