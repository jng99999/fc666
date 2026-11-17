"""Synthetic quotes/news verify explicit arrival policy without changing old accounts."""
from datetime import timedelta
from decimal import Decimal

from fastapi.testclient import TestClient

from apps.api.directional_service import create_app
from tests.test_directional_price_action_service import setup_rule, read
from tests.test_directional_service import ROOT, body


def restart(tmp_path, scene, client):
    app=create_app(tmp_path/'paper.sqlite3',provider=scene.quote,bar_provider=lambda *args:scene.rows,
        news_provider=scene.news,news_source='TEST_CALENDAR',history_settlement_seconds=60,
        background=False,secure=False,clock=scene.clock)
    other=TestClient(app);other.cookies.update(client.cookies)
    return app,other


def test_new_account_freezes_arrival_policy_and_old_account_cannot_silently_switch(tmp_path):
    scene,old_app,client,old_id=setup_rule(tmp_path)
    old_app.state.poll_once();assert read(client,old_id)['pending'] is not None
    scene.now+=timedelta(seconds=61)
    app,other=restart(tmp_path,scene,client)
    request=body('PRICE_ACTION',instrument='BTCUSDT',rule='TREND_PULLBACK',timeframe='1h',
                 quantity='1',stop_loss_percent='.05',take_profit_percent='.1')
    response=other.post(ROOT+'/accounts',json=request)
    assert response.status_code==201,response.text
    new=response.json();assert new['metadata']['history_settlement_seconds']==60
    assert new['metadata']['history_policy']=='minimum-arrival-wait-v2'
    app.state.refresh_rules_once();app.state.poll_once()
    old=read(other,old_id);new=read(other,new['account_id'])
    assert old['entry_gate']=='HISTORY_POLICY_MISMATCH'
    assert old['analysis']['reason']=='HISTORY_POLICY_MISMATCH'
    assert old['pending'] is None and old['fills_count']==0
    assert old['metadata']['history_settlement_seconds']==0
    assert new['pending']['target']=='LONG'
    scene.step(app)
    assert read(other,new['account_id'])['fills_count']==1
    assert read(other,old_id)['fills_count']==0


def test_old_policy_position_keeps_stop_loss_after_runtime_policy_changes(tmp_path):
    scene,old_app,client,identity=setup_rule(tmp_path)
    old_app.state.poll_once();scene.step(old_app)
    position=read(client,identity)['position'];assert position is not None
    scene.now+=timedelta(seconds=61)
    app,other=restart(tmp_path,scene,client)
    scene.news_state='HIGH'
    trigger=Decimal(position['stop_loss'])
    scene.step(app,trigger-1,trigger)
    result=read(other,identity)
    assert result['position'] is None and result['fills_count']==2
    assert result['fills'][-1]['reason']=='STOP_LOSS'
    assert result['pending'] is None


def test_gold_account_does_not_inherit_exchange_history_delay(tmp_path):
    scene,_,client,_=setup_rule(tmp_path,instrument='XAU/USD')
    app,other=restart(tmp_path,scene,client)
    response=other.post(ROOT+'/accounts',json=body('PRICE_ACTION',instrument='XAU/USD',
        rule='TREND_PULLBACK',timeframe='1m',quantity='1',stop_loss_percent='.05',take_profit_percent='.1'))
    assert response.status_code==201,response.text
    assert response.json()['metadata']['history_settlement_seconds']==0
    app.state.refresh_rules_once();app.state.poll_once()
    assert read(other,response.json()['account_id'])['pending']['target']=='LONG'
