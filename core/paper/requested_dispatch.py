"""Unique local Paper submission evidence; no external send/query/retry."""
from copy import deepcopy
from sqlalchemy import select,text
from core.paper import requested_journal as journal,requested_sources as sources,requested_ownership as ownership,requested_recovery as recovery
from core.paper.fault_adapter import sha,encoded
from core.storage.models import RequestedPaperDispatchRecord as Dispatch,RequestedPaperEventRecord as Event,RequestedPaperRequestRecord as Request,RequestedPaperClaimRecord as Claim

VERSION='paper-requested-local-dispatch-v1'
EXPORT_VERSION='paper-requested-dispatch-export-v1'


def declare(event,token):
    return VERSION if event['kind']=='SUBMIT' and token is not None else None


def payload(request,event,receipt,claim,accepted_us):
    if event['kind']!='SUBMIT' or event['sequence']!=0 or receipt is None:
        raise ValueError('Declared dispatch requires owned local SUBMIT receipt')
    return {'version':VERSION,'account_id':request['account_id'],'request_id':request['request_id'],
            'client_id':sha({'version':VERSION,'request_id':request['request_id']}),
            'request_sha256':sha(request),'submit_event_sha256':sha(event),'source_receipt_sha256':sha(receipt),
            'original_owner':claim['owner'],'original_token':claim['token'],'claim_sha256':sha(claim),
            'recorded_us':accepted_us,'mode':'LOCAL_PAPER_ONLY','external_submission_allowed':False}


def record(db,row,request,event,receipt):
    claim=db.scalar(select(Claim).where(Claim.request_id==request['request_id'],Claim.token==event.ownership_token))
    if claim is None:raise ValueError('Dispatch has no original ownership claim')
    value=payload(request,event.payload,receipt.payload if receipt is not None else None,claim.payload,event.ownership_accepted_us)
    db.add(Dispatch(request_id=request['request_id'],account_id=row.account_id,token=value['original_token'],client_id=value['client_id'],payload=value,payload_sha256=sha(value)))
    db.flush()


def replay(financial,source_slots,claims,event_tokens,slots):
    if not isinstance(slots,list) or len(slots)!=len(financial['requests']):raise ValueError('Incomplete dispatch coverage')
    receipts={(value['request_id'],value['sequence']):value['receipt'] for value in source_slots}
    tokens={(value['request_id'],value['sequence']):value for value in event_tokens}
    leases={(value['request_id'],value['token']):value for value in claims}
    for entry,slot in zip(financial['requests'],slots):
        request=entry['request'];request_id=request['request_id'];events=entry['events']
        if not isinstance(slot,dict) or set(slot)!={'request_id','dispatch_version','dispatch'} or slot['request_id']!=request_id:
            raise ValueError('Dispatch request coverage differs')
        if slot['dispatch_version'] is None:
            if slot['dispatch'] is not None:raise ValueError('Undeclared dispatch evidence')
            continue
        if slot['dispatch_version']!=VERSION or not events or not isinstance(slot['dispatch'],dict):raise ValueError('Missing/unsupported declared dispatch')
        token=tokens[(request_id,0)];claim=leases.get((request_id,token['token']))
        if claim is None:raise ValueError('Dispatch lacks original fenced event')
        expected=payload(request,events[0],receipts[(request_id,0)],claim,token['accepted_us'])
        if encoded(slot['dispatch'])!=encoded(expected):raise ValueError('Dispatch differs from original submission evidence')
    return deepcopy(slots)


def check(db,row,financial,controlled):
    source_slots=sources.check(db,row,financial,controlled);claims,tokens=ownership.check(db,row,financial,controlled)
    saved=list(db.scalars(select(Dispatch).where(Dispatch.account_id==row.account_id).limit(journal.REQUEST_LIMIT+1)))
    if len(saved)>journal.REQUEST_LIMIT:raise ValueError('Dispatch capacity exceeded')
    lookup={value.request_id:value for value in saved};slots=[]
    events=list(db.scalars(select(Event).join(Request,Request.request_id==Event.request_id).where(Request.account_id==row.account_id).limit(journal.TOTAL_EVENTS+1)))
    markers={value.request_id:value.dispatch_version for value in events if value.sequence==0}
    if any(value.dispatch_version is not None and value.sequence!=0 for value in events):raise ValueError('Dispatch declaration outside SUBMIT')
    for entry in financial['requests']:
        request_id=entry['request']['request_id'];value=lookup.pop(request_id,None)
        if value is not None and (sha(value.payload)!=value.payload_sha256 or value.request_id!=value.payload.get('request_id') or value.client_id!=value.payload.get('client_id') or type(value.payload.get('original_token')) is not int or value.token!=value.payload['original_token'] or value.account_id!=value.payload.get('account_id')):
            raise ValueError('Dispatch metadata/hash differs')
        slots.append({'request_id':request_id,'dispatch_version':markers.get(request_id),'dispatch':deepcopy(value.payload) if value is not None else None})
    if lookup:raise ValueError('Orphan dispatch evidence')
    return replay(financial,source_slots,claims,tokens,slots)


def evaluate(ownership_report,slots):
    report=ownership.verify(ownership_report);source=sources.verify(report['source_evidence']);financial=source['journal']['journal']
    slots=replay(financial,source['sources'],report['claims'],report['event_tokens'],slots)
    original=recovery.evaluate(report['source_evidence']);assessments=[]
    for row,slot in zip(original['requests'],slots):
        action=row['action'] if slot['dispatch'] is not None else 'LOCAL_VOIDED' if row['action']=='LOCAL_VOIDED' else 'NOT_DISPATCHED_LOCAL' if row['event_count']==0 else 'DISPATCH_PROVENANCE_UNAVAILABLE'
        assessments.append({'request_id':row['request_id'],'action':action,'state':row['state'],
                            'client_id':slot['dispatch']['client_id'] if slot['dispatch'] else None,
                            'funding':deepcopy(row['funding']),'resubmission_allowed':False,'external_query_supported':False})
    body={'version':EXPORT_VERSION,'ownership_evidence':report,'dispatches':slots,'recovery':assessments,
          'read_only':True,'external_submission_allowed':False}
    result={**body,'sha256':sha(body)}
    if len(encoded(result).encode())>sources.contract.MAX_BYTES:raise ValueError('Dispatch export exceeds32MiB')
    return result


def capture(engine,account_id):
    with journal.transaction(engine) as db:
        db.execute(text("SELECT set_config('lock_timeout','2000ms',true)"))
        row=journal.lock(db,account_id);financial=journal.audit(db,row);controlled=ownership.controls.check(db,row,financial)
        source_slots=sources.check(db,row,financial,controlled);claims,tokens=ownership.check(db,row,financial,controlled)
        source={'version':sources.EXPORT_VERSION,'journal':sources.inspection.seal(financial),'controls':controlled,'sources':source_slots}
        owned=ownership.evaluate({**source,'sha256':sha(source)},claims,tokens,ownership.clock(db))
        return evaluate(owned,check(db,row,financial,controlled))


def verify(report):
    keys={'version','ownership_evidence','dispatches','recovery','read_only','external_submission_allowed','sha256'}
    if not isinstance(report,dict) or set(report)!=keys or report['version']!=EXPORT_VERSION or len(encoded(report).encode())>sources.contract.MAX_BYTES:
        raise ValueError('Invalid dispatch report')
    expected=evaluate(report['ownership_evidence'],report['dispatches'])
    if encoded(report)!=encoded(expected):raise ValueError('Dispatch report differs from replay')
    return deepcopy(expected)
