"""Real admin API -> protected synthetic model -> PAPER fills. No external calls."""
from datetime import datetime, timedelta, timezone
import json
import socket
from uuid import uuid4

from fastapi.testclient import TestClient
import httpx
import pytest

from apps.api.directional_service import create_app
from apps.tradingagents_worker import configured
from core.paper.directional import News, Quote
from core.paper.directional_market import source_for
from core.security.operator import OperatorStore
from core.security.public_http import PublicTransport
from tests.test_ai_paper_automation import make_advice


ORIGIN = 'https://fc666-test.vercel.app'


def public_dns(host, port, **kwargs):
    return [(socket.AF_INET, socket.SOCK_STREAM, 6, '', ('93.184.216.34', port))]


def configuration(**sections):
    result = dict(model=dict(provider='openai', model='ONLY_SYNTHETIC_MODEL', base_url='', api_key='ONLY_SYNTHETIC_API_KEY'),
        news=dict(source='SYNTHETIC_CALENDAR', url='https://calendar.example/events', token='ONLY_SYNTHETIC_NEWS'),
        gold=dict(source='', url='', token=''), authorized_accounts=[])
    result.update(sections)
    return result


def test_transport_pins_destination_but_preserves_tls_and_host():
    seen = []
    def respond(request):
        seen.append(request)
        return httpx.Response(200, json={'fixture_only': True})
    with httpx.Client(transport=PublicTransport(resolver=public_dns,
            transport=httpx.MockTransport(respond))) as client:
        assert client.get('https://calendar.example:8443/events', headers={'Authorization': 'Bearer ONLY_FIXTURE'}).status_code == 200
    assert seen[0].url.host == '93.184.216.34'
    assert seen[0].url.port == 8443
    assert seen[0].headers['host'] == 'calendar.example:8443'
    assert seen[0].extensions['sni_hostname'] == 'calendar.example'
    assert seen[0].headers['authorization'] == 'Bearer ONLY_FIXTURE'


@pytest.mark.parametrize('address', ['127.0.0.1', '10.0.0.1', '169.254.169.254', '100.100.100.200', '::1', 'fc00::1', '224.0.0.1'])
def test_runtime_dns_rebinding_and_special_addresses_are_blocked(address):
    called = []
    def rebound(host, port, **kwargs):
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, '', (address, port))]
    with httpx.Client(transport=PublicTransport(resolver=rebound,
            transport=httpx.MockTransport(lambda request: called.append(request)))) as client:
        with pytest.raises(ValueError):
            client.get('https://previously-public.example/secret')
    assert not called


def test_credential_redirect_is_rejected_even_if_caller_requests_following():
    with httpx.Client(transport=PublicTransport(resolver=public_dns,
            transport=httpx.MockTransport(lambda request: httpx.Response(302, headers={'Location': 'https://other.example'}))), follow_redirects=True) as client:
        with pytest.raises(ValueError):
            client.get('https://calendar.example/events')


def test_worker_reads_saved_model_configuration_and_clearing_without_restart(tmp_path, monkeypatch):
    operator = OperatorStore(tmp_path / 'operator')
    monkeypatch.setenv('FC666_OPERATOR_VAULT_PATH', str(operator.vault.directory))
    monkeypatch.setenv('FC666_TA_API_KEY', 'STALE_ENV_MUST_NOT_FALL_BACK')
    assert not configured()
    body = configuration(news=dict(source='', url='', token=''))
    operator.vault.save(0, body)
    assert configured()
    body['model'].update(api_key='', clear_api_key=True)
    operator.vault.save(1, body)
    assert not configured()
    assert b'ONLY_SYNTHETIC_API_KEY' not in operator.vault.path.read_bytes()


@pytest.mark.parametrize('news_failure', ['high_impact', 'vault_unavailable'])
def test_admin_configuration_to_model_to_fill_and_protective_exit(tmp_path, monkeypatch, news_failure):
    from core.security import operator as operator_module
    monkeypatch.setattr(operator_module.socket, 'getaddrinfo', public_dns)
    now = [datetime(2026, 11, 2, 8, tzinfo=timezone.utc)]
    price = ['100']
    blocked_news = [False]
    operator = OperatorStore(tmp_path / 'operator', secure=False)
    class Runner:
        configured = True
        calls = 0
        @property
        def accounts(self):
            return set(operator.vault.read()['authorized_accounts'])
        def availability(self):
            ready = bool(operator.vault.read()['model']['api_key'])
            return ready, 'READY' if ready else 'WAIT_MODEL_CONFIG'
        def __call__(self, snapshot):
            self.calls += 1
            return make_advice(snapshot)
    runner = Runner()
    def quote(instrument):
        return Quote(instrument=instrument, source=source_for(instrument), at=now[0], bid=price[0], ask=price[0])
    def news(instrument, at):
        return News(source='SYNTHETIC_CALENDAR', observed_at=at, coverage_start=at-timedelta(days=1),
            coverage_end=at+timedelta(days=1), events=[] if not blocked_news[0] else [dict(event_id='SYNTHETIC_HIGH',
            instruments=[instrument], impact='HIGH', scheduled_at=at, known_at=at)])
    monkeypatch.setattr('apps.api.directional_service.http_news', lambda *args, **kwargs: news)
    def history(instrument, at, **kwargs):
        from core.research.market_evidence import calculate_indicators
        end = int(at.timestamp()) // 60 * 60000
        bars = [dict(open_time=end-(100-index)*60000, close_time=end-(99-index)*60000,
            open='100', high='101', low='99', close='100', volume=None) for index in range(100)]
        return dict(status='READY', instrument=instrument, bars=bars, indicators=calculate_indicators(bars),
            quote_source=source_for(instrument), candle_source='SYNTHETIC_ONLY_NOT_REAL_HISTORY')
    monkeypatch.setattr('core.research.market_evidence.evidence', history)
    app = create_app(tmp_path / 'paper.sqlite3', provider=quote, background=False, secure=False,
        clock=lambda: now[0], operator_store=operator, research_runner=runner)
    with TestClient(app, base_url=ORIGIN) as client:
        headers = {'Origin': ORIGIN, 'Content-Type': 'application/json'}
        invitation = operator.provision_setup()
        setup = client.post('/api/v1/directional/admin/setup', headers=headers,
            json=dict(setup_token=invitation, username='fixture_admin', password='ONLY_LOCAL_TEST_PASSWORD'))
        assert setup.status_code == 200
        csrf = setup.json()['csrf_token']
        protected = {**headers, 'X-CSRF-Token': csrf}
        cfg = configuration()
        saved = client.post('/api/v1/directional/admin/configuration', headers=protected,
            json=dict(expected_revision=0, **cfg))
        assert saved.status_code == 200
        assert 'ONLY_SYNTHETIC_API_KEY' not in saved.text
        client.get('/api/v1/directional/accounts')
        created = client.post('/api/v1/directional/accounts', headers=headers, json=dict(
            request_id=str(uuid4()), instrument='XAU/USD', strategy='AI_RESEARCH', initial_cash='10000',
            tick_size='0.01', quantity_step='0.01', max_quantity='10', max_order_quote='1000', fee_rate='0.001',
            slippage='0.05', news_pre_seconds=1800, news_post_seconds=1800))
        assert created.status_code == 201
        row = created.json(); account = row['account_id']
        second = TestClient(app, base_url=ORIGIN)
        assert second.post('/api/v1/directional/admin/login', headers=headers,
            json=dict(username='fixture_admin', password='ONLY_LOCAL_TEST_PASSWORD')).status_code == 200
        assert account in {item['account_id'] for item in second.get('/api/v1/directional/accounts').json()['accounts']}
        assert 'fc666_directional_session' not in second.cookies
        cfg['authorized_accounts'] = [account]
        assert client.post('/api/v1/directional/admin/configuration', headers=protected,
            json=dict(expected_revision=1, **cfg)).status_code == 200
        app.state.poll_once()
        row = client.get('/api/v1/directional/accounts/'+account).json()
        command = dict(request_id=str(uuid4()), expected_revision=row['revision'], expected_epoch=0,
                       action='START', interval_seconds=300)
        client.post('/api/v1/directional/admin/logout', headers=protected, json={})
        assert client.post('/api/v1/directional/research/automation/'+account, headers=headers, json=command).status_code == 401
        assert runner.calls == 0
        assert client.post('/api/v1/directional/admin/login', headers=headers,
            json=dict(username='fixture_admin', password='ONLY_LOCAL_TEST_PASSWORD')).status_code == 200
        assert client.post('/api/v1/directional/research/automation/'+account, headers=headers, json=command).status_code == 200
        service = app.state.research
        service.automation.schedule(); service.run_once()
        assert runner.calls == 1
        row = client.get('/api/v1/directional/accounts/'+account).json()
        assert row['pending']['target'] == 'LONG' and row['fills_count'] == 0
        now[0] += timedelta(seconds=1)
        app.state.poll_once()
        row = client.get('/api/v1/directional/accounts/'+account).json()
        assert row['fills_count'] == 1 and row['position']['direction'] == 'LONG'
        status = client.get('/api/v1/directional/admin/status').json()
        cfg['model']['api_key'] = ''
        result = client.post('/api/v1/directional/admin/configuration', headers={**headers,'X-CSRF-Token':status['csrf_token']},
            json=dict(expected_revision=2, **cfg))
        assert result.status_code == 200
        control = client.get('/api/v1/directional/research/automation/'+account).json()
        assert not control['enabled'] and control['state'] == 'PAUSED'
        if news_failure == 'vault_unavailable':
            from core.security.operator import OperatorError
            def unreadable():
                raise OperatorError(503, 'SYNTHETIC_VAULT_UNAVAILABLE')
            monkeypatch.setattr(operator.vault, 'read', unreadable)
        blocked_news[0] = True; price[0] = '111'; now[0] += timedelta(seconds=1)
        app.state.poll_once()
        row = client.get('/api/v1/directional/accounts/'+account).json()
        assert row['position'] is None and row['fills_count'] == 2
        assert row['fills'][-1]['reason'] == 'TAKE_PROFIT'
        assert row['trading_enabled'] is False
