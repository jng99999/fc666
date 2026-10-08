"""Atomic pool-aware local BUY preparation; never external submission."""
from copy import deepcopy
import re
from sqlalchemy import select,text
from core.storage.models import PaperCapitalAdmissionRecord as Admission,PaperCapitalMemberRecord as Member
from core.paper import shared_capital_pool as pools,shared_capital as capital,requested_journal as journal,requested_controls as controls,requested_preview as preview,requested_preparation as preparation
from core.paper.fault_adapter import sha,encoded
from core.paper.requested_execution import MAX_BYTES

VERSION='paper-capital-admission-v1'


def evaluate(pool_capture,local_preview):
    pool=pools.verify(pool_capture);local=preview.verify(local_preview)
    account=local['proposal']['account_id'];financial=next((report for report in pool['preview']['journals'] if report['journal']['opening']['account_id']==account),None)
    forecast=pool['preview']['forecast']
    if financial is None or encoded(financial)!=encoded(local['journal']) or forecast is None or not forecast['capital_forecast_allowed'] or encoded(forecast['request'])!=encoded(local['request']):raise ValueError('Pool/local funding admission differs or denied')
    body={'version':VERSION,'pool_capture':pool,'local_preview':local,'request_sha256':sha(local['request']),
          'shared_reservation_committed':True,'external_submission_allowed':False}
    result={**body,'sha256':sha(body)}
    if len(encoded(result).encode())>MAX_BYTES:raise ValueError('Shared admission exceeds32MiB')
    return result


def verify(value):
    if not isinstance(value,dict) or set(value)!={'version','pool_capture','local_preview','request_sha256','shared_reservation_committed','external_submission_allowed','sha256'} or value['version']!=VERSION or len(encoded(value).encode())>MAX_BYTES:raise ValueError('Invalid shared admission')
    expected=evaluate(value['pool_capture'],value['local_preview'])
    if encoded(expected)!=encoded(value):raise ValueError('Shared admission differs from replay')
    return expected


def guard(db,row,request,receipt):
    member=db.get(Member,row.account_id)
    if member is None:
        if receipt is not None:raise ValueError('Unpooled account cannot gain pool admission')
        return
    if receipt is None:raise ValueError('Pool member requires atomic pool admission')
    value=verify(receipt)
    if value['pool_capture']['definition']['pool_id']!=member.pool_id or encoded(value['local_preview']['request'])!=encoded(request):raise ValueError('Shared admission identity differs')


def insert(db,row,request,value):
    db.add(Admission(request_id=request['request_id'],account_id=row.account_id,pool_id=value['pool_capture']['definition']['pool_id'],payload=deepcopy(value),payload_sha256=value['sha256']))


def check(db,row,financial,controlled):
    member=db.get(Member,row.account_id)
    stored_bytes=db.scalar(text('SELECT COALESCE(sum(octet_length(payload::text)),0) FROM paper_capital_admissions WHERE account_id=:account_id'),{'account_id':row.account_id})
    if stored_bytes>MAX_BYTES:raise ValueError('Stored shared admission evidence exceeds32MiB')
    saved=list(db.scalars(select(Admission).where(Admission.account_id==row.account_id).limit(journal.REQUEST_LIMIT+1)))
    if member is None:
        if saved:raise ValueError('Orphan shared admission')
        return []
    pool_row=db.get(pools.Pool,member.pool_id)
    if pool_row is None:raise ValueError('Missing declared pool')
    definition=pools.check(db,pool_row);lookup={value.request_id:value for value in saved};values=[]
    if len(saved)!=len(financial['requests']):raise ValueError('Incomplete mandatory shared admissions')
    for index,entry in enumerate(financial['requests']):
        stored=lookup.pop(entry['request']['request_id'],None)
        if stored is None:raise ValueError('Missing mandatory shared admission')
        value=verify(stored.payload);local=value['local_preview'];prior=preparation.prefix(financial,index)
        if stored.pool_id!=member.pool_id or stored.payload_sha256!=value['sha256'] or encoded(value['pool_capture']['definition'])!=encoded(definition) or encoded(local['request'])!=encoded(entry['request']) or encoded(local['journal']['journal'])!=encoded(prior):raise ValueError('Shared admission storage/prefix differs')
        if encoded(local['controls']['records'])!=encoded(controlled['records'][:local['controls']['revision']]):raise ValueError('Shared admission control prefix differs')
        values.append(value)
    if lookup:raise ValueError('Orphan shared admission request')
    return values


def prepare(engine,pool_id,proposal,preview_sha256,*,authorized_accounts=None):
    if not isinstance(proposal,dict) or set(proposal)!=preview.PROPOSAL_KEYS or type(preview_sha256) is not str or re.fullmatch('[0-9a-f]{64}',preview_sha256) is None:raise ValueError('Exact local proposal/digest required')
    with journal.transaction(engine) as db:
        db.execute(text("SELECT set_config('lock_timeout','2000ms',true)"))
        definition=pools.check(db,pools.lock(db,pool_id));pools.authorize(definition,authorized_accounts)
        reports=pools.journals(db,definition)
        financial=next((report['journal'] for report in reports if report['journal']['opening']['account_id']==proposal['account_id']),None)
        if financial is None:raise ValueError('Account not in pool')
        row=journal.lock(db,proposal['account_id']);controlled=controls.check(db,row,financial)
        history=check(db,row,financial,controlled)
        old=next((value for value in history if value['local_preview']['proposal']['client_request_id']==proposal['client_request_id']),None)
        if old is not None:
            if encoded(old['local_preview']['proposal'])!=encoded(proposal) or old['local_preview']['sha256']!=preview_sha256:raise ValueError('Conflicting shared preparation retry')
            return old
        from core.paper import requested_capacity
        requested_capacity.require_new(financial,journal.TOTAL_EVENTS)
        local=preview.evaluate(financial,controlled,proposal)
        if local['sha256']!=preview_sha256:raise ValueError('Local preparation checkpoint differs')
        candidate={key:proposal[key] for key in capital.CANDIDATE_KEYS}
        captured=pools.evaluate(definition,reports,pools.ownership.clock(db),candidate)
        value=evaluate(captured,local)
        journal._prepare(db,row,financial,local['request'],pool_admission=value)
        return value


PREVIEW_VERSION='paper-capital-preparation-preview-v1'


def evaluate_preview(pool_capture,local_preview):
    pool=pools.verify(pool_capture);local=preview.verify(local_preview)
    account=local['proposal']['account_id'];financial=next((report for report in pool['preview']['journals'] if report['journal']['opening']['account_id']==account),None)
    forecast=pool['preview']['forecast']
    if financial is None or encoded(financial)!=encoded(local['journal']) or forecast is None or encoded(forecast['request'])!=encoded(local['request']):raise ValueError('Pool/local preview differs')
    body={'version':PREVIEW_VERSION,'pool_capture':pool,'local_preview':local,'capital_forecast_allowed':forecast['capital_forecast_allowed'],
          'shared_reservation_committed':False,'submission_allowed':False,'read_only':True}
    result={**body,'sha256':sha(body)}
    if len(encoded(result).encode())>MAX_BYTES:raise ValueError('Pool preparation preview exceeds32MiB')
    return result


def capture_preview(engine,pool_id,proposal,*,authorized_accounts=None):
    if not isinstance(proposal,dict) or set(proposal)!=preview.PROPOSAL_KEYS:raise ValueError('Exact local proposal required')
    with journal.transaction(engine) as db:
        db.execute(text("SELECT set_config('lock_timeout','2000ms',true)"))
        definition=pools.check(db,pools.lock(db,pool_id));pools.authorize(definition,authorized_accounts)
        reports=pools.journals(db,definition)
        financial=next((report['journal'] for report in reports if report['journal']['opening']['account_id']==proposal['account_id']),None)
        if financial is None:raise ValueError('Account not in pool')
        row=journal.lock(db,proposal['account_id']);controlled=controls.check(db,row,financial)
        from core.paper import requested_capacity
        requested_capacity.require_new(financial,journal.TOTAL_EVENTS)
        local=preview.evaluate(financial,controlled,proposal)
        candidate={key:proposal[key] for key in capital.CANDIDATE_KEYS}
        captured=pools.evaluate(definition,reports,pools.ownership.clock(db),candidate)
        return evaluate_preview(captured,local)


def verify_preview(report):
    keys={'version','pool_capture','local_preview','capital_forecast_allowed','shared_reservation_committed','submission_allowed','read_only','sha256'}
    if not isinstance(report,dict) or set(report)!=keys or report['version']!=PREVIEW_VERSION or len(encoded(report).encode())>MAX_BYTES:raise ValueError('Invalid pool preparation preview')
    expected=evaluate_preview(report['pool_capture'],report['local_preview'])
    if encoded(expected)!=encoded(report):raise ValueError('Pool preparation preview differs from replay')
    return expected
