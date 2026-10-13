from uuid import uuid4
import pytest
from sqlalchemy.orm import Session
from core.research import jobs
from core.storage.models import ResearchJobRecord as Job
from tests.test_integration import database
from tests.test_backtest import liquid,rules,cfg


@pytest.mark.parametrize('damage',['null','extra','oversize_dataset','missing_dataset','wrong_hash','oversize_input'])
def test_poison_task_failed_and_next_task_succeeds(database,damage):
    engine,_,_=database
    request={'config':cfg().model_dump(mode='json')}
    first=jobs.enqueue(engine,request,liquid([10,12,14]),rules())['job_id']
    second=jobs.enqueue(engine,request,liquid([10,12,14]),rules())['job_id']
    with Session(engine) as session,session.begin():
        record=session.get(Job,first);snapshot=dict(record.snapshot)
        if damage=='null':snapshot=None
        elif damage=='extra':snapshot['extra']=True
        elif damage=='oversize_dataset':snapshot['dataset']=[{}]*1001
        elif damage=='missing_dataset':snapshot.pop('dataset')
        elif damage=='wrong_hash':snapshot['sha256']='wrong'
        else:snapshot['instrument']={**snapshot['instrument'],'extra':'x'*jobs.MAX_INPUT_BYTES}
        if isinstance(snapshot,dict) and damage!='wrong_hash':snapshot['sha256']=jobs.digest({k:v for k,v in snapshot.items() if k!='sha256'})
        # The DB column is NOT NULL; JSON null is a valid non-SQL-null corruption.
        if snapshot is None:
            from sqlalchemy import update,JSON
            session.execute(update(Job).where(Job.job_id==first).values(snapshot=JSON.NULL))
        else:record.snapshot=snapshot
    owner=str(uuid4());assert jobs.claim(engine,owner)==first
    jobs.execute(engine,first,owner)
    with Session(engine) as session:
        record=session.get(Job,first);assert record.status=='FAILED' and record.result is None
        assert 'validation failed' in record.error
    assert jobs.claim(engine,owner)==second;jobs.execute(engine,second,owner)
    with Session(engine) as session:assert session.get(Job,second).status=='SUCCEEDED'


def test_snapshot_nonfinite_rejected_without_database():
    request={'config':{}}
    snapshot={k:None for k in jobs.SNAPSHOT_FIELDS};snapshot['dataset']=[{},{}];snapshot['instrument']={'bad':float('nan')}
    with pytest.raises(ValueError):jobs.validate_snapshot(request,snapshot)


def test_oversized_second_member_rolls_back_batch(database):
    from sqlalchemy import select,func
    engine,_,_=database
    valid={'config':cfg().model_dump(mode='json')}
    oversized={**valid,'extra':'x'*jobs.MAX_INPUT_BYTES}
    with pytest.raises(ValueError):jobs.enqueue_many(engine,[valid,oversized],liquid([10,12,14]),rules())
    with Session(engine) as session:assert session.scalar(select(func.count()).select_from(Job))==0
