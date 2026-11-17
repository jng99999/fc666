"""HTTP/background tests use explicit synthetic quotes/news, never live orders."""
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from threading import Event
import time
from uuid import uuid4

from fastapi.testclient import TestClient
import pytest

from apps.api.directional_service import COOKIE, create_app
from core.paper.directional import Event as NewsEvent, News, Quote
from core.paper.directional_market import source_for

BASE = datetime(2026, 10, 27, 8, tzinfo=timezone.utc)
ROOT = '/api/v1/directional'


class Scenario:
    def __init__(self):
        self.now = BASE
        self.bid, self.ask = '100', '101'
        self.news_state = 'CLEAR'
        self.calls = 0

    def clock(self):
        return self.now

    def quote(self, instrument):
        self.calls += 1
        return Quote(instrument=instrument, source=source_for(instrument), at=self.now, bid=self.bid, ask=self.ask)

    def news(self, instrument, now):
        if self.news_state == 'ERROR':
            raise RuntimeError('Explicit news outage test fixture')
        if self.news_state == 'NONE':
            return None
        events = () if self.news_state == 'CLEAR' else (NewsEvent(event_id='test-event', instruments=(instrument,),
                  impact='HIGH', scheduled_at=now, known_at=now - timedelta(hours=1)),)
        return News(source='TEST_CALENDAR', observed_at=now, coverage_start=now-timedelta(days=1),
                    coverage_end=now+timedelta(days=1), events=events)

    def step(self, app, bid=None, ask=None):
        self.now += timedelta(seconds=1)
        if bid is not None:
            self.bid = str(bid)
        if ask is not None:
            self.ask = str(ask)
        app.state.poll_once()


def body(strategy='MANUAL', **overrides):
    data = {'request_id': str(uuid4()), 'instrument': 'BTCUSDT', 'strategy': strategy,
            'initial_cash': '10000', 'tick_size': '.01', 'quantity_step': '.01', 'max_quantity': '10',
            'max_order_quote': '1000', 'fee_rate': '.001', 'slippage': '.05',
            'news_pre_seconds': 30, 'news_post_seconds': 60,
            'quantity': None, 'stop_loss_percent': None, 'take_profit_percent': None}
    if strategy == 'SMA20_QUOTES':
        data.update(quantity='1', stop_loss_percent='.1', take_profit_percent='.2')
    return {**data, **overrides}


def setup(tmp_path, *, news=True, **kwargs):
    scene = Scenario()
    app = create_app(tmp_path/'directional.sqlite3', provider=scene.quote, background=False, secure=False,
                     clock=scene.clock, news_provider=scene.news if news else None,
                     news_source='TEST_CALENDAR' if news else None, **kwargs)
    return scene, app, TestClient(app)


def create(client, **kwargs):
    response = client.post(ROOT+'/accounts', json=body(**kwargs))
    assert response.status_code == 201, response.text
    return response.json()


def read(client, identity):
    response = client.get(ROOT+'/accounts/'+identity)
    assert response.status_code == 200, response.text
    return response.json()


def decide(client, account, target='LONG', **updates):
    payload = {'request_id': str(uuid4()), 'expected_revision': account['revision'], 'target': target,
               'quantity': '0' if target == 'FLAT' else '1',
               'stop_loss': None if target == 'FLAT' else '90' if target == 'LONG' else '110',
               'take_profit': None if target == 'FLAT' else '110' if target == 'LONG' else '90'}
    payload.update(updates)
    return client.post(ROOT+'/accounts/'+account['account_id']+'/decision', json=payload), payload


@pytest.mark.parametrize('target', ['LONG', 'SHORT'])
@pytest.mark.parametrize('exit_kind', ['STOP_LOSS', 'TAKE_PROFIT', 'SIGNAL_EXIT'])
def test_real_http_flow_two_sides_protection_and_news_outage(tmp_path, target, exit_kind):
    scene, app, client = setup(tmp_path)
    with client:
        account = create(client)
        identity = account['account_id']
        app.state.poll_once()
        response, _ = decide(client, read(client, identity), target)
        assert response.status_code == 200, response.text
        assert response.json()['decision_result']['status'] == 'ACCEPTED'
        assert response.json()['fills'] == []
        scene.step(app)
        account = read(client, identity)
        assert account['position']['direction'] == target and len(account['fills']) == 1
        scene.news_state = 'ERROR'
        if exit_kind == 'SIGNAL_EXIT':
            response, _ = decide(client, account, 'FLAT')
            assert response.status_code == 200 and response.json()['decision_result']['status'] == 'ACCEPTED'
            scene.step(app)
        elif (target == 'LONG') == (exit_kind == 'TAKE_PROFIT'):
            scene.step(app, '111', '112')
        else:
            scene.step(app, '88', '89')
        after = read(client, identity)
        assert after['position'] is None and after['fills'][-1]['reason'] == exit_kind
        assert after['entry_gate'] == 'NEWS_UNAVAILABLE'
        fees = sum(Decimal(fill['fee']) for fill in after['fills'])
        assert Decimal(after['balance']) == Decimal('10000') + Decimal(after['gross_pnl']) - fees
        assert after['trading_enabled'] is False


def test_news_unavailable_blocks_http_entry_and_client_cannot_override(tmp_path):
    scene, app, client = setup(tmp_path, news=False)
    with client:
        account = create(client)
        app.state.poll_once()
        current = read(client, account['account_id'])
        response, payload = decide(client, current)
        assert response.status_code == 200 and response.json()['decision_result'] == {'status': 'REJECTED', 'reason': 'NEWS_UNAVAILABLE'}
        for field in ('news', 'quote', 'available_at', 'quote_source'):
            result = client.post(ROOT+'/accounts/'+account['account_id']+'/decision', json={**payload, field: {}})
            assert result.status_code == 422
        scene.step(app)
        assert read(client, account['account_id'])['fills'] == []
        status = client.get(ROOT+'/status').json()
        assert status['ai']['configured'] is False and status['news']['configured'] is False


def test_news_rechecked_before_fill_and_no_cached_clear_calendar(tmp_path):
    scene, app, client = setup(tmp_path)
    with client:
        account = create(client)
        app.state.poll_once()
        response, _ = decide(client, read(client, account['account_id']))
        assert response.json()['decision_result']['status'] == 'ACCEPTED'
        scene.news_state = 'HIGH'
        scene.step(app)
        account = read(client, account['account_id'])
        assert account['entry_gate'] == 'HIGH_IMPACT_EVENT' and account['fills'] == [] and account['pending'] is None
        scene.news_state = 'ERROR'
        assert read(client, account['account_id'])['entry_gate'] == 'NEWS_UNAVAILABLE'


def test_http_receipt_retry_preserves_server_decision_clock_and_detects_changed_inputs(tmp_path):
    scene, app, client = setup(tmp_path)
    with client:
        created = body()
        account = client.post(ROOT+'/accounts', json=created).json()
        assert client.post(ROOT+'/accounts', json=created).json()['account_id'] == account['account_id']
        app.state.poll_once()
        first, payload = decide(client, read(client, account['account_id']))
        assert first.status_code == 200
        scene.step(app)
        second = client.post(ROOT+'/accounts/'+account['account_id']+'/decision', json=payload)
        assert second.status_code == 200 and len(second.json()['fills']) == 1
        assert second.json()['decision_result'] == first.json()['decision_result']
        changed = client.post(ROOT+'/accounts/'+account['account_id']+'/decision', json={**payload, 'quantity': '2'})
        assert changed.status_code == 409


def test_stop_keeps_bracket_background_active_and_stale_tab_cannot_stop(tmp_path):
    scene, app, client = setup(tmp_path)
    with client:
        account = create(client)
        identity = account['account_id']
        app.state.poll_once()
        current = read(client, identity)
        decide(client, current)
        scene.step(app)
        stale = client.post(ROOT+'/accounts/'+identity+'/stop', json={'request_id': str(uuid4()), 'expected_revision': current['revision']})
        assert stale.status_code == 409
        account = read(client, identity)
        command = {'request_id': str(uuid4()), 'expected_revision': account['revision']}
        response = client.post(ROOT+'/accounts/'+identity+'/stop', json=command)
        assert response.status_code == 200 and response.json()['status'] == 'STOPPED'
        assert response.json()['position'] is not None and response.json()['pending'] is None
        assert client.post(ROOT+'/accounts/'+identity+'/stop', json=command).status_code == 200
        assert decide(client, read(client, identity))[0].status_code == 409
        scene.news_state = 'NONE'
        scene.step(app, '88', '89')
        after = read(client, identity)
        assert after['position'] is None and after['fills'][-1]['reason'] == 'STOP_LOSS'
        assert app.state.store.active() == []


def test_browser_owner_isolation_csrf_size_json_and_schema_validation(tmp_path):
    scene, app, client = setup(tmp_path)
    with client:
        account = create(client)
        assert COOKIE in client.cookies
        assert client.get(ROOT+'/accounts').headers['cache-control'] == 'no-store'
        other = TestClient(app)
        assert other.get(ROOT+'/accounts/'+account['account_id']).status_code == 401
        assert other.get(ROOT+'/accounts').status_code == 200
        assert other.get(ROOT+'/accounts/'+account['account_id']).status_code == 404
        assert other.get(ROOT+'/accounts').json()['accounts'] == []
        assert client.post(ROOT+'/accounts', json=body(), headers={'origin': 'https://evil.example'}).status_code == 403
        assert client.post(ROOT+'/accounts', json=body(), headers={'sec-fetch-site': 'cross-site'}).status_code == 403
        assert client.post(ROOT+'/accounts', content='{}').status_code == 415
        assert client.post(ROOT+'/accounts', content='x'*8193, headers={'content-type': 'application/json'}).status_code == 413
        for changes in ({'fee_rate': .001}, {'instrument': 'XAUUSDT'}, {'strategy': 'AI'}, {'news': {}},
                        {'max_order_quote': '10001'}, {'instrument': 'XAU/USD', 'max_quantity': '101'}):
            assert client.post(ROOT+'/accounts', json=body(**changes)).status_code == 422
        assert client.post(ROOT+'/accounts', json=body(strategy='SMA20_QUOTES', take_profit_percent='1')).status_code == 422


def test_automatic_20_distinct_quotes_next_quote_fill_and_reduce_only_reversal(tmp_path):
    scene, app, client = setup(tmp_path)
    with client:
        account = create(client, strategy='SMA20_QUOTES')
        identity = account['account_id']
        for index in range(19):
            scene.step(app, 100+index, 101+index)
        row = read(client, identity)
        assert row['analysis']['state'] == 'WARMUP' and row['pending'] is None
        app.state.poll_once()  # Same provider timestamp is not another sample.
        assert read(client, identity)['analysis']['samples'] == 19
        scene.step(app, 119, 120)
        row = read(client, identity)
        assert row['analysis']['target'] == 'LONG' and row['pending']['target'] == 'LONG' and row['fills'] == []
        scene.step(app, 120, 121)
        row = read(client, identity)
        assert row['position']['direction'] == 'LONG' and len(row['fills']) == 1
        # A reversal inside the protective bracket first schedules FLAT.
        scene.step(app, 110, 111)
        row = read(client, identity)
        assert row['analysis']['target'] == 'SHORT' and row['pending']['target'] == 'FLAT'
        assert row['position']['direction'] == 'LONG'
        scene.step(app, 110, 111)
        row = read(client, identity)
        assert row['position'] is None and row['fills'][-1]['reason'] == 'SIGNAL_EXIT'
        assert row['pending']['target'] == 'SHORT'
        scene.step(app, 109, 110)
        row = read(client, identity)
        assert row['position']['direction'] == 'SHORT' and len(row['fills']) == 3


def test_auto_news_unknown_warms_up_without_false_entry_and_manual_override_denied(tmp_path):
    scene, app, client = setup(tmp_path, news=False)
    with client:
        account = create(client, strategy='SMA20_QUOTES')
        for index in range(22):
            scene.step(app, 100+index, 101+index)
        row = read(client, account['account_id'])
        assert row['analysis']['state'] == 'READY' and row['entry_gate'] == 'NEWS_UNAVAILABLE'
        assert row['pending'] is None and row['fills'] == []
        assert decide(client, row)[0].status_code == 409


def test_automatic_budget_limit_visible_without_repeated_impossible_pending_orders(tmp_path):
    scene, app, client = setup(tmp_path)
    with client:
        account = create(client, strategy='SMA20_QUOTES', max_order_quote='50')
        for index in range(22):
            scene.step(app, 100+index, 101+index)
        row = read(client, account['account_id'])
        assert row['analysis']['state'] == 'READY' and row['analysis']['target'] == 'LONG'
        assert row['entry_gate'] == 'COLLATERAL_OR_ORDER_LIMIT' and row['pending'] is None and row['fills'] == []
        assert row['last_outcome'] is None


def test_actual_fill_rejection_exposed_after_quote_gap_and_new_news_snapshot(tmp_path):
    scene, app, client = setup(tmp_path)
    with client:
        account = create(client)
        app.state.poll_once()
        result, _ = decide(client, read(client, account['account_id']))
        assert result.json()['last_outcome']['status'] == 'ACCEPTED'
        scene.news_state = 'HIGH'
        scene.step(app)
        row = read(client, account['account_id'])
        assert row['last_outcome']['kind'] == 'ENTRY_REJECTED'
        assert row['last_outcome']['reason'] == 'HIGH_IMPACT_EVENT' and row['last_outcome']['status'] == 'REJECTED'


def test_automatic_restart_recovers_observation_window_and_pending_without_browser(tmp_path):
    scene, app, client = setup(tmp_path)
    with client:
        account = create(client, strategy='SMA20_QUOTES')
        identity = account['account_id']
        cookies = dict(client.cookies)
        for index in range(20):
            scene.step(app, 100+index, 101+index)
        assert read(client, identity)['pending'] is not None
    restored = create_app(tmp_path/'directional.sqlite3', provider=scene.quote, news_provider=scene.news,
                          news_source='TEST_CALENDAR', clock=scene.clock, background=False, secure=False)
    # No GET, page or command is used to execute the queued decision.
    scene.step(restored, 120, 121)
    with TestClient(restored) as reader:
        reader.cookies.update(cookies)
        row = read(reader, identity)
        assert row['position']['direction'] == 'LONG' and len(row['fills']) == 1


def test_actual_lifespan_thread_runs_with_no_webpage_and_stops_cleanly(tmp_path):
    seen = Event()
    scene = Scenario()
    def provider(instrument):
        seen.set()
        return scene.quote(instrument)
    app = create_app(tmp_path/'directional.sqlite3', provider=provider, news_provider=scene.news,
                     news_source='TEST_CALENDAR', clock=scene.clock, secure=False, interval=.01)
    with TestClient(app) as client:
        create(client)
        assert seen.wait(timeout=3)
        deadline = time.monotonic() + 3
        while client.get(ROOT+'/status').json()['feed']['BTCUSDT']['state'] != 'CONNECTED' and time.monotonic() < deadline:
            time.sleep(.01)
        assert client.get(ROOT+'/status').json()['feed']['BTCUSDT']['state'] == 'CONNECTED'


def test_entry_gate_reports_missing_quote_staleness_and_stop_before_news(tmp_path):
    scene, app, client = setup(tmp_path)
    with client:
        account = create(client)
        identity = account['account_id']
        assert account['entry_gate'] == 'QUOTE_UNAVAILABLE'
        app.state.poll_once()
        assert read(client, identity)['entry_gate'] == 'CLEAR'
        scene.now += timedelta(seconds=61)
        assert read(client, identity)['entry_gate'] == 'QUOTE_STALE'
        account = read(client, identity)
        result = client.post(ROOT+'/accounts/'+identity+'/stop',
                             json={'request_id': str(uuid4()), 'expected_revision': account['revision']})
        assert result.status_code == 200 and result.json()['entry_gate'] == 'ENTRY_HALTED'


def test_quote_outage_does_not_reuse_old_feed_as_a_fresh_quote(tmp_path):
    scene = Scenario()
    unavailable = False
    def provider(instrument):
        if unavailable:
            raise RuntimeError('Explicit public quote outage test fixture')
        return scene.quote(instrument)
    app = create_app(tmp_path/'directional.sqlite3', provider=provider, news_provider=scene.news,
                     news_source='TEST_CALENDAR', clock=scene.clock, background=False, secure=False)
    with TestClient(app) as client:
        account = create(client)
        app.state.poll_once()
        before = read(client, account['account_id'])
        unavailable = True
        scene.now += timedelta(seconds=61)
        app.state.poll_once()
        after = read(client, account['account_id'])
        assert after['last_quote'] == before['last_quote'] and after['fills'] == []
        assert after['feed']['state'] == 'UNAVAILABLE' and after['entry_gate'] == 'QUOTE_STALE'


def test_live_flag_refused_and_source_contract_is_explicit(tmp_path, monkeypatch):
    monkeypatch.setenv('LIVE_TRADING', 'true')
    with pytest.raises(ValueError, match='LIVE_TRADING=false'):
        create_app(tmp_path/'directional.sqlite3', background=False)
