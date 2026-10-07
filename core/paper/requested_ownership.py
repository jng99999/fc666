"""PostgreSQL-only local Paper leases; no remote submission or transport."""
from copy import deepcopy
import re
from sqlalchemy import select, text
from core.paper import requested_journal as journal, requested_controls as controls, requested_sources as sources
from core.paper.fault_adapter import encoded, sha
from core.storage.models import RequestedPaperClaimRecord as Claim, RequestedPaperEventRecord as Event, RequestedPaperRequestRecord as Request

VERSION = 'paper-requested-ownership-v1'
CLAIM_LIMIT = 64


def clock(db):
    return db.scalar(text("SELECT (extract(epoch FROM clock_timestamp()) * 1000000)::bigint"))


def guard(db, request_id, token):
    last = db.scalar(select(Claim).where(Claim.request_id == request_id).order_by(Claim.token.desc()).limit(1))
    if last is None:
        if token is not None: raise ValueError('Unknown ownership token')
    elif type(token) is not int or token != last.token:
        raise ValueError('Owned request requires current fencing token')


def replay(financial, controlled, claims, event_tokens):
    if not isinstance(claims, list) or len(claims) > journal.REQUEST_LIMIT * CLAIM_LIMIT:
        raise ValueError('Ownership history exceeds capacity')
    if not isinstance(event_tokens, list) or len(event_tokens) != financial['total_events']:
        raise ValueError('Incomplete ownership event evidence')
    by_request = {}; known = {entry['request']['request_id'] for entry in financial['requests']}
    for value in claims:
        keys = {'version','account_id','request_id','owner','token','acquired_us','expires_us','journal_revision','control_revision'}
        if (not isinstance(value,dict) or set(value) != keys or value['version'] != VERSION
                or value['account_id'] != financial['opening']['account_id'] or value['request_id'] not in known
                or not isinstance(value['owner'],str) or not 1 <= len(value['owner']) <= 128
                or any(type(value[k]) is not int for k in ['token','acquired_us','expires_us','journal_revision','control_revision'])
                or not 0 < value['acquired_us'] < value['expires_us'] <= 2**63-1 or not 1000000 <= value['expires_us']-value['acquired_us'] <= 60000000
                or not 0 <= value['journal_revision'] <= financial['revision']
                or not 1 <= value['control_revision'] <= controlled['revision'] or controlled['coverage'] != 'CONTROLLED'):
            raise ValueError('Invalid ownership claim')
        history = by_request.setdefault(value['request_id'],[])
        if value['token'] != len(history)+1 or len(history) >= CLAIM_LIMIT:
            raise ValueError('Ownership token gap/capacity')
        if history and (value['acquired_us'] < history[-1]['expires_us'] or value['journal_revision'] < history[-1]['journal_revision']):
            raise ValueError('Ownership takeover precedes expiry/checkpoint')
        if controlled['records'][value['control_revision']-1]['journal_revision'] > value['journal_revision']:
            raise ValueError('Ownership control checkpoint is from the future')
        history.append(value)
    if claims != sorted(claims,key=lambda value:(value['request_id'],value['token'])):
        raise ValueError('Noncanonical ownership history')
    ordinal = 0; prepared_revision = 0
    for entry in financial['requests']:
        request_id = entry['request']['request_id']; history = by_request.get(request_id,[]); prepared_revision += 1
        if history and history[0]['journal_revision'] != prepared_revision:
            raise ValueError('Retrospective ownership enrollment')
        if history and history[-1]['journal_revision'] > prepared_revision+len(entry['events']):
            raise ValueError('Ownership after request closure or later request')
        previous_us = 0
        for sequence,value in enumerate(entry['events']):
            slot = event_tokens[ordinal]; ordinal += 1
            keys = {'request_id','sequence','token','accepted_us'}
            if not isinstance(slot,dict) or set(slot)!=keys or slot['request_id'] != request_id or type(slot['sequence']) is not int or slot['sequence'] != sequence:
                raise ValueError('Ownership event identity differs')
            if not history:
                if slot['token'] is not None or slot['accepted_us'] is not None: raise ValueError('Event lacks ownership history')
                continue
            available = [claim for claim in history if claim['journal_revision'] <= prepared_revision+sequence]
            if not available or type(slot['token']) is not int or slot['token'] != available[-1]['token'] or type(slot['accepted_us']) is not int:
                raise ValueError('Event has stale/missing fencing evidence')
            claim = available[-1]
            if not claim['acquired_us'] <= slot['accepted_us'] < claim['expires_us'] or slot['accepted_us'] < previous_us:
                raise ValueError('Event accepted outside ownership lease')
            previous_us = slot['accepted_us']
        prepared_revision += len(entry['events']) + int(entry.get('void') is not None)
    return deepcopy(claims), deepcopy(event_tokens)


def check(db,row,financial,controlled):
    saved = list(db.scalars(select(Claim).where(Claim.account_id==row.account_id).order_by(Claim.request_id,Claim.token).limit(journal.REQUEST_LIMIT*CLAIM_LIMIT+1)))
    claims = []
    for value in saved:
        if (value.request_id != value.payload.get('request_id') or value.token != value.payload.get('token')
                or value.account_id != value.payload.get('account_id') or sha(value.payload) != value.payload_sha256):
            raise ValueError('Ownership metadata/hash mismatch')
        claims.append(value.payload)
    saved_events = list(db.scalars(select(Event).join(Request,Request.request_id==Event.request_id).where(Request.account_id==row.account_id).order_by(Request.ordinal,Event.sequence).limit(journal.TOTAL_EVENTS+1)))
    if any(value.ownership_token is not None and value.source_version != sources.VERSION for value in saved_events):
        raise ValueError('Owned event requires immutable local source evidence')
    tokens = [{'request_id':value.request_id,'sequence':value.sequence,'token':value.ownership_token,'accepted_us':value.ownership_accepted_us} for value in saved_events]
    return replay(financial,controlled,claims,tokens)


def _owned(db, request_id, owner, token):
    guard(db,request_id,token)
    value = db.scalar(select(Claim).where(Claim.request_id==request_id,Claim.token==token))
    now=clock(db)
    if value is None or value.payload['owner'] != owner or not value.payload['acquired_us'] <= now < value.payload['expires_us']:
        raise ValueError('Expired or fenced owner')
    return value.payload


def claim(engine,account_id,request_id,owner,ttl_seconds=30,*,expected_financial_revision=None,expected_control_revision=None,expected_token=None):
    if not isinstance(owner,str) or not 1<=len(owner)<=128 or type(ttl_seconds) is not int or not 1<=ttl_seconds<=60:
        raise ValueError('Invalid owner/duration')
    fences=(expected_financial_revision,expected_control_revision,expected_token)
    fenced=any(value is not None for value in fences)
    if fenced and (any(type(value) is not int for value in fences) or expected_financial_revision<1 or expected_control_revision<1 or not 0<=expected_token<=CLAIM_LIMIT):
        raise ValueError('Complete strict claim fences required')
    with journal.transaction(engine) as db:
        db.execute(text("SELECT set_config('lock_timeout','2000ms',true)"))
        row=journal.lock(db,account_id); financial=journal.audit(db,row); controlled=controls.check(db,row,financial)
        if controlled['coverage'] != 'CONTROLLED' or row.active_request_id != request_id:
            raise ValueError('Active controlled request required')
        entry=next(value for value in financial['requests'] if value['request']['request_id']==request_id)
        saved,_=check(db,row,financial,controlled); history=[value for value in saved if value['request_id']==request_id]
        now=clock(db)
        last=history[-1] if history else None
        if fenced:
            # Only an exact retry of the current unexpired claim may use original checkpoints.
            retry=(last is not None and last['owner']==owner and last['acquired_us']<=now<last['expires_us']
                   and expected_token+1==last['token'] and expected_financial_revision==last['journal_revision']
                   and expected_control_revision==last['control_revision'] and ttl_seconds*1000000==last['expires_us']-last['acquired_us'])
            if retry:return deepcopy(last)
            if (expected_financial_revision!=financial['revision'] or expected_control_revision!=controlled['revision']
                    or expected_token!=(last['token'] if last else 0)):
                raise ValueError('Claim checkpoint/token conflict')
            if last is not None and last['expires_us']>now and ttl_seconds*1000000!=last['expires_us']-last['acquired_us']:
                raise ValueError('Active claim duration cannot change')
        if history and now < history[-1]['acquired_us']: raise ValueError('Database clock regressed')
        if history and history[-1]['expires_us'] > now:
            if history[-1]['owner'] != owner: raise ValueError('Request already owned')
            return deepcopy(history[-1])
        if not history and entry['events']: raise ValueError('Submitted history cannot gain retrospective ownership')
        if len(history)>=CLAIM_LIMIT: raise ValueError('Ownership capacity exhausted')
        value={'version':VERSION,'account_id':account_id,'request_id':request_id,'owner':owner,'token':len(history)+1,
               'acquired_us':now,'expires_us':now+ttl_seconds*1000000,'journal_revision':financial['revision'],'control_revision':controlled['revision']}
        db.add(Claim(request_id=request_id,token=value['token'],account_id=account_id,payload=value,payload_sha256=sha(value)))
        db.flush();journal.audit(db,row)
        _owned(db,request_id,owner,value['token'])
        return deepcopy(value)


def deliver(engine,proposal,preview_sha256,owner,token):
    if (not isinstance(proposal,dict) or set(proposal)!=sources.preview.KEYS or not isinstance(proposal['event'],dict)
            or not isinstance(preview_sha256,str) or re.fullmatch('[0-9a-f]{64}',preview_sha256) is None):
        raise ValueError('Exact source proposal and digest required')
    with journal.transaction(engine) as db:
        db.execute(text("SELECT set_config('lock_timeout','2000ms',true)"))
        row=journal.lock(db,proposal['account_id']); financial=journal.audit(db,row); controlled=controls.check(db,row,financial)
        _owned(db,proposal['request_id'],owner,token)
        result=sources._accept(db,row,financial,controlled,proposal,preview_sha256,ownership_token=token)
        _owned(db,proposal['request_id'],owner,token)
        return result


def evaluate(source_report,claims,event_tokens,observed_us):
    evidence=sources.verify(source_report); financial=evidence['journal']['journal']; controlled=evidence['controls']
    claims,event_tokens=replay(financial,controlled,claims,event_tokens)
    if type(observed_us) is not int or not 0<observed_us<=2**63-1 or any(c['acquired_us']>observed_us for c in claims) or any(t['accepted_us'] is not None and t['accepted_us']>observed_us for t in event_tokens):
        raise ValueError('Invalid ownership observation clock')
    for token,source in zip(event_tokens,evidence['sources']):
        if token['token'] is not None and source['receipt'] is None: raise ValueError('Owned event lacks source receipt')
    leases=[]
    for entry in financial['requests']:
        request_id=entry['request']['request_id']; history=[c for c in claims if c['request_id']==request_id]
        last=history[-1] if history else None
        leases.append({'request_id':request_id,'coverage':'LOCAL_LEASED' if last else 'UNOWNED',
                       'latest_claim':deepcopy(last),'unexpired_at_observation':last is not None and last['expires_us']>observed_us,
                       'active_request':request_id==financial['active_request_id']})
    body={'version':VERSION,'source_evidence':deepcopy(source_report),'claims':claims,'event_tokens':event_tokens,
          'observed_us':observed_us,'leases':leases,'resubmission_allowed':False,'external_submission_allowed':False}
    report={**body,'sha256':sha(body)}
    if len(encoded(report).encode())>sources.contract.MAX_BYTES: raise ValueError('Ownership export exceeds32MiB')
    return report


def capture(engine,account_id):
    with journal.transaction(engine) as db:
        db.execute(text("SELECT set_config('lock_timeout','2000ms',true)"))
        row=journal.lock(db,account_id); financial=journal.audit(db,row); controlled=controls.check(db,row,financial)
        slots=sources.check(db,row,financial,controlled); claims,tokens=check(db,row,financial,controlled)
        body={'version':sources.EXPORT_VERSION,'journal':sources.inspection.seal(financial),'controls':controlled,'sources':slots}
        return evaluate({**body,'sha256':sha(body)},claims,tokens,clock(db))


def verify(report):
    keys={'version','source_evidence','claims','event_tokens','observed_us','leases','resubmission_allowed','external_submission_allowed','sha256'}
    if not isinstance(report,dict) or set(report)!=keys or report['version']!=VERSION or len(encoded(report).encode())>sources.contract.MAX_BYTES:
        raise ValueError('Invalid ownership export')
    expected=evaluate(report['source_evidence'],report['claims'],report['event_tokens'],report['observed_us'])
    if encoded(report)!=encoded(expected): raise ValueError('Ownership export differs from replay')
    return deepcopy(expected)
