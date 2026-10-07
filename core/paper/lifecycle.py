"""Independent local Paper facts; no venue acknowledgements or account writes."""
from datetime import datetime,timezone
from decimal import Decimal,localcontext
import json
from sqlalchemy import select,func
from sqlalchemy.orm import Session
from core.backtest.spot import digest
from core.paper import authorization,intents,streams
from core.storage.models import PaperStreamRecord as Stream,PaperPreparationRecord as Preparation,PaperAuthorizationRecord as Authorization,PaperLifecycleCoverageRecord as Coverage,PaperLifecycleEventRecord as Event

VERSION='paper-local-lifecycle-v1'
WINDOW=20
MAX_BYTES=32*1024*1024


def descriptor(aid,sequence,kind,payload):
    value={'authorization_id':aid,'sequence':sequence,'kind':kind,'payload':payload,'payload_sha256':digest(payload),
        'fill_id':digest({'authorization_id':aid,'fill':payload['fill']}) if kind=='FILL' else None}
    value['event_id']=digest({'version':VERSION,**value})
    return value


def creation(auth):
    return descriptor(auth.authorization_id,0,'CREATED',{'version':VERSION,'authorization_id':auth.authorization_id,
        'authorization_sha256':auth.payload_sha256,'authorization':auth.payload})


def completion(auth,status,reason):
    result=[];sequence=1
    if status=='CONSUMED' and auth.payload['projected_fill'] is not None:
        result.append(descriptor(auth.authorization_id,sequence,'FILL',{'version':VERSION,'authorization_id':auth.authorization_id,'fill':auth.payload['projected_fill']}));sequence+=1
    payload={'version':VERSION,'authorization_id':auth.authorization_id,
        'status':auth.payload['proposed_order']['status'] if status=='CONSUMED' else 'CANCELLED',
        'reason':auth.payload['reason'] if status=='CONSUMED' else reason,
        'receipt':{'order':auth.payload['proposed_order'],'fill':auth.payload['projected_fill']} if status=='CONSUMED' else None}
    result.append(descriptor(auth.authorization_id,sequence,'OUTCOME',payload));return result


def serialized(row):
    return {key:(getattr(row,key).isoformat() if key=='recorded_at' else getattr(row,key)) for key in ['event_id','authorization_id','sequence','kind','fill_id','recorded_at','payload','payload_sha256']}


def reduce(events):
    if not events:raise ValueError('Lifecycle creation missing')
    unique={};sequences={}
    fields={'event_id','authorization_id','sequence','kind','fill_id','recorded_at','payload','payload_sha256'}
    for row in events:
        if set(row)!=fields:raise ValueError('Invalid lifecycle fields')
        seq=row['sequence']
        if isinstance(seq,bool) or not isinstance(seq,int) or seq<0:raise ValueError('Invalid lifecycle sequence')
        if row['kind'] not in ['CREATED','FILL','OUTCOME'] or not isinstance(row['payload'],dict) or (row['kind']=='FILL' and 'fill' not in row['payload']):raise ValueError('Invalid lifecycle event kind or payload')
        expected=descriptor(row['authorization_id'],seq,row['kind'],row['payload'])
        if any(row[k]!=v for k,v in expected.items()):raise ValueError('Lifecycle identity or digest differs')
        if row['event_id'] in unique:
            if row!=unique[row['event_id']]:raise ValueError('Conflicting duplicate lifecycle delivery')
            continue
        if seq in sequences:raise ValueError('Conflicting lifecycle sequence')
        unique[row['event_id']]=row;sequences[seq]=row
    ordered=sorted(unique.values(),key=lambda r:r['sequence'])
    if [r['sequence'] for r in ordered]!=list(range(len(ordered))):raise ValueError('Lifecycle sequence gap')
    aid=ordered[0]['authorization_id'];created=ordered[0]['payload'];previous=None
    if ordered[0]['kind']!='CREATED' or set(created)!={'version','authorization_id','authorization_sha256','authorization'}:raise ValueError('Invalid lifecycle creation')
    auth=created['authorization']
    if created['authorization_sha256']!=digest(auth) or auth['version']!=authorization.VERSION or auth['projection_only'] is not True or auth['external_submission_allowed'] is not False:raise ValueError('Invalid lifecycle authorization evidence')
    if aid!=digest({'version':authorization.VERSION,'preparation_id':auth['preparation_id'],'order_id':auth['proposed_order']['order_id']}):raise ValueError('Wrong lifecycle authorization identity')
    estimate=auth['projected_fill'];allowed=auth['decision']=='ALLOW_SIMULATION'
    if auth['decision'] not in ['ALLOW_SIMULATION','DENY_SIMULATION'] or allowed!=(estimate is not None):raise ValueError('Invalid lifecycle decision')
    with localcontext() as ctx:
        ctx.prec=60
        maximum=Decimal(estimate['quantity']) if estimate else None
        if maximum is not None and (not maximum.is_finite() or maximum<=0):raise ValueError('Invalid authorized quantity')
        if allowed and (auth['proposed_order']['status'] not in ['FILLED','PARTIAL_CANCELLED'] or Decimal(auth['proposed_order']['quantity'])!=maximum):raise ValueError('Authorization order quantity differs from estimate')
        if not allowed and (auth['proposed_order']['status']!='REJECTED' or auth['proposed_order']['reason']!=auth['reason']):raise ValueError('Authorization rejection differs')
        quantity=fees=notional=Decimal(0);fill_ids=set();terminal=None
        for row in ordered:
            payload=row['payload'];clock=datetime.fromisoformat(row['recorded_at'])
            if clock.tzinfo is None or clock>datetime.now(timezone.utc) or (previous is not None and clock<previous):raise ValueError('Invalid lifecycle clock')
            previous=clock
            if payload.get('version')!=VERSION or payload.get('authorization_id')!=aid or row['authorization_id']!=aid:raise ValueError('Mixed lifecycle identities')
            if row['sequence']==0:continue
            if terminal is not None:raise ValueError('Lifecycle update after terminal requires separate reconciliation')
            if row['kind']=='FILL':
                if set(payload)!={'version','authorization_id','fill'} or not allowed:raise ValueError('Unauthorized lifecycle fill')
                fill=payload['fill'];size=Decimal(fill['quantity']);price=Decimal(fill['price']);fee=Decimal(fill['fee'])
                if any(not v.is_finite() for v in [size,price,fee]) or size<=0 or price<=0 or fee<0:raise ValueError('Invalid lifecycle fill amounts')
                if any(fill[k]!=auth['proposed_order'][k] for k in ['order_id','side','decision_at','execution_at','observed_at','simulated']):raise ValueError('Lifecycle fill linkage differs')
                if row['fill_id'] in fill_ids:raise ValueError('Repeated fill identity in new event')
                fill_ids.add(row['fill_id']);quantity+=size;fees+=fee;notional+=size*price
                if quantity>maximum:raise ValueError('Lifecycle cumulative quantity exceeds authorization')
            elif row['kind']=='OUTCOME':
                if set(payload)!={'version','authorization_id','status','reason','receipt'}:raise ValueError('Invalid lifecycle outcome')
                terminal=payload['status'];receipt=payload['receipt']
                if terminal=='CANCELLED':
                    if receipt is not None or quantity!=0 or payload['reason'] not in ['CONTROL_CHANGED','EXPIRED','SOURCE_CHANGED','AUTHORIZATION_MISSING']:raise ValueError('Invalid unconsumed cancellation')
                elif terminal in ['FILLED','PARTIAL_CANCELLED','REJECTED']:
                    if receipt!={'order':auth['proposed_order'],'fill':estimate} or terminal!=auth['proposed_order']['status'] or payload['reason']!=auth['reason']:raise ValueError('Lifecycle outcome differs from authorization')
                    if terminal=='REJECTED':
                        if allowed or quantity!=0:raise ValueError('Rejected lifecycle has fills')
                    elif not allowed or quantity!=maximum or fees!=Decimal(estimate['fee']) or notional!=maximum*Decimal(estimate['price']):raise ValueError('Lifecycle cumulative receipts differ')
                else:raise ValueError('Unsupported local terminal state')
            else:raise ValueError('Unsupported lifecycle event')
        return {'authorization_id':aid,'state':terminal or 'AWAITING_CONSUMPTION','authorized_quantity':str(maximum) if maximum is not None else None,
            'cumulative_quantity':str(quantity),'cumulative_fees':str(fees),'cumulative_notional':str(notional),'unique_fills':len(fill_ids),'events':len(ordered)}


def event_rows(db,aid):
    return list(db.scalars(select(Event).where(Event.authorization_id==aid).order_by(Event.sequence)))


def check_coverage(db,record,prepared):
    from core.paper import preparation
    preparation.validate(prepared)
    coverage=db.get(Coverage,prepared.preparation_id)
    auths=authorization.rows(db,prepared.preparation_id)
    if coverage is None:
        if any(event_rows(db,a.authorization_id) for a in auths):raise ValueError('Undeclared lifecycle events')
        return None,auths
    if coverage.version!=VERSION or coverage.origin not in ['NEW_PREPARATION','LEGACY_PENDING_ENROLLMENT'] or coverage.recorded_at.tzinfo is None or coverage.recorded_at<prepared.created_at or coverage.recorded_at>datetime.now(timezone.utc):raise ValueError('Invalid lifecycle coverage')
    if prepared.finished_at is not None and coverage.recorded_at>prepared.finished_at:raise ValueError('Lifecycle enrolled after completion')
    frozen=authorization.historical_base(record,prepared)
    auths=authorization.check(db,frozen,prepared)
    for auth in auths:
        if auth.recorded_at>coverage.recorded_at:raise ValueError('Lifecycle creation predates authorization evidence')
        stored=event_rows(db,auth.authorization_id)
        expected=[creation(auth)]
        if prepared.status!='PREPARED':expected+=completion(auth,prepared.status,prepared.reason)
        if len(stored)!=len(expected) or any(any(getattr(row,k)!=v for k,v in value.items()) for row,value in zip(stored,expected)):raise ValueError('Lifecycle event coverage differs')
        for row in stored:
            if row.recorded_at<coverage.recorded_at or (prepared.finished_at is not None and row.recorded_at>prepared.finished_at):raise ValueError('Lifecycle event outside evidence clock')
        reduce([serialized(row) for row in stored])
    return coverage,auths


def enroll(db,record,prepared,*,legacy=False):
    coverage,auths=check_coverage(db,record,prepared)
    if coverage is not None:return
    if prepared.status!='PREPARED' or prepared.authorization_version!=authorization.VERSION:raise ValueError('Lifecycle can enroll only unconsumed authorized preparation')
    authorization.check(db,record,prepared)
    clock=datetime.now(timezone.utc)
    db.add(Coverage(preparation_id=prepared.preparation_id,version=VERSION,recorded_at=clock,
        origin='LEGACY_PENDING_ENROLLMENT' if legacy else 'NEW_PREPARATION'))
    for auth in auths:db.add(Event(recorded_at=clock,**creation(auth)))
    db.flush()


def finish(db,record,prepared,status,reason):
    coverage,auths=check_coverage(db,record,prepared)
    if coverage is None:return
    clock=datetime.now(timezone.utc)
    for auth in auths:
        for value in completion(auth,status,reason):db.add(Event(recorded_at=clock,**value))
    db.flush()


def capture(engine,id):
    from core.paper import preparation
    with engine.connect().execution_options(isolation_level='REPEATABLE READ') as conn,conn.begin(),Session(bind=conn) as db:
        record=db.get(Stream,id)
        if record is None:raise intents.MissingAccount(id)
        streams.visible(record)
        total=db.scalar(select(func.count()).select_from(Preparation).where(Preparation.session_id==id))
        if total>preparation.LIMIT:raise ValueError('Lifecycle history exceeds capacity')
        batches=list(reversed(list(db.scalars(select(Preparation).where(Preparation.session_id==id).order_by(Preparation.created_at.desc(),Preparation.preparation_id.desc()).limit(WINDOW)))))
        results=[]
        for prepared in batches:
            coverage,auths=check_coverage(db,record,prepared)
            orders=[]
            if coverage is not None:
                if prepared.status=='CONSUMED':
                    frozen=authorization.historical_base(record,prepared);start=len(frozen.observations)
                    if record.observations[start:start+len(prepared.payload['observations'])]!=prepared.payload['observations']:raise ValueError('Lifecycle consumed observation missing')
                    actual={r['order_id']:r for r in record.ledger['orders']};fills={r['order_id']:r for r in record.ledger['fills']}
                    if any(actual.get(a.order_id)!=a.payload['proposed_order'] or fills.get(a.order_id)!=a.payload['projected_fill'] for a in auths):raise ValueError('Lifecycle consumed receipts differ')
                for auth in auths:
                    events=[serialized(row) for row in event_rows(db,auth.authorization_id)]
                    orders.append({'summary':reduce(events),'events':events})
            results.append({'preparation_id':prepared.preparation_id,'status':prepared.status,'coverage':'VERIFIED' if coverage else 'LEGACY_UNAVAILABLE','origin':coverage.origin if coverage else None,'orders':orders})
        result={'version':VERSION,'session_id':id,'revision':record.revision,'total_batches':total,'window_limit':WINDOW,'has_older':total>len(batches),'batches':results,
            'mode':'READ_ONLY_LOCAL_PAPER_LIFECYCLE','trading_enabled':False,'automatic_replay':False,'external_reconciliation_supported':False}
        if len(json.dumps(result,separators=(',',':')).encode())>MAX_BYTES:raise ValueError('Lifecycle response exceeds 32 MiB')
        return result
