"""Cross-market summaries project JSON fields without loading full frozen reports."""
from sqlalchemy import select, update
from sqlalchemy.orm import Session
from core.storage.models import ResearchJobRecord as Job
from core.research import jobs

TERMINAL={'SUCCEEDED','FAILED','CANCELLED'}

def read(engine,matrix_id):
    with Session(engine) as session:
        rows=session.execute(select(Job.job_id,Job.status,Job.progress,Job.cancel_requested,Job.error,Job.request,
            Job.result['run_id'].as_string().label('run_id'),Job.result['metrics'].label('metrics'),
            Job.result['analysis']['sharpe'].label('sharpe'),
            Job.result['analysis']['closed_trade_count'].label('closed_trade_count'))
            .where(Job.request['matrix_id'].as_string()==matrix_id)).mappings().all()
    if not rows:raise KeyError(matrix_id)
    records=sorted([dict(row) for row in rows],key=lambda row:row['request']['matrix_slot'])
    return {'matrix_id':matrix_id,'request_sha256':records[0]['request']['matrix_request_sha256'],
            'scope':records[0]['request']['matrix_scope'],'status':'COMPLETE' if all(row['status'] in TERMINAL for row in records) else 'ACTIVE',
            'jobs':records,'trading_enabled':False}

def cancel(engine,matrix_id):
    with Session(engine) as session,session.begin():
        rows=session.execute(select(Job.job_id,Job.status).where(Job.request['matrix_id'].as_string()==matrix_id).order_by(Job.job_id).with_for_update()).all()
        if not rows:raise KeyError(matrix_id)
        for job_id,status in rows:
            if status not in TERMINAL:
                session.execute(update(Job).where(Job.job_id==job_id).values(cancel_requested=True,updated_at=jobs.now(),
                    **({'status':'CANCELLED'} if status=='QUEUED' else {})))
    return read(engine,matrix_id)
