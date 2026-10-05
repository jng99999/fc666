from copy import deepcopy
from fractions import Fraction
from fastapi.encoders import jsonable_encoder
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session
import pytest
from apps.api.main import create_app
from core.backtest.spot import simulate,digest
from core.replay import sessions
from core.storage.models import ReplaySessionRecord as Replay
from tests.test_integration import database
from tests.test_backtest import liquid,rules,cfg
from tests.test_replay import request
from scripts.verify_replay_prefix import verify


def configured(strategy='ema_long_flat_v1',parameters=None):
    return {**request(),'strategy':strategy,'parameters':parameters or ({'period':2} if strategy=='ema_long_flat_v1' else {'fast':2,'slow':3})}


def test_ema_events_match_backtest_decisions_and_reached_prefix(database):
    engine,_,_=database;bars=liquid([10,12,14,16,12,11,20])
    created=sessions.create(engine,configured(),bars,rules());session_id=created['session_id']
    assert created['decisions']==[] and created['manifest']['parameters']=={'period':2}
    for count in range(1,8):
        view=sessions.command(engine,session_id,count-1,'step')
        expected=[] if count<2 else simulate(bars[:count],rules(),cfg())['signals']
        assert [{'target':event['target'],'available_at':event['available_at']} for event in view['decisions']]==expected
        assert len({event['event_id'] for event in view['decisions']})==len(expected)
        assert all(event['execution']=='NOT_IMPLEMENTED' for event in view['decisions'])
        assert sessions.read(engine,session_id)['decisions']==view['decisions']
        assert verify(jsonable_encoder(view))=={'bars':count,'decisions':len(expected)}
    last=list(view['decisions'])
    reset=sessions.command(engine,session_id,view['revision'],'reset')
    assert reset['decisions']==[]
    replayed=sessions.command(engine,session_id,reset['revision'],'step',7)
    assert replayed['decisions']==last


def test_sma_independent_fraction_reference_and_stable_batch_step(database):
    engine,_,_=database;values=[10,12,14,10,9,16,17];bars=liquid(values)
    first=sessions.create(engine,configured('sma_long_flat_v1'),bars,rules())
    second=sessions.create(engine,configured('sma_long_flat_v1'),bars,rules())
    assert first['manifest']['snapshot_sha256']==second['manifest']['snapshot_sha256']
    for index in range(7):incremental=sessions.command(engine,first['session_id'],index,'step')
    batch=sessions.command(engine,second['session_id'],0,'step',7)
    assert incremental['decisions']==batch['decisions']
    for index,event in enumerate(batch['decisions'],start=2):
        fast=sum(map(Fraction,values[index-1:index+1]))/2
        slow=sum(map(Fraction,values[index-2:index+1]))/3
        assert event['target']==('LONG' if fast>slow else 'FLAT')
        assert event['available_at']==bars[index].close_time.isoformat()
        assert event['strategy_version']==1 and event['parameters']=={'fast':2,'slow':3}


def test_future_prices_do_not_change_reached_decision_payloads(database):
    engine,_,_=database
    first=sessions.create(engine,configured(),liquid([10,12,14,16,12,11,20]),rules())
    second=sessions.create(engine,configured(),liquid([10,12,14,16,120,110,200]),rules())
    a=sessions.command(engine,first['session_id'],0,'step',4)
    b=sessions.command(engine,second['session_id'],0,'step',4)
    assert [{k:v for k,v in event.items() if k!='event_id'} for event in a['decisions']]==[{k:v for k,v in event.items() if k!='event_id'} for event in b['decisions']]
    assert a['manifest']['snapshot_sha256']!=b['manifest']['snapshot_sha256']


def test_old_v1_snapshot_keeps_original_price_only_contract(database):
    engine,_,_=database;created=sessions.create(engine,request(),liquid([10,12,14]),rules())
    with Session(engine) as session,session.begin():
        record=session.get(Replay,created['session_id']);snapshot=deepcopy(record.snapshot)
        snapshot['version']='closed-bar-replay-v1';snapshot['request']=request()
        snapshot['sha256']=digest({key:value for key,value in snapshot.items() if key!='sha256'})
        record.snapshot=snapshot
    restored=sessions.command(engine,created['session_id'],0,'step',2)
    assert restored['manifest']['version']=='closed-bar-replay-v1'
    assert 'decisions' not in restored and 'strategy' not in restored['manifest']
    assert restored['cursor']==2 and len(restored['candles'])==2


@pytest.mark.parametrize('fields',[
    {'strategy':'custom_python'}, {'strategy':None,'parameters':{}},
    {'strategy':'sma_long_flat_v1','parameters':{'fast':True,'slow':3}},
    {'strategy':'sma_long_flat_v1','parameters':{'fast':3,'slow':2}},
    {'strategy':'ema_long_flat_v1','parameters':{'period':3}},
    {'strategy':'ema_long_flat_v1','parameters':{'period':2,'extra':1}},
])
def test_bad_strategy_configuration_rejected_at_api(database,fields):
    _,_,settings=database
    with TestClient(create_app(settings)) as client:
        assert client.post('/api/v1/replay/sessions',json={**request(),**fields}).status_code==422


def test_prefix_verifier_rejects_target_indicator_and_unreached_data_tampering(database):
    engine,_,_=database;created=sessions.create(engine,configured(),liquid([10,12,14,16,12,11,20]),rules())
    view=jsonable_encoder(sessions.command(engine,created['session_id'],0,'step',4))
    assert verify(view)=={'bars':4,'decisions':3}
    changed=deepcopy(view);changed['decisions'][0]['target']='FLAT'
    with pytest.raises(ValueError,match='differ'):verify(changed)
    changed=deepcopy(view);changed['indicators'][-1]['values']['ema']=99
    with pytest.raises(ValueError,match='differ'):verify(changed)
    changed=deepcopy(view);changed['clock']=view['candles'][0]['close_time']
    with pytest.raises(ValueError):verify(changed)
    changed=deepcopy(view);changed['candles'].append(changed['candles'][-1])
    with pytest.raises(ValueError,match='length'):verify(changed)
    empty=jsonable_encoder(sessions.command(engine,created['session_id'],1,'reset'))
    assert verify(empty)=={'bars':0,'decisions':0}
