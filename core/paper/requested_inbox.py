"""Durable bounded local input staging. No ledger application or remote provenance."""
from copy import deepcopy
from typing import Literal
from pydantic import BaseModel,ConfigDict,Field,StrictInt
from sqlalchemy import select,text
from core.paper import requested_execution as execution,requested_journal as journal,requested_controls as controls,requested_ownership as ownership
from core.paper.fault_adapter import encoded,sha
from core.storage.models import RequestedPaperInboxRecord as Inbox,RequestedPaperRequestRecord as Request,RequestedPaperClaimRecord as Claim,RequestedPaperControlRecord as Control,RequestedPaperAccountRecord as Account

VERSION='paper-requested-inbox-v1'
LIMIT=128
MAX_ITEM_BYTES=8192
MAX_BYTES=1024*1024


class InputEvent(BaseModel):
    model_config=ConfigDict(extra='forbid',strict=True)
    event_id:str=Field(min_length=1,max_length=128)
    request_id:str=Field(pattern=r'^[0-9a-f]{64}$')
    sequence:StrictInt=Field(ge=0,le=1_000_000_000)
    received_at:str=Field(min_length=1,max_length=64)
    kind:Literal['SUBMIT','ACK','UNKNOWN_SUBMISSION','CANCEL_REQUEST','CANCEL_ACK','REJECT','FILL','RECEIPT','SEAL']
    payload:dict=Field(max_length=6)


class InputSource(BaseModel):
    model_config=ConfigDict(extra='forbid',strict=True)
    kind:Literal['LOCAL_PAPER_OPERATOR_INPUT']
    source_id:str=Field(min_length=1,max_length=128)


class Stage(BaseModel):
    model_config=ConfigDict(extra='forbid',strict=True)
    account_id:str=Field(min_length=1,max_length=128)
    request_id:str=Field(pattern=r'^[0-9a-f]{64}$')
    expected_financial_revision:StrictInt=Field(ge=1)
    owner:str=Field(min_length=1,max_length=128)
    ownership_token:StrictInt=Field(ge=1,le=64)
    source:InputSource
    event:InputEvent


def validate(value):
    value=Stage.model_validate(value).model_dump()
    event=value['event'];payload=event['payload'];execution.clock(event['received_at'])
    if event['request_id']!=value['request_id']:raise ValueError('Inbox request identity mismatch')
    if event['kind']=='FILL':
        if set(payload)!={'fill_id','quantity','price','fee','executed_at'} or not isinstance(payload['fill_id'],str) or not 1<=len(payload['fill_id'])<=128:
            raise ValueError('Invalid staged fill shape')
        for key in ['quantity','price']:execution.decimal(payload[key],asset=True,positive=True)
        execution.decimal(payload['fee'])
        if execution.clock(payload['executed_at'])>execution.clock(event['received_at']):raise ValueError('Staged execution follows receipt')
    elif event['kind'] in ['RECEIPT','SEAL']:
        if set(payload)!=({'quantity','fees','notional','last_sequence'} if event['kind']=='SEAL' else {'quantity','fees','notional'}):raise ValueError('Invalid staged cumulative shape')
        for key in ['quantity','fees','notional']:execution.decimal(payload[key])
        if event['kind']=='SEAL' and (type(payload['last_sequence']) is not int or payload['last_sequence']!=event['sequence']-1):raise ValueError('Invalid staged seal sequence')
    elif payload:raise ValueError('Unexpected staged event payload')
    if event['kind']=='SUBMIT' and event['sequence']!=0:raise ValueError('Invalid staged submit sequence')
    if len(encoded(value).encode())>MAX_ITEM_BYTES:raise ValueError('Staged input exceeds8KiB')
    return value


def verify(receipt):
    keys={'version','ordinal','proposal','request_sha256','control_revision','claim_sha256','received_us','state','staging_committed','event_committed','financial_application_verified','external_submission_allowed','sha256'}
    if not isinstance(receipt,dict) or set(receipt)!=keys or receipt['version']!=VERSION or len(encoded(receipt).encode())>MAX_ITEM_BYTES:
        raise ValueError('Invalid inbox receipt bounds/version')
    value=validate(receipt['proposal'])
    if (type(receipt['ordinal']) is not int or not 0<=receipt['ordinal']<LIMIT or type(receipt['received_us']) is not int or not 0<receipt['received_us']<2**63
        or type(receipt['control_revision']) is not int or receipt['control_revision']<1
        or any(not isinstance(receipt[key],str) or len(receipt[key])!=64 or any(c not in '0123456789abcdef' for c in receipt[key]) for key in ['request_sha256','claim_sha256'])
        or receipt['state']!='STAGED_UNAPPLIED' or receipt['staging_committed'] is not True or receipt['event_committed'] is not False
        or receipt['financial_application_verified'] is not False or receipt['external_submission_allowed'] is not False
        or encoded(value)!=encoded(receipt['proposal']) or receipt['sha256']!=sha({k:v for k,v in receipt.items() if k!='sha256'})):
        raise ValueError('Inbox receipt differs from staging contract')
    return deepcopy(receipt)


def records(db,account_id):
    size=db.scalar(text('SELECT COALESCE(sum(octet_length(payload::text)),0) FROM requested_paper_inbox WHERE account_id=:account'),{'account':account_id})
    if size>MAX_BYTES:raise ValueError('Inbox exceeds1MiB')
    saved=list(db.scalars(select(Inbox).where(Inbox.account_id==account_id).order_by(Inbox.ordinal).limit(LIMIT+1)))
    if len(saved)>LIMIT:raise ValueError('Inbox count exceeds128')
    for ordinal,row in enumerate(saved):
        value=verify(row.payload);proposal=value['proposal'];event=proposal['event']
        request=db.get(Request,row.request_id);claim=db.get(Claim,(row.request_id,proposal['ownership_token']))
        control=db.get(Control,(account_id,value['control_revision']-1));account=db.get(Account,account_id)
        if (request is None or claim is None or control is None or account is None or request.account_id!=account_id
            or not claim.payload['journal_revision']<=proposal['expected_financial_revision']<=account.revision
            or control.payload['journal_revision']>proposal['expected_financial_revision'] or value['control_revision']<claim.payload['control_revision'] or request.payload_sha256!=sha(request.payload)
            or value['request_sha256']!=sha(request.payload) or value['claim_sha256']!=sha(claim.payload)
            or claim.payload['owner']!=proposal['owner'] or not claim.payload['acquired_us']<=value['received_us']<claim.payload['expires_us']):
            raise ValueError('Inbox request/claim history mismatch')
        if (row.ordinal!=ordinal or value['ordinal']!=ordinal or proposal['account_id']!=account_id
            or row.request_id!=proposal['request_id'] or row.event_id!=event['event_id'] or row.source_sequence!=event['sequence'] or row.payload_sha256!=value['sha256']):
            raise ValueError('Inbox storage identity mismatch')
    return saved


def stage(engine,value):
    value=validate(value)
    with journal.transaction(engine) as db:
        db.execute(text("SET LOCAL lock_timeout='2000ms'"))
        row=journal.lock(db,value['account_id']);financial=journal.audit(db,row);controlled=controls.check(db,row,financial)
        entry=next((item for item in financial['requests'] if item['request']['request_id']==value['request_id']),None)
        if entry is None or controlled['coverage']!='CONTROLLED':raise ValueError('Known controlled request required')
        saved=records(db,row.account_id)
        old=next((item for item in saved if item.request_id==value['request_id'] and item.event_id==value['event']['event_id']),None)
        if old is not None:
            if encoded(old.payload['proposal'])!=encoded(value):raise ValueError('Conflicting staged redelivery')
            # Receipt acknowledgement does not apply an event or renew authority.
            return deepcopy(old.payload)
        if any(item.request_id==value['request_id'] and item.source_sequence==value['event']['sequence'] for item in saved):raise ValueError('Conflicting staged source sequence')
        claim=ownership._owned(db,value['request_id'],value['owner'],value['ownership_token'])
        if value['expected_financial_revision']!=financial['revision']:raise ValueError('Staging checkpoint conflict')
        if execution.clock(value['event']['received_at'])<execution.clock(entry['request']['created_at']):raise ValueError('Staged receipt predates request')
        if value['event']['sequence']<len(entry['events']):raise ValueError('Applied history cannot be relabeled as staged')
        if len(saved)>=LIMIT:raise ValueError('Inbox count capacity reached')
        body={'version':VERSION,'ordinal':len(saved),'proposal':value,'request_sha256':sha(entry['request']),
              'control_revision':controlled['revision'],'claim_sha256':sha(claim),'received_us':ownership.clock(db),
              'state':'STAGED_UNAPPLIED','staging_committed':True,'event_committed':False,
              'financial_application_verified':False,'external_submission_allowed':False}
        result=verify({**body,'sha256':sha(body)})
        if sum(len(encoded(item.payload).encode()) for item in saved)+len(encoded(result).encode())>MAX_BYTES:
            raise ValueError('Inbox byte capacity reached')
        db.add(Inbox(account_id=row.account_id,ordinal=len(saved),request_id=value['request_id'],event_id=value['event']['event_id'],source_sequence=value['event']['sequence'],payload=result,payload_sha256=result['sha256']))
        db.flush();records(db,row.account_id);ownership._owned(db,value['request_id'],value['owner'],value['ownership_token'])
        return result


def capture(engine,account_id):
    with journal.transaction(engine) as db:
        db.execute(text("SET LOCAL lock_timeout='2000ms'"))
        row=journal.lock(db,account_id);financial=journal.audit(db,row);saved=records(db,account_id)
        applied={(entry['request']['request_id'],event['event_id']):event for entry in financial['requests'] for event in entry['events']}
        observations=[]
        for item in saved:
            event=applied.get((item.request_id,item.event_id));original=item.payload['proposal']['event']
            state='NOT_PRESENT_IN_JOURNAL' if event is None else 'PRESENT_IN_JOURNAL' if encoded(event)==encoded(original) else 'CONFLICTING_JOURNAL_EVENT'
            observations.append({'ordinal':item.ordinal,'journal_state':state})
        return {'version':VERSION,'account_id':account_id,'financial_revision':financial['revision'],
                'records':[deepcopy(item.payload) for item in saved],'application_observations':observations,
                'read_only':True,'event_committed':False,'external_submission_allowed':False}
