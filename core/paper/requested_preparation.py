"""Atomic server-recomputed preview acceptance; local reservation only."""
from copy import deepcopy
import re
from sqlalchemy import text
from core.paper import requested_journal as journal,requested_controls as controls,requested_preview as preview,requested_inspection as inspection
from core.paper.fault_adapter import encoded

VERSION='paper-requested-preparation-command-result-v1'


def prefix(financial,count):
    entries=deepcopy(financial['requests'][:count])
    v2=any(entry.get('void') is not None for entry in entries)
    if not v2:
        for entry in entries:entry.pop('void',None)
    return inspection.replay(financial['opening'],entries,version=journal.VERSION_V2 if v2 else journal.VERSION)


def historical(financial,controlled,proposal,index):
    entry=financial['requests'][index];request_id=entry['request']['request_id']
    admitted=next((gate for gate in controlled['gates'] if gate['request_id']==request_id and gate['phase']=='PREPARE'),None)
    if admitted is None:raise ValueError('Request lacks controlled preparation evidence')
    if (type(proposal['expected_control_revision']) is not int or proposal['expected_control_revision']!=admitted['control_revision'] or
        type(proposal['expected_financial_revision']) is not int or proposal['expected_financial_revision']!=admitted['journal_revision']):
        raise ValueError('Conflicting preparation checkpoint retry')
    before=prefix(financial,index);identities={item['request']['request_id'] for item in before['requests']}
    old=controls.replay(controls.VERSION,controlled['policy'],controlled['records'][:admitted['control_revision']],
                        [gate for gate in controlled['gates'] if gate['request_id'] in identities],before)
    result=preview.evaluate(before,old,proposal)
    if encoded(result['request'])!=encoded(entry['request']):raise ValueError('Conflicting preparation request retry')
    return result


def prepare(engine,proposal,preview_sha256,*,health_cache=None):
    if not isinstance(proposal,dict) or set(proposal)!=preview.PROPOSAL_KEYS or not isinstance(preview_sha256,str) or re.fullmatch('[0-9a-f]{64}',preview_sha256) is None:
        raise ValueError('Exact proposal and preview digest required')
    with journal.transaction(engine) as db:
        db.execute(text("SELECT set_config('lock_timeout', '2000ms', true)"))
        row=journal.lock(db,proposal['account_id']);financial=journal.audit(db,row);controlled=controls.check(db,row,financial)
        if controlled['coverage']!='CONTROLLED':raise ValueError('Explicit controlled account required')
        existing=next((index for index,entry in enumerate(financial['requests']) if entry['request']['client_request_id']==proposal['client_request_id']),None)
        report=preview.evaluate(financial,controlled,proposal) if existing is None else historical(financial,controlled,proposal,existing)
        if report['sha256']!=preview_sha256:raise ValueError('Preview does not match audited preparation checkpoint')
        if existing is None:journal._prepare(db,row,financial,report['request'],health_cache=health_cache)
        return {'version':VERSION,'request':deepcopy(report['request']),'accepted_preview_sha256':preview_sha256,
                'initial_funding':deepcopy(report['projected_summary']['funding']),'preparation_committed':True,'external_submission_allowed':False}
