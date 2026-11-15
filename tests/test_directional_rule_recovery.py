"""History revision recovery with temporary ledgers and explicit synthetic markets."""
from datetime import timedelta
from decimal import Decimal as D
import sqlite3
from uuid import uuid4

from fastapi.testclient import TestClient
import pytest

from apps.api.directional_service import create_app
from core.paper.price_action_rules import RuleBar, analyze
from core.paper.directional import Proposal
from tests.test_directional_price_action_service import setup_rule, read
from tests.test_directional_service import ROOT


def revise(scene, app):
    rows=list(scene.rows)
    rows[10]=rows[10].model_copy(update={'close':rows[10].close+D('.01')})
    scene.rows=rows
    scene.now+=timedelta(seconds=1)
    app.state.refresh_rules_once()


def cross(scene, up):
    rows=list(scene.rows)
    result=analyze(rows,instrument='BTCUSDT',source='bitget.public',timeframe='1h',
                   now=rows[-1].close_time,rule='TREND_PULLBACK')
    mean=D(result['evidence']['ema20']).quantize(D('.000001'))
    opened=rows[-1].close_time
    rows.append(RuleBar(instrument='BTCUSDT',source='bitget.public',timeframe='1h',
        open_time=opened,close_time=opened+timedelta(hours=1),open=mean+(D('-.2') if up else D('.2')),
        close=mean+(D('.6') if up else D('-.6')),high=mean+(D('.8') if up else D('.5')),
        low=mean-(D('.5') if up else D('.8')),volume=None,is_closed=True))
    scene.rows=rows;scene.now=rows[-1].close_time+timedelta(seconds=1)
    scene.bid,scene.ask=str(rows[-1].close-D('.01')),str(rows[-1].close+D('.01'))


def test_revision_cancels_queued_entry_before_next_quote_and_records_only_once(tmp_path):
    scene,app,client,identity=setup_rule(tmp_path)
    app.state.poll_once();before=read(client,identity)
    assert before['pending']['target']=='LONG' and before['fills_count']==0
    revise(scene,app)
    guarded=read(client,identity)
    assert guarded['pending'] is None and guarded['last_outcome']['reason']=='HISTORY_REVISED'
    assert guarded['rule_history']['blocked'] is True
    revision=guarded['revision']
    scene.step(app);scene.step(app)
    final=read(client,identity)
    assert final['fills_count']==0 and final['revision']==revision


@pytest.mark.parametrize('direction',['LONG','SHORT'])
@pytest.mark.parametrize('kind',['STOP_LOSS','TAKE_PROFIT'])
def test_history_quarantine_and_high_news_never_disable_existing_brackets(tmp_path,direction,kind):
    scene,app,client,identity=setup_rule(tmp_path,direction=direction)
    app.state.poll_once();scene.step(app)
    position=read(client,identity)['position']
    revise(scene,app);scene.news_state='HIGH'
    trigger=D(position['stop_loss' if kind=='STOP_LOSS' else 'take_profit'])
    if direction=='LONG':scene.step(app,trigger,trigger+D('.02'))
    else:scene.step(app,trigger-D('.02'),trigger)
    result=read(client,identity)
    assert result['position'] is None and result['fills_count']==2
    assert result['fills'][-1]['reason']==kind and result['rule_history']['blocked'] is True


def test_reduce_only_flat_survives_history_quarantine(tmp_path):
    scene,app,client,identity=setup_rule(tmp_path)
    app.state.poll_once();scene.step(app);revise(scene,app)
    account=read(client,identity)
    response=client.post(ROOT+'/accounts/'+identity+'/decision',json={
        'request_id':str(uuid4()),'expected_revision':account['revision'],'target':'FLAT',
        'quantity':'0','stop_loss':None,'take_profit':None})
    assert response.status_code==200,response.text
    scene.step(app)
    result=read(client,identity)
    assert result['position'] is None and result['fills'][-1]['reason']=='SIGNAL_EXIT'


@pytest.mark.parametrize('advance_clock', [False, True])
def test_restart_never_fills_an_old_unfilled_rule_intent(tmp_path, advance_clock):
    scene,app,client,identity=setup_rule(tmp_path)
    app.state.poll_once();assert read(client,identity)['pending'] is not None
    if advance_clock:
        scene.now+=timedelta(seconds=1)
    restarted=create_app(tmp_path/'paper.sqlite3',provider=scene.quote,bar_provider=lambda *args:scene.rows,
        news_provider=scene.news,news_source='TEST_CALENDAR',background=False,secure=False,clock=scene.clock)
    other=TestClient(restarted);other.cookies.update(client.cookies)
    restarted.state.refresh_rules_once();scene.step(restarted)
    result=read(other,identity)
    assert result['pending'] is None and result['fills_count']==0
    assert result['last_outcome']['reason']=='HISTORY_RESTART_RECHECK'


def test_same_start_clock_does_not_cancel_a_new_intent_created_by_this_process(tmp_path):
    scene,app,client,identity=setup_rule(tmp_path)
    # setup and the first automatic decision deliberately share one clock value.
    app.state.poll_once();queued=read(client,identity)
    assert queued['pending'] is not None
    app.state.refresh_rules_once()
    assert read(client,identity)['pending']['request_id']==queued['pending']['request_id']
    scene.step(app)
    assert read(client,identity)['fills_count']==1


def test_stale_internal_producer_cannot_bypass_a_durable_history_fence(tmp_path):
    scene,app,client,identity=setup_rule(tmp_path)
    original=list(scene.rows)
    app.state.store.on_quote(identity,scene.quote('BTCUSDT'),now=scene.now,news=scene.news('BTCUSDT',scene.now))
    revise(scene,app)
    view=read(client,identity)
    analysis=analyze(original,instrument='BTCUSDT',source='bitget.public',timeframe='1h',
                     now=scene.now,rule='TREND_PULLBACK')
    result=app.state.store.rule_decision(identity,analysis,bar_sha256='a'*64,
        expected_revision=view['revision'],now=scene.now,news=scene.news('BTCUSDT',scene.now),
        proposal=Proposal(request_id='stale-worker-before-revision',target='LONG',available_at=scene.now,
                          quantity='1',stop_loss='90',take_profit='120'))
    assert result['pending'] is None and result['fills_count']==0
    assert result['rule_analysis']['decision_status']=='HISTORY_RESYNC_PENDING'


def test_durable_resume_barrier_survives_restart_before_any_recovered_quote(tmp_path):
    scene,app,client,identity=setup_rule(tmp_path)
    app.state.poll_once();revise(scene,app)
    cross(scene,False);cross(scene,True)
    app.state.refresh_rules_once()
    assert read(client,identity)['rule_history']['blocked'] is True
    scene.now+=timedelta(seconds=5);app.state.refresh_rules_once()
    resumed=read(client,identity)
    assert resumed['rule_history']['blocked'] is False
    assert resumed['rule_history']['resume_after']==scene.rows[-1].close_time.isoformat()
    assert resumed['analysis']['available_at']!=resumed['rule_history']['resume_after']
    restarted=create_app(tmp_path/'paper.sqlite3',provider=scene.quote,bar_provider=lambda *args:scene.rows,
        news_provider=scene.news,news_source='TEST_CALENDAR',background=False,secure=False,clock=scene.clock)
    other=TestClient(restarted);other.cookies.update(client.cookies)
    restarted.state.refresh_rules_once();scene.step(restarted)
    result=read(other,identity)
    assert result['analysis']['target']=='LONG'
    assert result['analysis']['decision_status']=='HISTORY_RESYNC_WAIT_NEW_BAR'
    assert result['pending'] is None and result['fills_count']==0
    cross(scene,False);cross(scene,True)
    restarted.state.refresh_rules_once();restarted.state.poll_once()
    assert read(other,identity)['pending']['target']=='LONG'
    scene.step(restarted)
    assert read(other,identity)['fills_count']==1


@pytest.mark.parametrize('failure_mode', ['ABORT', 'ROLLBACK'])
def test_rule_metadata_write_fault_halts_new_risk_but_preserves_a_stop(tmp_path, failure_mode):
    scene,app,client,identity=setup_rule(tmp_path)
    app.state.poll_once();scene.step(app)
    position=read(client,identity)['position']
    with sqlite3.connect(tmp_path/'paper.sqlite3') as db:
        db.execute(f"CREATE TRIGGER test_rule_metadata_fault BEFORE UPDATE ON directional_rule_state BEGIN SELECT RAISE({failure_mode},'synthetic checkpoint write failure'); END")
    revise(scene,app)
    assert read(client,identity)['entry_halted'] is True
    scene.step(app,D(position['stop_loss'])-1,D(position['stop_loss']))
    result=read(client,identity)
    assert result['position'] is None and result['fills'][-1]['reason']=='STOP_LOSS'
    assert result['entry_halted'] is True and result['fills_count']==2


def test_full_metadata_rollback_reloads_and_cancels_pending_entry_without_filling_it(tmp_path):
    scene,app,client,identity=setup_rule(tmp_path)
    app.state.poll_once();queued=read(client,identity)
    assert queued['pending'] is not None
    with sqlite3.connect(tmp_path/'paper.sqlite3') as db:
        db.execute("CREATE TRIGGER test_rule_metadata_rollback BEFORE UPDATE ON directional_rule_state BEGIN SELECT RAISE(ROLLBACK,'synthetic whole metadata rollback'); END")
    revise(scene,app)
    halted=read(client,identity)
    assert halted['entry_halted'] is True and halted['pending'] is None
    assert halted['last_outcome']['reason']=='RULE_HISTORY_STATE_UNAVAILABLE'
    revision=halted['revision']
    scene.step(app)
    result=read(client,identity)
    assert result['fills_count']==0 and result['revision']==revision


def test_full_metadata_rollback_preserves_pending_reduce_only_flat(tmp_path):
    scene,app,client,identity=setup_rule(tmp_path)
    app.state.poll_once();scene.step(app)
    view=read(client,identity)
    response=client.post(ROOT+'/accounts/'+identity+'/decision',json={
        'request_id':str(uuid4()),'expected_revision':view['revision'],'target':'FLAT',
        'quantity':'0','stop_loss':None,'take_profit':None})
    assert response.status_code==200,response.text
    with sqlite3.connect(tmp_path/'paper.sqlite3') as db:
        db.execute("CREATE TRIGGER test_rule_flat_rollback BEFORE UPDATE ON directional_rule_state BEGIN SELECT RAISE(ROLLBACK,'synthetic reduce-only metadata rollback'); END")
    revise(scene,app)
    guarded=read(client,identity)
    assert guarded['entry_halted'] is True and guarded['pending']['target']=='FLAT'
    scene.step(app)
    closed=read(client,identity)
    assert closed['position'] is None and closed['fills'][-1]['reason']=='SIGNAL_EXIT'
    assert closed['fills_count']==2


def test_distinct_guard_events_at_same_clock_do_not_reuse_an_operation_key(tmp_path):
    scene,app,client,identity=setup_rule(tmp_path)
    app.state.poll_once();revise(scene,app)
    first=read(client,identity)
    assert first['pending'] is None and first['entry_halted'] is False
    with sqlite3.connect(tmp_path/'paper.sqlite3') as db:
        db.execute("CREATE TRIGGER test_same_clock_metadata_abort BEFORE UPDATE ON directional_rule_state BEGIN SELECT RAISE(ABORT,'synthetic same-clock metadata fault'); END")
    rows=list(scene.rows)
    rows[11]=rows[11].model_copy(update={'close':rows[11].close+D('.01')})
    scene.rows=rows  # A distinct history guard event at the same observation time.
    app.state.refresh_rules_once()
    halted=read(client,identity)
    assert halted['entry_halted'] is True and halted['revision']==first['revision']+1
    app.state.refresh_rules_once()
    assert read(client,identity)['revision']==halted['revision']
