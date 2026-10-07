"""Two separately committed transactions for closed-bar simulation preparation."""
from datetime import datetime,timezone,timedelta
from types import SimpleNamespace
import re
import json
from sqlalchemy import select,func
from sqlalchemy.orm import Session
from core.backtest.spot import digest
from core.models import Candle
from core.paper import streams,intents,authorization,lifecycle
from core.storage.models import PaperStreamRecord as Stream,PaperPreparationRecord as Preparation

VERSION='paper-closed-bar-preparation-v1'
LIMIT=1000
MAX_BYTES=32*1024*1024
class Capacity(ValueError):pass


def base(record):
    return {'revision':record.revision,'status':record.status,'halt_at':record.halt_at,
        'snapshot_sha256':record.snapshot['sha256'],'ledger_sha256':digest(record.ledger),
        'observations_sha256':digest(record.observations)}


def validate(row):
    value=row.payload
    if set(value)!={'version','session_id','base','observations'} or value['version']!=VERSION or value['session_id']!=row.session_id:raise ValueError('Invalid preparation payload')
    if row.payload_sha256!=digest(value) or row.preparation_id!=digest({'version':VERSION,'payload':value}):raise ValueError('Preparation integrity failed')
    if not 1<=len(value['observations'])<=10:raise ValueError('Invalid prepared batch size')
    baseline=value['base']
    if set(baseline)!={'revision','status','halt_at','snapshot_sha256','ledger_sha256','observations_sha256'}:raise ValueError('Invalid preparation base')
    if isinstance(baseline['revision'],bool) or not isinstance(baseline['revision'],int) or baseline['revision']<0 or baseline['status'] not in ['RUNNING','PAUSED']:raise ValueError('Invalid preparation revision')
    if baseline['halt_at'] is not None and (isinstance(baseline['halt_at'],bool) or not isinstance(baseline['halt_at'],int) or baseline['halt_at']<0):raise ValueError('Invalid halt cursor')
    if any(not isinstance(baseline[k],str) or not re.fullmatch('[0-9a-f]{64}',baseline[k]) for k in ['snapshot_sha256','ledger_sha256','observations_sha256']):raise ValueError('Invalid preparation digests')
    previous=None;observed=None
    for observation in value['observations']:
        if set(observation)!={'candle','observed_at','gate'} or observation['gate'] not in ['ELIGIBLE','STALE_BAR','PAUSED','PRE_ACTIVATION']:raise ValueError('Invalid prepared observation')
        clock=datetime.fromisoformat(observation['observed_at']);bar=Candle.model_validate(observation['candle'])
        if clock.tzinfo is None or clock<bar.close_time or (observed is not None and clock!=observed) or (previous is not None and (bar.open_time!=previous.close_time or bar.instrument_id!=previous.instrument_id or bar.timeframe!=previous.timeframe)):raise ValueError('Invalid prepared observation clock')
        previous=bar;observed=clock
    if row.created_at.tzinfo is None or observed>row.created_at or row.created_at>datetime.now(timezone.utc):raise ValueError('Invalid preparation creation clock')
    if row.finished_at is not None and (row.finished_at.tzinfo is None or row.finished_at<row.created_at):raise ValueError('Invalid preparation outcome clock')
    if row.status not in ['PREPARED','CONSUMED','CANCELLED']:raise ValueError('Invalid preparation state')
    if row.status=='PREPARED' and (row.finished_at is not None or row.reason is not None):raise ValueError('Invalid pending outcome')
    if row.status=='CONSUMED' and (row.finished_at is None or row.reason is not None):raise ValueError('Invalid consumed outcome')
    if row.status=='CANCELLED' and (row.finished_at is None or row.reason not in ['CONTROL_CHANGED','EXPIRED','SOURCE_CHANGED','AUTHORIZATION_MISSING']):raise ValueError('Invalid cancellation')
    return value


def pending(db,id):
    return db.scalar(select(Preparation).where(Preparation.session_id==id,Preparation.status=='PREPARED').with_for_update())


def prepare(engine,id,*,observed_at=None):
    stamp=observed_at or streams.now()
    if stamp.tzinfo is None or stamp>streams.now():raise ValueError('Require nonfuture timezone-aware observation')
    with Session(engine) as db,db.begin():
        record=db.scalar(select(Stream).where(Stream.session_id==id).with_for_update(skip_locked=True))
        if record is None:return None
        existing=pending(db,id)
        if existing is not None:
            validate(existing)
            if existing.authorization_version==authorization.VERSION and record.status in ['RUNNING','PAUSED'] and base(record)==existing.payload['base']:
                lifecycle.enroll(db,record,existing,legacy=True)
            return existing.preparation_id
        if record.status not in ['RUNNING','PAUSED']:return None
        # The dry preparation never accepts observations or changes economic facts.
        observations=streams.advance_locked(db,record,stamp,prepare_only=True)
        if not observations:return None
        if db.scalar(select(func.count()).select_from(Preparation).where(Preparation.session_id==id))>=LIMIT:raise Capacity('Paper preparation capacity reached')
        payload={'version':VERSION,'session_id':id,'base':base(record),'observations':observations}
        pid=digest({'version':VERSION,'payload':payload})
        previous=db.get(Preparation,pid)
        if previous is not None:validate(previous);return None
        # Validate the candidate prefix without changing the persisted ledger.
        candidate=SimpleNamespace(snapshot=record.snapshot,observations=[*record.observations,*observations],halt_at=record.halt_at)
        streams.computed(candidate)
        prepared=Preparation(preparation_id=pid,session_id=id,created_at=datetime.now(timezone.utc),finished_at=None,
            payload=payload,payload_sha256=digest(payload),status='PREPARED',reason=None,authorization_version=authorization.VERSION)
        db.add(prepared);db.flush()
        authorization.persist(db,record,prepared)
        lifecycle.enroll(db,record,prepared)
        return pid


def consume(engine,id,pid,*,observed_at=None):
    stamp=observed_at or streams.now()
    if stamp.tzinfo is None or stamp>streams.now():raise ValueError('Require nonfuture timezone-aware observation')
    with Session(engine) as db,db.begin():
        record=db.scalar(select(Stream).where(Stream.session_id==id).with_for_update(skip_locked=True))
        if record is None:return False
        row=db.get(Preparation,pid,with_for_update=True)
        if row is None or row.session_id!=id:raise ValueError('Preparation account mismatch')
        payload=validate(row)
        if row.status!='PREPARED':return False
        streams.visible(record)
        if row.authorization_version not in [None,authorization.VERSION]:raise ValueError('Unsupported preparation authorization version')
        reason=None
        if row.authorization_version is None:
            if authorization.rows(db,pid):raise ValueError('Legacy preparation has undeclared authorization')
            reason='AUTHORIZATION_MISSING'
        elif base(record)!=payload['base'] or record.status not in ['RUNNING','PAUSED']:reason='CONTROL_CHANGED'
        elif any(stamp<datetime.fromisoformat(obs['observed_at']) for obs in payload['observations']):raise ValueError('Consumption precedes preparation observation')
        elif any(obs['gate']=='ELIGIBLE' and stamp>datetime.fromisoformat(obs['candle']['close_time'])+timedelta(seconds=record.snapshot['request']['max_lag_seconds']) for obs in payload['observations']):reason='EXPIRED'
        if reason:
            lifecycle.finish(db,record,row,'CANCELLED',reason)
            row.status='CANCELLED';row.reason=reason;row.finished_at=datetime.now(timezone.utc);return False
        observed=datetime.fromisoformat(payload['observations'][0]['observed_at'])
        approved=authorization.check(db,record,row)
        coverage,_=lifecycle.check_coverage(db,record,row)
        if coverage is None:raise ValueError('Lifecycle must be independently enrolled before consumption')
        order_count=len(record.ledger['orders']);fill_count=len(record.ledger['fills'])
        added=streams.advance_locked(db,record,observed,accepted_plan=payload['observations'])
        if added:
            orders={v['order_id']:v for v in record.ledger['orders'][order_count:]}
            fills={v['order_id']:v for v in record.ledger['fills'][fill_count:]}
            if set(orders)!={v.order_id for v in approved} or any(orders[v.order_id]!=v.payload['proposed_order'] or fills.get(v.order_id)!=v.payload['projected_fill'] for v in approved):raise ValueError('Applied simulation differs from authorization')
        lifecycle.finish(db,record,row,'CONSUMED' if added else 'CANCELLED',None if added else 'SOURCE_CHANGED')
        row.status='CONSUMED' if added else 'CANCELLED';row.reason=None if added else 'SOURCE_CHANGED'
        row.finished_at=datetime.now(timezone.utc)
        return bool(added)


def advance(engine,id,*,observed_at=None):
    pid=prepare(engine,id,observed_at=observed_at)
    if pid is None:return False
    return consume(engine,id,pid,observed_at=observed_at)


def cancel_control(db,record):
    row=pending(db,record.session_id)
    if row is not None:
        validate(row);lifecycle.finish(db,record,row,'CANCELLED','CONTROL_CHANGED')
        row.status='CANCELLED';row.reason='CONTROL_CHANGED';row.finished_at=datetime.now(timezone.utc)


def capture(engine,id):
    with engine.connect().execution_options(isolation_level='REPEATABLE READ') as conn,conn.begin(),Session(bind=conn) as db:
        record=db.get(Stream,id)
        if record is None:raise intents.MissingAccount(id)
        streams.visible(record)
        stored=list(db.scalars(select(Preparation).where(Preparation.session_id==id).order_by(Preparation.created_at,Preparation.preparation_id).limit(LIMIT+1)))
        if len(stored)>LIMIT:raise ValueError('Preparation history exceeds capacity')
        result=[]
        for row in stored:
            validate(row)
            result.append({'preparation_id':row.preparation_id,'created_at':row.created_at.isoformat(),'finished_at':row.finished_at.isoformat() if row.finished_at else None,'status':row.status,'reason':row.reason,'payload':row.payload,'payload_sha256':row.payload_sha256})
        report={'version':VERSION,'session_id':id,'revision':record.revision,'records':result,
            'mode':'READ_ONLY_PAPER_PREPARATION','trading_enabled':False,'automatic_replay':False,'external_submission_supported':False}
        if len(json.dumps(report,separators=(',',':')).encode())>MAX_BYTES:raise ValueError('Preparation response exceeds 32 MiB')
        return report
