"""PostgreSQL queue with immutable input snapshots and fenced expiring leases."""
from datetime import datetime,timedelta,timezone
from uuid import uuid4
from sqlalchemy import select,or_,func,text
from sqlalchemy.orm import Session
from core.storage.models import ResearchJobRecord as Job,ResearchWorkerRecord as Worker
from core.backtest.spot import simulate,BacktestConfig,digest,VERSION
from core.models import Candle,Instrument

LEASE_SECONDS=30
ACTIVE_LIMIT=20
class QueueFull(Exception):pass
class Cancelled(Exception):pass
class LostLease(Exception):pass

def now():return datetime.now(timezone.utc)

def view(job):
    return {'job_id':job.job_id,'created_at':job.created_at,'updated_at':job.updated_at,'status':job.status,'progress':job.progress,'cancel_requested':job.cancel_requested,'attempts':job.attempts,'request':job.request,'error':job.error,'run_id':None if job.result is None else job.result['run_id']}

def enqueue(engine,request,bars,instrument):
    if len(bars)<2:raise ValueError('Require at least two finalized bars')
    if any(b.open_time!=a.close_time or b.timeframe!=a.timeframe for a,b in zip(bars,bars[1:])):raise ValueError('Contiguous ordered series required')
    snapshot={'request_sha256':digest(request),'engine_version':VERSION,'strategy':request.get('strategy','ema_long_flat_v1'),'dataset':[b.model_dump(mode='json') for b in bars],'instrument':instrument.model_dump(mode='json')}
    snapshot['sha256']=digest(snapshot)
    stamp=now()
    with Session(engine) as session,session.begin():
        session.execute(text('SELECT pg_advisory_xact_lock(6660601)'))
        count=session.scalar(select(func.count()).select_from(Job).where(Job.status.in_(['QUEUED','RUNNING'])))
        if count>=ACTIVE_LIMIT:raise QueueFull()
        job=Job(job_id=str(uuid4()),created_at=stamp,updated_at=stamp,status='QUEUED',progress=0,cancel_requested=False,attempts=0,request=request,snapshot=snapshot)
        session.add(job);session.flush();return view(job)

def heartbeat(engine,worker_id):
    with Session(engine) as session,session.begin():
        worker=session.get(Worker,worker_id)
        if worker:worker.heartbeat_at=now()
        else:session.add(Worker(worker_id=worker_id,heartbeat_at=now()))

def healthy(engine):
    with Session(engine) as session:
        stamp=session.scalar(select(func.max(Worker.heartbeat_at)))
    return stamp is not None and stamp>now()-timedelta(seconds=LEASE_SECONDS)

def claim(engine,owner):
    stamp=now()
    with Session(engine) as session,session.begin():
        job=session.scalar(select(Job).where(or_(Job.status=='QUEUED',(Job.status=='RUNNING')&(Job.lease_until<=stamp))).order_by(Job.created_at,Job.job_id).with_for_update(skip_locked=True).limit(1))
        if job is None:return None
        if job.cancel_requested or job.attempts>=3:
            job.status='CANCELLED' if job.cancel_requested else 'FAILED';job.error=None if job.cancel_requested else 'Worker lease expired after 3 attempts';job.lease_owner=None;job.lease_until=None;job.updated_at=stamp
            return None
        job.status='RUNNING';job.progress=0;job.attempts+=1;job.lease_owner=owner;job.lease_until=stamp+timedelta(seconds=LEASE_SECONDS);job.updated_at=stamp
        return job.job_id

def cancel(engine,job_id):
    with Session(engine) as session,session.begin():
        job=session.get(Job,job_id,with_for_update=True)
        if job is None:raise KeyError(job_id)
        if job.status in ['SUCCEEDED','FAILED']:raise ValueError('Task already terminal')
        job.cancel_requested=True;job.updated_at=now()
        if job.status=='QUEUED':job.status='CANCELLED'
        return view(job)

def owned(session,job_id,owner):
    job=session.get(Job,job_id,with_for_update=True)
    if job is None or job.status!='RUNNING' or job.lease_owner!=owner or job.lease_until<=now():raise LostLease()
    return job

def finish(engine,job_id,owner,result=None,error=None):
    with Session(engine) as session,session.begin():
        job=owned(session,job_id,owner)
        job.updated_at=now();job.lease_until=None;job.lease_owner=None
        if job.cancel_requested:job.status='CANCELLED'
        elif error:job.status='FAILED';job.error=error
        else:job.status='SUCCEEDED';job.progress=100;job.result=result

def execute(engine,job_id,owner,stopping=lambda:False):
    with Session(engine) as session:
        job=owned(session,job_id,owner);snapshot=job.snapshot;request=job.request
    def checkpoint(done,total):
        if stopping():raise LostLease() # Release by expiry; process shutdown is not success/cancellation.
        with Session(engine) as session,session.begin():
            job=owned(session,job_id,owner)
            if job.cancel_requested:raise Cancelled()
            job.progress=min(99,int(done*100/total));job.updated_at=now();job.lease_until=now()+timedelta(seconds=LEASE_SECONDS)
        heartbeat(engine,owner)
    try:
        body={key:value for key,value in snapshot.items() if key!='sha256'}
        if digest(body)!=snapshot['sha256'] or snapshot['engine_version'] not in [VERSION,'spot-next-open-v2'] or digest(request)!=snapshot['request_sha256']:raise ValueError('Unsupported or altered job snapshot')
        runner=simulate;config_type=BacktestConfig;options={'strategy_id':snapshot['strategy'],'parameters':request.get('parameters')}
        if snapshot['strategy']!=request.get('strategy','ema_long_flat_v1'):raise ValueError('Strategy snapshot mismatch')
        if snapshot['engine_version']=='spot-next-open-v2':
            from core.backtest.legacy_v2 import simulate as runner, BacktestConfig as config_type
            if snapshot['strategy']!='ema_long_flat_v1':raise ValueError('Unsupported legacy strategy')
            options={}
        result=runner([Candle.model_validate(b) for b in snapshot['dataset']],Instrument.model_validate(snapshot['instrument']),config_type.model_validate(request['config']),checkpoint=checkpoint,**options)
        finish(engine,job_id,owner,result=result)
    except Cancelled:finish(engine,job_id,owner)
    except LostLease:pass # Old worker may never publish after expiry/reclaim.
    except ValueError:finish(engine,job_id,owner,error='Research input/snapshot/engine version validation failed')
    except Exception:finish(engine,job_id,owner,error='Research computation failed; immutable input and engine version must be checked')
