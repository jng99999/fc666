"""Isolated synthetic execution acceptance; never a production calendar or fill."""
from datetime import timedelta
from decimal import Decimal as D
import sqlite3
import json
from uuid import uuid4

from fastapi.testclient import TestClient
import pytest

from apps.api.directional_service import create_app
from core.paper.directional_market import source_for
from core.paper.price_action_rules import RuleBar
from core.paper.price_action_rules import analyze
from core.backtest.spot import digest
from core.paper.directional import Proposal
from core.paper.directional_store import Conflict
from tests.test_directional_service import Scenario, BASE, ROOT, body
from tests.test_price_action_rules import trend_fixture, range_fixture, fvg_fixture, mirror


def history(rule='TREND_PULLBACK', direction='LONG', instrument='BTCUSDT'):
    raw = {'TREND_PULLBACK': trend_fixture, 'RANGE_REVERSION': range_fixture, 'STRUCTURE_FVG': fvg_fixture}[rule]()
    if direction == 'SHORT':
        raw = mirror(raw)
    step = timedelta(minutes=1) if instrument == 'XAU/USD' else timedelta(hours=1)
    return [RuleBar(**{**b.model_dump(), 'instrument': instrument,
        'source': source_for(instrument) if instrument == 'XAU/USD' else 'bitget.public',
        'timeframe': '1m' if instrument == 'XAU/USD' else '1h',
        'open_time': BASE - step * (len(raw) - i), 'close_time': BASE - step * (len(raw) - i - 1)})
        for i, b in enumerate(raw)]


def setup_rule(tmp_path, rule='TREND_PULLBACK', direction='LONG', instrument='BTCUSDT', news=True):
    scene = Scenario()
    rows = history(rule, direction, instrument)
    scene.bid, scene.ask = str(rows[-1].close - D('.01')), str(rows[-1].close + D('.01'))
    scene.rows = rows
    def bars(name, timeframe, at):
        if scene.rows is None:
            raise RuntimeError('Explicit history outage fixture')
        return scene.rows
    app = create_app(tmp_path/'paper.sqlite3', provider=scene.quote, bar_provider=bars,
        news_provider=scene.news if news else None, news_source='TEST_CALENDAR' if news else None,
        background=False, secure=False, clock=scene.clock)
    client = TestClient(app)
    request = body('PRICE_ACTION', instrument=instrument, rule=rule,
                   timeframe='1m' if instrument == 'XAU/USD' else '1h',
                   quantity='1', stop_loss_percent='.05', take_profit_percent='.1')
    response = client.post(ROOT+'/accounts', json=request)
    assert response.status_code == 201, response.text
    identity = response.json()['account_id']
    app.state.refresh_rules_once()
    return scene, app, client, identity


def read(client, identity):
    response = client.get(ROOT+'/accounts/'+identity)
    assert response.status_code == 200, response.text
    return response.json()


@pytest.mark.parametrize('rule', ['STRUCTURE_FVG', 'RANGE_REVERSION', 'TREND_PULLBACK'])
@pytest.mark.parametrize('direction', ['LONG', 'SHORT'])
def test_actual_worker_rule_signal_queues_then_fills_only_on_a_later_quote(tmp_path, rule, direction):
    scene, app, client, identity = setup_rule(tmp_path, rule, direction)
    app.state.poll_once()
    first = read(client, identity)
    assert first['analysis']['target'] == direction
    assert first['pending']['target'] == direction
    assert first['fills_count'] == 0
    assert first['analysis']['decision_status'] == 'QUEUED'
    app.state.poll_once()  # same quote, same closed bar, no fill or repeated intent
    assert read(client, identity)['fills_count'] == 0
    scene.step(app)
    filled = read(client, identity)
    assert filled['position']['direction'] == direction
    assert filled['fills_count'] == 1 and filled['pending'] is None
    assert filled['analysis']['decision_status'] == 'FILLED'
    for key in ('stop_loss', 'take_profit'):
        assert D(filled['position'][key]) % D('.01') == 0
    assert filled['trading_enabled'] is False


@pytest.mark.parametrize('direction', ['LONG', 'SHORT'])
@pytest.mark.parametrize('exit_kind', ['STOP_LOSS', 'TAKE_PROFIT'])
def test_high_impact_blocks_entries_but_never_blocks_protective_exits(tmp_path, direction, exit_kind):
    scene, app, client, identity = setup_rule(tmp_path, direction=direction)
    app.state.poll_once(); scene.step(app)
    position = read(client, identity)['position']
    scene.news_state = 'HIGH'
    trigger = D(position['stop_loss' if exit_kind == 'STOP_LOSS' else 'take_profit'])
    if direction == 'LONG':
        scene.step(app, trigger, trigger + D('.02'))
    else:
        scene.step(app, trigger - D('.02'), trigger)
    result = read(client, identity)
    assert result['position'] is None and result['pending'] is None
    assert result['fills'][-1]['reason'] == exit_kind
    assert result['fills_count'] == 2


@pytest.mark.parametrize('news', ['HIGH', 'NONE', 'ERROR'])
def test_a_blocked_bar_is_consumed_and_cannot_open_when_news_later_clears(tmp_path, news):
    scene, app, client, identity = setup_rule(tmp_path)
    scene.news_state = news; app.state.poll_once()
    blocked = read(client, identity)
    assert blocked['pending'] is None and blocked['fills_count'] == 0
    assert blocked['analysis']['decision_status'] in ('HIGH_IMPACT_EVENT', 'NEWS_UNAVAILABLE')
    scene.news_state = 'CLEAR'; scene.step(app); scene.step(app)
    assert read(client, identity)['fills_count'] == 0


def test_pending_cancelled_during_quote_failure_does_not_resurrect_after_embargo(tmp_path):
    scene, app, client, identity = setup_rule(tmp_path)
    app.state.poll_once(); assert read(client, identity)['pending']
    original = scene.quote
    scene.news_state = 'HIGH'; scene.now += timedelta(seconds=1)
    scene.quote = lambda name: (_ for _ in ()).throw(RuntimeError('test public outage'))
    # create_app captured the bound provider; replace its mutable behavior instead.
    scene.bid = 'not-a-price'
    app.state.poll_once()
    assert read(client, identity)['pending'] is None
    scene.quote = original; scene.bid = str(scene.rows[-1].close - D('.01'))
    scene.news_state = 'CLEAR'; scene.step(app)
    assert read(client, identity)['fills_count'] == 0


def test_missing_calendar_is_visible_and_never_replaced_with_empty_safe_events(tmp_path):
    _, app, client, identity = setup_rule(tmp_path, news=False)
    app.state.poll_once(); view = read(client, identity)
    assert view['entry_gate'] == 'NEWS_UNAVAILABLE'
    assert view['news']['configured'] is False and view['fills_count'] == 0


def test_old_closed_bar_does_not_become_an_activation_time_fill(tmp_path):
    scene, app, client, identity = setup_rule(tmp_path)
    scene.now += timedelta(seconds=121); app.state.poll_once()
    view = read(client, identity)
    assert view['analysis']['decision_status'] == 'CLOSED_BAR_STALE'
    assert view['pending'] is None and view['fills_count'] == 0


def test_restart_preserves_cursor_and_never_reopens_on_the_protective_exit_bar(tmp_path):
    scene, app, client, identity = setup_rule(tmp_path)
    app.state.poll_once(); scene.step(app)
    p = read(client, identity)['position']
    scene.step(app, D(p['take_profit']), D(p['take_profit']) + D('.02'))
    assert read(client, identity)['fills_count'] == 2
    restarted = create_app(tmp_path/'paper.sqlite3', provider=scene.quote, bar_provider=lambda *args: scene.rows,
        news_provider=scene.news, news_source='TEST_CALENDAR', background=False, secure=False, clock=scene.clock)
    other = TestClient(restarted); other.cookies.update(client.cookies)
    restarted.state.refresh_rules_once(); scene.step(restarted)
    result = read(other, identity)
    assert result['fills_count'] == 2 and result['position'] is None and result['pending'] is None


def test_opposite_rule_signal_exits_first_and_needs_a_later_bar_for_reentry(tmp_path):
    scene, app, client, identity = setup_rule(tmp_path)
    app.state.poll_once(); scene.step(app)
    rows = list(scene.rows)
    anchor = rows[-1].close
    for i in range(1, 78):
        price = anchor - D(i) * D('.04') + [D(-1), D(0), D(1), D(0)][i % 4]
        opened = rows[-1].close_time
        rows.append(RuleBar(instrument='BTCUSDT', source='bitget.public', timeframe='1h',
            open_time=opened, close_time=opened+timedelta(hours=1), open=price, close=price,
            high=price+D('.5'), low=price-D('.5'), volume=None, is_closed=True))
    def crossing(down):
        result = analyze(rows, instrument='BTCUSDT', source='bitget.public', timeframe='1h',
                         now=rows[-1].close_time, rule='TREND_PULLBACK')
        mean = D(result['evidence']['ema20']).quantize(D('.000001'))
        opened = rows[-1].close_time
        rows.append(RuleBar(instrument='BTCUSDT', source='bitget.public', timeframe='1h',
            open_time=opened, close_time=opened+timedelta(hours=1),
            open=mean+(D('.2') if down else D('-.2')),
            close=mean+(D('-.6') if down else D('.2')),
            high=mean+D('.5'), low=mean-D('.8'), volume=None, is_closed=True))
        scene.rows=list(rows);scene.now=rows[-1].close_time
        scene.bid,scene.ask=str(rows[-1].close-D('.01')),str(rows[-1].close+D('.01'))
        app.state.refresh_rules_once();app.state.poll_once()
    crossing(True)
    reverse=read(client,identity)
    assert reverse['analysis']['target']=='SHORT'
    assert reverse['position']['direction']=='LONG' and reverse['pending']['target']=='FLAT'
    scene.step(app)
    flat=read(client,identity)
    assert flat['position'] is None and flat['fills'][-1]['reason']=='SIGNAL_EXIT'
    assert flat['fills_count']==2
    crossing(False);assert read(client,identity)['pending'] is None
    crossing(True);assert read(client,identity)['pending']['target']=='SHORT'
    scene.step(app)
    assert read(client,identity)['position']['direction']=='SHORT'
    assert read(client,identity)['fills_count']==3


def test_failed_checkpoint_write_rolls_back_the_queued_order_in_the_same_transaction(tmp_path):
    scene, app, client, identity = setup_rule(tmp_path)
    with sqlite3.connect(tmp_path/'paper.sqlite3') as db:
        db.execute("CREATE TRIGGER test_fail_checkpoint BEFORE INSERT ON directional_rule_state BEGIN SELECT RAISE(ABORT,'test checkpoint failure'); END")
    app.state.poll_once()
    before=read(client,identity)
    assert before['pending'] is None and before['fills_count']==0
    with sqlite3.connect(tmp_path/'paper.sqlite3') as db:
        db.execute('DROP TRIGGER test_fail_checkpoint')
    scene.step(app)
    assert read(client,identity)['pending'] is not None


def test_browser_stop_wins_over_an_older_worker_rule_decision(tmp_path):
    scene, app, client, identity = setup_rule(tmp_path)
    app.state.poll_once()
    previous = read(client, identity)
    response = client.post(ROOT+'/accounts/'+identity+'/stop', json={
        'request_id': str(uuid4()), 'expected_revision': previous['revision']})
    assert response.status_code == 200, response.text
    with pytest.raises(Conflict):
        app.state.store.rule_decision(identity, previous['analysis'], bar_sha256='a'*64,
            expected_revision=previous['revision'], now=scene.now, news=scene.news('BTCUSDT', scene.now),
            proposal=Proposal(request_id='stale-worker', target='LONG', available_at=scene.now,
                              quantity='1', stop_loss='90', take_profit='120'))
    scene.step(app)
    result = read(client, identity)
    assert result['status'] == 'STOPPED' and result['pending'] is None and result['fills_count'] == 0


@pytest.mark.parametrize('bad', [None, [], {'analysis': []}, {'analysis': {}}])
def test_indicator_checkpoint_corruption_blocks_new_signals_but_not_an_existing_stop(tmp_path, bad):
    scene, app, client, identity = setup_rule(tmp_path)
    app.state.poll_once(); scene.step(app)
    p = read(client, identity)['position']
    with sqlite3.connect(tmp_path/'paper.sqlite3') as db:
        if bad is None:
            db.execute('UPDATE directional_rule_state SET sha256=? WHERE account_id=?', ('corrupt-test', identity))
        else:
            db.execute('UPDATE directional_rule_state SET payload=?,sha256=? WHERE account_id=?', (json.dumps(bad), digest(bad), identity))
    scene.step(app, D(p['stop_loss']) - 1, D(p['stop_loss']))
    result = read(client, identity)
    assert result['position'] is None and result['fills'][-1]['reason'] == 'STOP_LOSS'
    assert result['analysis']['reason'] == 'RULE_CHECKPOINT_INVALID'


def test_gold_same_rule_uses_its_own_minute_quote_history_and_no_volume(tmp_path):
    _, app, client, identity = setup_rule(tmp_path, instrument='XAU/USD')
    app.state.poll_once(); result = read(client, identity)
    assert result['pending']['target'] == 'LONG'
    assert result['analysis']['source'] == source_for('XAU/USD')


@pytest.mark.parametrize('extra', [{'timeframe': '4h', 'instrument': 'XAU/USD'}, {'rule': None},
    {'quantity': None}, {'bars': []}, {'news': {}}, {'quote': {}}])
def test_client_cannot_supply_market_or_news_or_incomplete_rule_configuration(tmp_path, extra):
    _, _, client, _ = setup_rule(tmp_path)
    data = body('PRICE_ACTION', rule='TREND_PULLBACK', timeframe='1h', quantity='1',
                stop_loss_percent='.05', take_profit_percent='.1')
    response = client.post(ROOT+'/accounts', json={**data, **extra})
    assert response.status_code == 422
