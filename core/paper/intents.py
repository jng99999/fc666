"""Atomic independent completed simulation intents, never exchange instructions."""
import json
from datetime import datetime,timezone
from types import SimpleNamespace
from uuid import UUID
from sqlalchemy import select
from sqlalchemy.orm import Session
from core.backtest.spot import digest
from core.storage.models import PaperOrderIntentRecord as Intent,PaperStreamRecord as Stream

VERSION='paper-completed-order-intent-v1'
MAX_BYTES=32*1024*1024
class MissingAccount(KeyError):pass


def transition_path(status):
    if status=='REJECTED':return ['CREATED','REJECTED']
    if status in ['FILLED','PARTIAL_CANCELLED']:return ['CREATED','VALIDATED',status]
    raise ValueError('Unsupported simulated order state')


def expected(record):
    from core.paper.streams import visible
    ledger=visible(record);fills={fill['order_id']:fill for fill in ledger['fills']}
    if len(fills)!=len(ledger['fills']):raise ValueError('Duplicate simulated fill')
    result={}
    for order in ledger['orders']:
        oid=order['order_id'];fill=fills.get(oid)
        if oid in result or order['simulated'] is not True or (order['status']=='REJECTED')!=(fill is None):raise ValueError('Ambiguous simulated receipt')
        if fill is not None and (any(fill[k]!=order[k] for k in ['order_id','side','decision_at','execution_at','observed_at','simulated']) or fill['quantity']!=order['quantity']):raise ValueError('Inconsistent receipt')
        payload={'version':VERSION,'session_id':record.session_id,'snapshot_sha256':record.snapshot['sha256'],'engine_version':record.snapshot['version'],'order':order,'fill':fill}
        result[oid]={'intent_id':digest({'version':VERSION,'session_id':record.session_id,'order_id':oid}),
            'payload':payload,'payload_sha256':digest(payload),'status':order['status'],
            'transitions':transition_path(order['status'])}
    if set(fills)-set(result):raise ValueError('Orphan simulated fill')
    return result


def rows(db,record):
    return list(db.scalars(select(Intent).where(Intent.session_id==record.session_id).order_by(Intent.order_id)))


def check(record,stored,*,allow_missing=False):
    wanted=expected(record);found={row.order_id:row for row in stored}
    if len(found)!=len(stored) or set(found)-set(wanted):raise ValueError('Unexpected durable intent')
    if not allow_missing and set(found)!=set(wanted):raise ValueError('Durable intent coverage mismatch')
    for oid,row in found.items():
        value=wanted[oid]
        if row.session_id!=record.session_id or row.version!=VERSION or row.origin not in ['BAR_ACCEPTANCE','HISTORICAL_MATERIALIZATION'] or any(getattr(row,k)!=v for k,v in value.items()):raise ValueError('Durable intent integrity failed')
        if row.recorded_at.tzinfo is None:raise ValueError('Invalid intent clock')
    return wanted,found


def synchronize(db,record,*,new=False):
    """Caller holds the stream row lock and owns the ledger transaction."""
    if record.intent_version not in [None,VERSION]:raise ValueError('Unsupported intent version')
    legacy=record.intent_version is None
    stored=rows(db,record)
    if legacy and stored:raise ValueError('Legacy account has undeclared durable intents')
    wanted,found=check(record,stored,allow_missing=legacy or new)
    for oid,value in wanted.items():
        if oid not in found:
            db.add(Intent(session_id=record.session_id,order_id=oid,version=VERSION,
                origin='HISTORICAL_MATERIALIZATION' if legacy else 'BAR_ACCEPTANCE',
                recorded_at=datetime.now(timezone.utc),**value))
    record.intent_version=VERSION
    db.flush()


def capture(engine,id):
    with engine.connect().execution_options(isolation_level='REPEATABLE READ') as conn,conn.begin(),Session(bind=conn) as db:
        record=db.get(Stream,id)
        if record is None:raise MissingAccount(id)
        cutoff=datetime.now(timezone.utc)
        raw={k:getattr(record,k) for k in ['session_id','snapshot','observations','ledger','revision','status','halt_at','feed','intent_version']}
        stored=[{k:(getattr(r,k).isoformat() if k=='recorded_at' else getattr(r,k)) for k in ['intent_id','session_id','order_id','version','recorded_at','origin','status','payload','payload_sha256','transitions']} for r in rows(db,record)]
        return evaluate({'version':VERSION,'as_of':cutoff.isoformat(),'record':raw,'stored':stored})


def evaluate(inputs):
    from core.paper.recovery import stamp
    if set(inputs)!={'version','as_of','record','stored'} or inputs['version']!=VERSION:raise ValueError('Invalid intent inputs')
    cutoff=stamp(inputs['as_of']);raw=inputs['record']
    if set(raw)!={'session_id','snapshot','observations','ledger','revision','status','halt_at','feed','intent_version'}:raise ValueError('Invalid account fields')
    if str(UUID(raw['session_id']))!=raw['session_id'] or isinstance(raw['revision'],bool) or not isinstance(raw['revision'],int) or raw['revision']<0:raise ValueError('Invalid account identity')
    if raw['status'] not in ['RUNNING','PAUSED','STOPPED','BLOCKED','LIMIT_REACHED']:raise ValueError('Invalid lifecycle')
    if stamp(raw['snapshot']['request']['as_of'])>cutoff or any(stamp(r['observed_at'])>cutoff for r in raw['observations']):raise ValueError('Future account evidence')
    if raw['feed'].get('checked_at') is not None and stamp(raw['feed']['checked_at'])>cutoff:raise ValueError('Future feed evidence')
    record=SimpleNamespace(**raw)
    if record.intent_version not in [None,VERSION]:raise ValueError('Unsupported intent version')
    stored=[]
    fields={'intent_id','session_id','order_id','version','recorded_at','origin','status','payload','payload_sha256','transitions'}
    for row in inputs['stored']:
        if set(row)!=fields:raise ValueError('Invalid intent fields')
        clock=stamp(row['recorded_at'])
        if clock>cutoff:raise ValueError('Future intent evidence')
        stored.append(SimpleNamespace(**{**row,'recorded_at':clock}))
    legacy=record.intent_version is None
    if legacy and stored:raise ValueError('Legacy account has undeclared durable intents')
    wanted,_=check(record,stored,allow_missing=legacy)
    result={'version':VERSION,'session_id':record.session_id,'as_of':inputs['as_of'],'revision':record.revision,
        'coverage':'LEGACY_UNMATERIALIZED' if legacy else 'VERIFIED',
        'ledger_orders':len(wanted),'durable_orders':len(stored),'intents':inputs['stored'],
        'mode':'READ_ONLY_COMPLETED_PAPER_INTENTS','trading_enabled':False,'automatic_replay':False,
        'in_flight_submission_supported':False,'inputs_sha256':digest(inputs),'inputs':inputs}
    if len(json.dumps(result,separators=(',',':')).encode())>MAX_BYTES:raise ValueError('Intent export exceeds 32 MiB')
    return result


def verify(value):
    if value!=evaluate(value['inputs']):raise ValueError('Intent export differs from reconstructed inputs')
    return {'coverage':value['coverage'],'orders':value['durable_orders']}
