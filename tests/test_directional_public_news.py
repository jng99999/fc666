"""Native collector wiring uses explicit synthetic news and temporary ledgers."""
from datetime import timedelta

from fastapi.testclient import TestClient
import pytest

from apps.api import directional_service
from core.security.operator import OperatorStore, _empty_config
from tests.test_directional_service import ROOT, Scenario, body


class Collector:
    source = 'official-us-calendar-bitget-v1'

    def __init__(self, scene):
        self.scene, self.value, self.refreshes = scene, None, 0

    def refresh(self, now):
        self.refreshes += 1
        self.value = self.scene.news('BTCUSDT', now)
        if self.value is not None:
            self.value = self.value.model_copy(update={'source': self.source})

    def read(self, instrument, now):
        if self.value is None or not 0 <= (now-self.value.observed_at).total_seconds() <= 120:
            return None
        return self.value

    def status(self, now):
        return {'source': self.source, 'state': 'READY' if self.read('BTCUSDT', now) else 'UNAVAILABLE'}

    def close(self):
        pass


def setup(tmp_path, monkeypatch, *, mismatch=False):
    monkeypatch.setattr('core.security.operator.validate_public_url', lambda value: value)
    scene = Scenario()
    operator = OperatorStore(tmp_path/'operator', secure=False)
    config = _empty_config()
    config['news'].update(source='CUSTOM' if mismatch else Collector.source, url='https://paper.example/api/directional/news')
    operator.vault.save(0, config)
    collector = Collector(scene)
    app = directional_service.create_app(tmp_path/'paper.sqlite3', provider=scene.quote,
        operator_store=operator, public_news_provider=collector,
        public_news_url='https://paper.example/api/directional/news', clock=scene.clock,
        background=False, secure=False)
    return scene, app, TestClient(app), collector


def test_native_snapshot_requires_explicit_matching_operator_configuration_and_does_not_refetch(tmp_path, monkeypatch):
    scene, app, client, collector = setup(tmp_path, monkeypatch)
    response = client.post(ROOT+'/accounts', json=body())
    assert response.status_code == 201, response.text
    identity = response.json()['account_id']
    assert response.json()['config']['news_sources'] == [collector.source]
    assert client.get(ROOT+'/news').status_code == 503
    app.state.refresh_news_once()
    app.state.poll_once()
    assert client.get(ROOT+'/accounts/'+identity).json()['entry_gate'] == 'CLEAR'
    assert client.get(ROOT+'/news').json()['source'] == collector.source
    assert client.get(ROOT+'/news/status').json()['state'] == 'READY'
    assert client.get(ROOT+'/status').json()['news']['monitor']['source'] == collector.source
    assert collector.refreshes == 1
    scene.now += timedelta(seconds=121)
    app.state.poll_once()
    assert client.get(ROOT+'/accounts/'+identity).json()['entry_gate'] != 'CLEAR'
    assert client.get(ROOT+'/news').status_code == 503
    assert collector.refreshes == 1


def test_configured_custom_source_never_uses_native_snapshot_as_fallback(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(directional_service, 'http_news', lambda *args, **kwargs: lambda *a: calls.append(args) or None)
    scene, app, client, collector = setup(tmp_path, monkeypatch, mismatch=True)
    app.state.refresh_news_once()
    response = client.post(ROOT+'/accounts', json=body())
    assert response.status_code == 201
    app.state.poll_once()
    assert calls
    assert client.get(ROOT+'/accounts/'+response.json()['account_id']).json()['entry_gate'] == 'NEWS_UNAVAILABLE'


def test_collector_failure_and_high_event_deny_new_risk_without_rewriting_a_clear_snapshot(tmp_path, monkeypatch):
    scene, app, client, collector = setup(tmp_path, monkeypatch)
    identity = client.post(ROOT+'/accounts', json=body()).json()['account_id']
    scene.news_state = 'HIGH'
    app.state.refresh_news_once(); app.state.poll_once()
    assert client.get(ROOT+'/accounts/'+identity).json()['entry_gate'] == 'HIGH_IMPACT_EVENT'
    scene.news_state = 'NONE'
    app.state.refresh_news_once(); scene.step(app)
    assert client.get(ROOT+'/accounts/'+identity).json()['entry_gate'] == 'NEWS_UNAVAILABLE'


@pytest.mark.parametrize('url', ['http://paper.example/news', 'https://user:password@paper.example/news',
    'https://paper.example/news?token=private', 'https://paper.example/news#fragment'])
def test_native_profile_url_is_explicit_https_without_credentials_or_query(tmp_path, url):
    with pytest.raises(ValueError, match='explicit HTTPS'):
        directional_service.create_app(tmp_path/'paper.sqlite3', public_news_url=url, background=False)
