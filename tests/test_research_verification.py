from copy import deepcopy
from uuid import uuid4
import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session
from sqlalchemy.orm.attributes import flag_modified
from core.research import jobs
from core.research.verification import verify
from core.storage.models import ResearchJobRecord as Job
from apps.api.main import create_app
from tests.test_integration import database
from tests.test_backtest import liquid,rules,cfg


def retained(kind=None,version=None):
    request={'config':cfg().model_dump(mode='json'),'strategy':'ema_long_flat_v1','parameters':{'period':2}}
    data=liquid([10,12,14,16,30,28,25,27])
    from core.backtest.spot import simulate
    options={'strategy_id':request['strategy'],'parameters':request['parameters']}
    if kind=='holdout':
        from core.backtest.holdout import simulate
        request.update(research_type=kind,train_bars=4);options['train_bars']=4
    elif kind=='walk_forward':
        from core.backtest.walk_forward import simulate
        request.update(research_type=kind,grid=[{'period':2},{'period':3}],train_bars=4,test_bars=4)
        options={'strategy_id':request['strategy'],'grid':request['grid'],'train_bars':4,'test_bars':4}
    elif version=='spot-next-open-v2':
        from core.backtest.legacy_v2 import simulate
        options={}
    result=simulate(data,rules(),cfg(),**options)
    snapshot={'request_sha256':jobs.digest(request),'engine_version':version or jobs.ENGINES[kind],'strategy':request['strategy'],'dataset':[b.model_dump(mode='json') for b in data],'instrument':rules().model_dump(mode='json')}
    snapshot['sha256']=jobs.digest(snapshot)
    return request,snapshot,result


@pytest.mark.parametrize('kind,version',[(None,None),('holdout',None),('walk_forward',None),(None,'spot-next-open-v2')])
def test_supported_replay(kind,version):
    inputs=retained(kind,version);before=deepcopy(inputs)
    result=verify(*inputs)
    assert result['consistency_verified'] and result['run_id']==inputs[2]['run_id']
    assert result['provenance_authenticated'] is False and result['trading_enabled'] is False
    assert inputs==before


@pytest.mark.parametrize('target',['request','snapshot','result','type_substitution','resealed_input'])
def test_altered_retained_inputs_or_result(target):
    request,snapshot,result=retained()
    if target=='request':request['config']['period']=3
    elif target=='snapshot':snapshot['dataset'][0]['volume']='1'
    elif target=='result':result['metrics']['fees']='999'
    elif target=='type_substitution':result['trading_enabled']=0
    else:
        snapshot['dataset'][0]['volume']='1'
        snapshot['sha256']=jobs.digest({k:v for k,v in snapshot.items() if k!='sha256'})
    with pytest.raises(ValueError):verify(request,snapshot,result)


def test_api_replay_is_readonly_and_rejects_corrupt_success(database):
    engine,_,settings=database
    request,_,_=retained()
    job_id=jobs.enqueue(engine,request,liquid([10,12,14,16,30,28,25,27]),rules())['job_id']
    with TestClient(create_app(settings)) as client:
        path=f'/api/v1/research/jobs/{job_id}/verify'
        assert client.post(path).status_code==409
        owner=str(uuid4());jobs.claim(engine,owner);jobs.execute(engine,job_id,owner)
        with Session(engine) as session:
            job=session.get(Job,job_id);before=(job.updated_at,deepcopy(job.result),deepcopy(job.snapshot))
        response=client.post(path)
        assert response.status_code==200 and response.headers['cache-control']=='no-store'
        assert response.json()['consistency_verified'] is True
        with Session(engine) as session,session.begin():
            job=session.get(Job,job_id);assert (job.updated_at,job.result,job.snapshot)==before
            job.result={**job.result,'trading_enabled':0}
            # SQLAlchemy's JSON dirty comparison also treats False == 0; force the
            # intended corrupt value to be persisted so this exercises the API.
            flag_modified(job,'result')
        rejected=client.post(path)
        assert rejected.status_code==409 and rejected.json()['detail']=='Retained research verification failed'
        assert client.post(f'/api/v1/research/jobs/{uuid4()}/verify').status_code==404
        assert client.post('/api/v1/research/jobs/invalid/verify').status_code==422


@pytest.mark.parametrize('change',['oversize_dataset','unknown_engine','missing_manifest','nonfinite','extra_snapshot','wrong_strategy'])
def test_verification_bounds_and_envelopes(change):
    request,snapshot,result=retained()
    if change=='oversize_dataset':snapshot['dataset']=[{}]*1001
    elif change=='unknown_engine':snapshot['engine_version']='unknown'
    elif change=='missing_manifest':result.pop('manifest')
    elif change=='nonfinite':result['bad']=float('nan')
    elif change=='extra_snapshot':snapshot['extra']=True
    else:snapshot['strategy']='sma_long_flat_v1'
    snapshot['sha256']=jobs.digest({k:v for k,v in snapshot.items() if k!='sha256'})
    with pytest.raises(ValueError):verify(request,snapshot,result)
