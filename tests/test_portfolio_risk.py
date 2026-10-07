from copy import deepcopy
from uuid import uuid4
from types import SimpleNamespace
import json
import pytest
from fastapi.testclient import TestClient
from apps.api.main import create_app
from core.backtest.spot import digest
from core.paper.streams import computed
from core.portfolio import risk_history,continuity,history
from core.portfolio.valuation import evaluate
from tests.test_integration import database
from tests.test_portfolio_continuity import base_snapshot,shifted,inputs


@pytest.mark.parametrize('weight,status',[('0.8','NOT_TRIGGERED'),('0.800000000000000001','BREACH'),('0.799999999999999999','NOT_TRIGGERED')])
def test_strict_exact_thresholds(weight,status):
    report={'status':'COMPLETE','totals':{'equity':'100','gross_weight':weight},'exposures':[{'base':'BTC','weight':'0.6'}]}
    result=risk_history.rules(report,{'max_gross_weight':'0.8','max_asset_weight':'0.6'})
    assert result[0]['status']==status and result[1]['status']=='NOT_TRIGGERED'
    assert result[2]['value']=='0' and result[2]['status']=='NOT_TRIGGERED'


def test_zero_equity_never_looks_like_passed_ratio():
    result=risk_history.rules({'status':'COMPLETE','totals':{'equity':'0','gross_weight':None},'exposures':[]},{'max_gross_weight':'0.8','max_asset_weight':'0.6'})
    assert all(rule['status']=='UNAVAILABLE' and rule['value'] is None for rule in result)


def test_enter_exit_events_require_comparable_links(database,monkeypatch):
    base=base_snapshot(database,monkeypatch)
    low=shifted(base,1,price='0.000001');high=shifted(base,2,price='20')
    result=risk_history.evaluate(inputs(base,[base,low,high]))
    assert [event['event'] for event in result['events']]==['LEFT_BREACH','LEFT_BREACH','ENTERED_BREACH','ENTERED_BREACH']
    assert [event['rule_id'] for event in result['events']]==['GROSS_WEIGHT','ASSET_WEIGHT:BTC']*2
    assert result['policy']['mode']=='HINT_ONLY' and result['trading_enabled'] is False
    assert risk_history.verify(result)==result['summary']
    gap=risk_history.evaluate(inputs(base,[base,shifted(base,3,price='0.000001')]))
    assert gap['points'][1]['context']=='RESET' and not gap['events']
    assert 'OBSERVATION_GAP' in gap['points'][1]['reset_reasons']


def test_unavailable_never_clears_breaches(database,monkeypatch):
    base=base_snapshot(database,monkeypatch)
    result=risk_history.evaluate(inputs(base,[base,shifted(base,1,unavailable=True),shifted(base,2,price='0.000001')]))
    point=result['points'][1]
    assert point['status']=='UNKNOWN' and all(rule['value'] is None for rule in point['rules'])
    assert point['unavailable_reasons'] and result['events']==[]
    assert result['points'][2]['context']=='RESET'


def test_worker_warning_is_unknown_even_with_complete_valuation(database,monkeypatch):
    base=base_snapshot(database,monkeypatch);value=shifted(base,-1,price='20')
    source=value['report']['inputs'];source['worker_heartbeat']=None
    for account in source['accounts']:
        raw=account['record'];raw['status']='RUNNING';raw['halt_at']=None;raw['ledger']=computed(SimpleNamespace(**raw))
    value['report']=evaluate(source);value['report_sha256']=digest(value['report'])
    assert value['report']['status']=='COMPLETE'
    result=risk_history.evaluate(inputs(base,[value]))
    assert result['points'][0]['status']=='UNKNOWN'
    assert all(hint['code']=='WORKER_UNAVAILABLE' for hint in result['points'][0]['operational_hints'])


def test_policy_frozen_hash_and_tampering(database,monkeypatch):
    base=base_snapshot(database,monkeypatch);source=inputs(base,[base]);before=deepcopy(source)
    result=risk_history.evaluate(source)
    assert source==before and result['policy']['limits']==base['scenario']['definition']['limits']
    assert result['policy_sha256']==digest(result['policy'])
    assert risk_history.evaluate(source)==result
    for field in ['policy','points','events']:
        damaged=deepcopy(result)
        if field=='policy':damaged[field]['limits']['max_gross_weight']='1'
        elif field=='points':damaged[field][0]['status']='NO_CONFIGURED_HINTS'
        else:damaged[field].append({'event':'LEFT_BREACH'})
        with pytest.raises(ValueError):risk_history.verify(damaged)


def test_empty_window_and_bounds(database,monkeypatch):
    base=base_snapshot(database,monkeypatch)
    assert risk_history.evaluate(inputs(base,[]))['summary']['unknown']==0
    source=inputs(base,[base],limit=1,earlier=True)
    result=risk_history.evaluate(source)
    assert result['window']['has_earlier'] and result['points'][0]['context']=='BASELINE'
    old_size=len(json.dumps(continuity.evaluate(source),ensure_ascii=False,separators=(',',':')).encode())
    monkeypatch.setattr(continuity,'MAX_BYTES',old_size+1)
    with pytest.raises(ValueError,match='Risk export'):risk_history.evaluate(source)


def test_api_no_recapture_or_mutation(database,monkeypatch):
    engine,_,settings=database;base=base_snapshot(database,monkeypatch)
    monkeypatch.setattr(history,'capture',lambda *args:(_ for _ in ()).throw(AssertionError('No current recapture')))
    with TestClient(create_app(settings)) as client:
        path='/api/v1/portfolio/scenarios/'+base['scenario_id']+'/risk'
        response=client.get(path);assert response.status_code==200
        risk_history.verify(response.json())
        assert client.get(path+'?limit=9').status_code==422
        assert client.post(path,json={}).status_code==405
        assert client.get('/api/v1/portfolio/scenarios/'+str(uuid4())+'/risk').status_code==404
    assert history.saved(engine,base['scenario_id'],base['snapshot_id'])==base
