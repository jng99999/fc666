from copy import deepcopy
from decimal import Decimal
import pytest
from fastapi.testclient import TestClient
from apps.api.main import create_app
from core.portfolio import sampled,continuity,history
from tests.test_integration import database
from tests.test_portfolio_continuity import base_snapshot,shifted,inputs


def points(equities):return [{'snapshot_id':str(index),'equity':value} for index,value in enumerate(equities)]


def test_hand_drawdowns_with_distinct_peak_trough_pairs():
    result=sampled.measure(points(['100','50','1000','700','1000']))
    assert result['max_drawdown_amount']=='300'
    assert result['max_drawdown_ratio']=='0.5'
    assert result['amount_interval']=={'peak_snapshot_id':'2','trough_snapshot_id':'3'}
    assert result['ratio_interval']=={'peak_snapshot_id':'0','trough_snapshot_id':'1'}


@pytest.mark.parametrize('values,status,amount,ratio,reason',[
    (['10'],'INSUFFICIENT_OBSERVATIONS',None,None,'INSUFFICIENT_OBSERVATIONS'),
    (['0'],'INSUFFICIENT_OBSERVATIONS',None,None,'INSUFFICIENT_OBSERVATIONS'),
    (['0','0'],'OBSERVED_POINTS','0',None,'NO_POSITIVE_PEAK'),
    (['0','10'],'OBSERVED_POINTS','0','0',None),
    (['10','0'],'OBSERVED_POINTS','10','1',None),
    (['10','10'],'OBSERVED_POINTS','0','0',None),
    (['10','20','30'],'OBSERVED_POINTS','0','0',None),
])
def test_zero_single_and_increasing_cases(values,status,amount,ratio,reason):
    result=sampled.measure(points(values))
    assert (result['measurement_status'],result['max_drawdown_amount'],result['max_drawdown_ratio'],result['ratio_unavailable_reason'])==(status,amount,ratio,reason)


def test_precision_and_invalid_equity():
    result=sampled.measure(points(['1000000000000000000.000000000000000002','1000000000000000000.000000000000000001']))
    assert result['max_drawdown_amount']=='0.000000000000000001'
    assert Decimal(result['max_drawdown_ratio'])>0
    for value in ['-1','NaN','Infinity']:
        with pytest.raises(ValueError):sampled.measure(points([value]))
    assert sampled.measure([]) is None


def test_segment_metrics_and_plot_never_bridge_unavailable(database,monkeypatch):
    base=base_snapshot(database,monkeypatch)
    values=[base,shifted(base,1,price='18'),shifted(base,2,unavailable=True),shifted(base,3,price='22'),shifted(base,4,price='20')]
    source=inputs(base,values);legacy=continuity.evaluate(source)
    result=sampled.evaluate(source)
    assert continuity.evaluate(source)==legacy
    assert result['points'][2]['plot_height'] is None and result['points'][2]['equity'] is None
    assert [segment['sampled']['observations'] for segment in result['segments']]==[2,2]
    assert [segment['sampled']['max_drawdown_amount'] for segment in result['segments']]==['285.712','285.712']
    assert all(Decimal(point['plot_height'])>=0 and Decimal(point['plot_height'])<=1 for point in result['points'] if point['plot_height'] is not None)
    assert sampled.verify(result)==result['summary']
    damaged=deepcopy(result);damaged['segments'][0]['sampled']['max_drawdown_amount']='0'
    with pytest.raises(ValueError):sampled.verify(damaged)


def test_empty_single_and_truncated_window(database,monkeypatch):
    base=base_snapshot(database,monkeypatch)
    assert sampled.evaluate(inputs(base,[]))['plot']['low_equity'] is None
    result=sampled.evaluate(inputs(base,[base],limit=1,earlier=True))
    assert result['window']['has_earlier']
    assert result['points'][0]['plot_height']=='0.5'
    assert result['segments'][0]['sampled']['max_drawdown_ratio'] is None


def test_readonly_api_export_size_and_reproduction(database,monkeypatch):
    engine,_,settings=database;base=base_snapshot(database,monkeypatch)
    before=history.saved(engine,base['scenario_id'],base['snapshot_id'])
    monkeypatch.setattr(history,'capture',lambda *args:(_ for _ in ()).throw(AssertionError('No market recapture')))
    with TestClient(create_app(settings)) as client:
        path='/api/v1/portfolio/scenarios/'+base['scenario_id']+'/sampled'
        response=client.get(path);assert response.status_code==200
        sampled.verify(response.json())
        assert client.get(path+'?limit=9').status_code==422
        assert client.post(path,json={}).status_code==405
    assert history.saved(engine,base['scenario_id'],base['snapshot_id'])==before
    import json
    source=inputs(base,[base])
    old_size=len(json.dumps(continuity.evaluate(source),ensure_ascii=False,separators=(',',':')).encode())
    monkeypatch.setattr(continuity,'MAX_BYTES',old_size+1)
    with pytest.raises(ValueError,match='Sampled export'):sampled.evaluate(source)
    monkeypatch.setattr(continuity,'MAX_BYTES',1)
    with pytest.raises(ValueError):sampled.evaluate(inputs(base,[base]))
