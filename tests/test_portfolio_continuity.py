from copy import deepcopy
from datetime import timedelta
from uuid import uuid4
import pytest
from fastapi.testclient import TestClient
from core.backtest.spot import digest
from core.portfolio import continuity,history
from core.portfolio.valuation import evaluate,stamp
from apps.api.main import create_app
from tests.test_integration import database
from tests.test_portfolio_history import setup


def shifted(base,minutes,*,price='21',unavailable=False):
    value=deepcopy(base);value['snapshot_id']=str(uuid4());value['request_id']=str(uuid4())
    value['created_at']=(stamp(base['created_at'])+timedelta(seconds=minutes+1)).isoformat()
    inputs=value['report']['inputs'];inputs['as_of']=(stamp(inputs['as_of'])+timedelta(minutes=minutes)).isoformat()
    inputs['price_as_of']=(stamp(inputs['price_as_of'])+timedelta(minutes=minutes)).isoformat()
    inputs['worker_heartbeat']=inputs['as_of']
    for account in inputs['accounts']:
        if unavailable:account['quote']=None;continue
        quote=account['quote']
        for key in ['open_time','close_time']:quote[key]=(stamp(quote[key])+timedelta(minutes=minutes)).isoformat()
        for key in ['open','high','low','close']:quote[key]=price
    value['report']=evaluate(inputs);value['report_sha256']=digest(value['report'])
    return value


def inputs(base,values,*,limit=8,earlier=False):
    return {'version':continuity.VERSION,'limit':limit,'has_earlier':earlier,'scenario':base['scenario'],'snapshots':values}


def base_snapshot(database,monkeypatch):
    engine,_,_=database;_,scenario=setup(engine,monkeypatch)
    return history.save(engine,scenario['scenario_id'],str(uuid4()))


def test_hand_equity_changes_and_segments(database,monkeypatch):
    base=base_snapshot(database,monkeypatch);second=shifted(base,1);third=shifted(base,2,price='22')
    result=continuity.evaluate(inputs(base,[base,second,third]))
    assert result['summary']=={'complete':3,'unavailable':0,'comparable_links':2,'breaks':0}
    assert [edge['equity_change'] for edge in result['edges']]==['142.856','142.856']
    assert result['segments'][0]['equity_change']=='285.712'
    assert result['segments'][0]['observations']==3
    assert continuity.verify(result)==result['summary']


@pytest.mark.parametrize('minutes,reason,missing',[(0,'PRICE_CLOCK_NOT_ADVANCING',0),(3,'OBSERVATION_GAP',2),(-1,'PRICE_CLOCK_NOT_ADVANCING',0)])
def test_clock_breaks_never_bridge(database,monkeypatch,minutes,reason,missing):
    base=base_snapshot(database,monkeypatch);other=shifted(base,minutes)
    # Storage order still advances even if input valuation clocks regress.
    other['created_at']=(stamp(base['created_at'])+timedelta(seconds=1)).isoformat()
    result=continuity.evaluate(inputs(base,[base,other]))
    edge=result['edges'][0]
    assert not edge['comparable'] and reason in edge['reasons']
    assert edge['equity_change'] is None and edge['unobserved_minutes']==missing
    assert len(result['segments'])==2


def test_unavailable_breaks_segment_without_zero_equity(database,monkeypatch):
    base=base_snapshot(database,monkeypatch)
    values=[base,shifted(base,1,unavailable=True),shifted(base,2),shifted(base,3,price='22')]
    result=continuity.evaluate(inputs(base,values))
    assert result['points'][1]['equity'] is None and result['points'][1]['segment_id'] is None
    assert result['summary']=={'complete':3,'unavailable':1,'comparable_links':1,'breaks':2}
    assert [segment['observations'] for segment in result['segments']]==[1,2]
    assert result['edges'][1]['equity_change'] is None


@pytest.mark.parametrize('mutation,reason',[
    ('revision','ACCOUNT_REVISION_REGRESSED'),('definition','ACCOUNT_DEFINITION_CHANGED'),
    ('rules','INSTRUMENT_RULES_CHANGED'),('observations','OBSERVATION_PREFIX_CHANGED'),('accepted','ACCEPTED_DATA_PREFIX_CHANGED'),
    ('worker','WORKER_HEALTH_WARNING')])
def test_source_changes_block_comparison(database,monkeypatch,mutation,reason):
    base=base_snapshot(database,monkeypatch);other=shifted(base,1)
    current=other['report']['inputs']['accounts'][0]
    if mutation=='revision':current['record']['revision']=0
    elif mutation=='definition':current['record']['snapshot']['request']['as_of']='2020-01-01T00:00:00+00:00'
    elif mutation=='rules':current['instrument']=None
    elif mutation=='observations':current['record']['observations']=[]
    elif mutation=='accepted':current['accepted']=[]
    elif mutation=='worker':other['report']['alerts'].append({'code':'WORKER_UNAVAILABLE','session_id':current['record']['session_id']})
    # Explicitly exercise source lineage checks on two already-established input reports.
    assert reason in continuity.reasons(base,other)
    assert continuity.reasons(base,other)==sorted(set(continuity.reasons(base,other)))


def test_empty_single_boundaries_and_tampering(database,monkeypatch):
    base=base_snapshot(database,monkeypatch)
    assert continuity.evaluate(inputs(base,[]))['segments']==[]
    result=continuity.evaluate(inputs(base,[base],limit=1,earlier=True))
    assert result['window']['has_earlier'] and result['segments'][0]['equity_change']=='0'
    damaged=deepcopy(result);damaged['summary']['complete']=999
    with pytest.raises(ValueError):continuity.verify(damaged)
    damaged=deepcopy(result);damaged['inputs']['snapshots'][0]['report_sha256']='0'*64
    with pytest.raises(ValueError):continuity.verify(damaged)
    for values in [[base,base],[shifted(base,1),base]]:
        with pytest.raises(ValueError):continuity.evaluate(inputs(base,values))
    with pytest.raises(ValueError):continuity.evaluate(inputs(base,[base],limit=0))
    import json
    source=inputs(base,[base])
    monkeypatch.setattr(continuity,'MAX_BYTES',len(json.dumps(source,ensure_ascii=False,separators=(',',':')).encode())+1)
    with pytest.raises(ValueError,match='32 MiB'):continuity.evaluate(source)
    monkeypatch.setattr(continuity,'MAX_BYTES',1)
    with pytest.raises(ValueError,match='32 MiB'):continuity.evaluate(inputs(base,[base]))


def test_db_window_api_and_no_current_recapture(database,monkeypatch):
    engine,_,settings=database;base=base_snapshot(database,monkeypatch)
    other=shifted(base,1);monkeypatch.setattr(history,'capture',lambda *args:other['report'])
    saved=history.save(engine,base['scenario_id'],str(uuid4()))
    monkeypatch.setattr(history,'capture',lambda *args:(_ for _ in ()).throw(AssertionError('No current recapture')))
    one=continuity.capture(engine,base['scenario_id'],limit=1)
    assert one['window']=={'limit':1,'has_earlier':True,'order':'STORAGE_TIME_ASC','points':1}
    assert one['points'][0]['snapshot_id']==saved['snapshot_id']
    with TestClient(create_app(settings)) as client:
        root='/api/v1/portfolio/scenarios/'+base['scenario_id']+'/analysis'
        response=client.get(root);assert response.status_code==200
        assert response.json()['edges'][0]['equity_change']=='142.856'
        assert client.get(root+'?limit=9').status_code==422
        assert client.get('/api/v1/portfolio/scenarios/'+str(uuid4())+'/analysis').status_code==404
        assert client.post(root,json={}).status_code==405


def test_reproduced_revision_regression_still_splits(database,monkeypatch):
    base=base_snapshot(database,monkeypatch);other=shifted(base,1)
    old=base['report']['inputs']['accounts'][0]['record']
    other['report']['inputs']['accounts'][0]['record']['revision']=old['revision']-1
    other['report']=evaluate(other['report']['inputs']);other['report_sha256']=digest(other['report'])
    assert other['report']['status']=='COMPLETE'
    result=continuity.evaluate(inputs(base,[base,other]))
    assert result['edges'][0]['reasons']==['ACCOUNT_REVISION_REGRESSED']
    assert result['edges'][0]['equity_change'] is None
    assert len(result['segments'])==2


def test_reproduced_retroactive_halt_does_not_fake_equity_change(database,monkeypatch):
    from types import SimpleNamespace
    from core.paper.streams import computed
    base=base_snapshot(database,monkeypatch);other=shifted(base,1)
    raw=other['report']['inputs']['accounts'][0]['record']
    assert raw['ledger']['fills']
    raw['halt_at']=0;raw['revision']+=1
    raw['ledger']=computed(SimpleNamespace(**raw))
    other['report']=evaluate(other['report']['inputs']);other['report_sha256']=digest(other['report'])
    assert other['report']['status']=='COMPLETE' and not raw['ledger']['fills']
    result=continuity.evaluate(inputs(base,[base,other]))
    assert result['edges'][0]['reasons']==['LEDGER_PREFIX_CHANGED']
    assert result['edges'][0]['equity_change'] is None
