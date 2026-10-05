"""Bounded research comparisons, each member remains independently recoverable."""
from uuid import uuid4
from sqlalchemy import select
from sqlalchemy.orm import Session
from core.research import jobs
from core.storage.models import ResearchJobRecord as Job

TERMINAL={'SUCCEEDED','FAILED','CANCELLED'}


def enqueue(engine,requests,bars,instrument):
    if not 2<=len(requests)<=8:raise ValueError('Require 2..8 variants')
    batch_id=str(uuid4())
    shared=jobs.digest({'dataset':[bar.model_dump(mode='json') for bar in bars],
                        'instrument':instrument.model_dump(mode='json'),
                        'symbol':requests[0]['symbol'],'timeframe':requests[0]['timeframe'],
                        'as_of':requests[0]['as_of']})
    members=[{**request,'batch_id':batch_id,'batch_slot':index,'shared_sha256':shared}
             for index,request in enumerate(requests)]
    jobs.enqueue_many(engine,members,bars,instrument)
    return read(engine,batch_id)


def members(session,batch_id,lock=False):
    query=select(Job).where(Job.request['batch_id'].as_string()==batch_id).order_by(Job.job_id)
    if lock:query=query.with_for_update()
    records=list(session.scalars(query))
    if not records:raise KeyError(batch_id)
    return sorted(records,key=lambda record:record.request['batch_slot'])


def read(engine,batch_id):
    with Session(engine) as session:
        records=members(session,batch_id)
        counts={state:sum(record.status==state for record in records)
                for state in ['QUEUED','RUNNING','SUCCEEDED','FAILED','CANCELLED']}
        rows=[]
        for record in records:
            result=record.result if record.status=='SUCCEEDED' else None
            rows.append({**jobs.view(record),'metrics':None if result is None else result['metrics'],
                         'analysis':None if result is None else {key:result['analysis'][key] for key in ['sharpe','closed_trade_count','warnings','unavailable_reasons']}})
        return {'batch_id':batch_id,'shared_sha256':records[0].request['shared_sha256'],
                'status':'COMPLETE' if all(record.status in TERMINAL for record in records) else 'ACTIVE',
                'counts':counts,'jobs':rows,'trading_enabled':False}


def cancel(engine,batch_id):
    # Stable lock order matches members; success/failure already committed is retained.
    with Session(engine) as session,session.begin():
        for record in members(session,batch_id,lock=True):
            if record.status not in TERMINAL:
                record.cancel_requested=True;record.updated_at=jobs.now()
                if record.status=='QUEUED':record.status='CANCELLED'
    return read(engine,batch_id)
