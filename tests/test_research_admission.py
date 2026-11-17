from uuid import uuid4
import pytest
from sqlalchemy import select,func
from sqlalchemy.orm import Session
from core.research import jobs
from core.storage.models import ResearchJobRecord as Job
from tests.test_integration import database
from tests.test_backtest import cfg,liquid,rules


def request(**updates):return {'config':cfg().model_dump(mode='json'),**updates}


@pytest.mark.parametrize('change',[{'research_type':'unknown'},{'research_type':False},{'research_type':[]},{'strategy':'custom_python'},{'parameters':{'period':3}},{'config':{'period':True}}])
def test_reject_before_database_access(change):
    with pytest.raises(ValueError):jobs.enqueue(None,request(**change),liquid([10,12,14]),rules())


@pytest.mark.parametrize('kind',['oversize','unclosed','foreign','perpetual','gap'])
def test_reject_invalid_source_before_database(kind):
    series=liquid([10,12,14]);instrument=rules()
    if kind=='oversize':series=liquid(list(range(10,5011)))
    elif kind=='unclosed':series[0]=series[0].model_copy(update={'is_closed':False})
    elif kind=='foreign':series[0]=series[0].model_copy(update={'instrument_id':'other'})
    elif kind=='perpetual':instrument=instrument.model_copy(update={'market_type':'PERPETUAL'})
    else:series.pop(1)
    with pytest.raises(ValueError):jobs.enqueue(None,request(),series,instrument)


def test_invalid_batch_member_leaves_queue_empty(database):
    engine,_,_=database
    with pytest.raises(ValueError):jobs.enqueue_many(engine,[request(),request(research_type='unknown')],liquid([10,12,14]),rules())
    with Session(engine) as session:assert session.scalar(select(func.count()).select_from(Job))==0


@pytest.mark.parametrize('kind,version',[('unknown',jobs.VERSION),('holdout',jobs.VERSION),(None,'spot-holdout-v1')])
def test_resealed_type_engine_mismatch_never_computes(database,monkeypatch,kind,version):
    engine,_,_=database
    job_id=jobs.enqueue(engine,request(),liquid([10,12,14]),rules())['job_id']
    with Session(engine) as session,session.begin():
        record=session.get(Job,job_id)
        altered={**record.request,'research_type':kind}
        snapshot={**record.snapshot,'request_sha256':jobs.digest(altered),'engine_version':version}
        snapshot['sha256']=jobs.digest({k:v for k,v in snapshot.items() if k!='sha256'})
        record.request=altered;record.snapshot=snapshot
    def forbidden(*args,**kwargs):pytest.fail('Invalid snapshot reached computation')
    monkeypatch.setattr(jobs,'simulate',forbidden)
    owner=str(uuid4());assert jobs.claim(engine,owner)==job_id;jobs.execute(engine,job_id,owner)
    with Session(engine) as session:
        record=session.get(Job,job_id);assert record.status=='FAILED' and record.result is None


@pytest.mark.parametrize('invalid',[{},None,request(research_type='holdout'),request(research_type='walk_forward'),request(research_type='walk_forward',grid=[{'period':2},{'period':2}],train_bars=4,test_bars=4)])
def test_specialized_admission_preflight(invalid):
    with pytest.raises(ValueError):jobs.enqueue(None,invalid,liquid([10,12,14,16,30,28,25,27]),rules())
