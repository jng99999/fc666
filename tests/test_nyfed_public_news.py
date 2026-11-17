"""Synthetic profile/permission boundaries; real source probes live in outputs."""
from datetime import datetime, timedelta

import httpx
import pytest
from fastapi.testclient import TestClient

from apps.api.directional_service import create_app
from core.paper import public_news as module
from core.paper.directional import Event, entry_gate
from tests.test_public_news import NOW, SyntheticHTTP, risk_config
READ_AT = NOW + timedelta(seconds=1)


@pytest.fixture
def profile(tmp_path, monkeypatch):
    from core.paper import nyfed_calendar
    monkeypatch.delenv('DIRECTIONAL_BLS_RELAY_URL', raising=False)
    monkeypatch.setattr(module, 'VENDOR_INTERVAL', 0)
    upstream = SyntheticHTTP()
    upstream.contents[module.NYFED_URL] = '<p>Explicit synthetic parser input, not real history</p>'

    def parse(text, now, checked):
        assert text == upstream.contents[module.NYFED_URL]
        event = Event(event_id='nyfed:synthetic', impact='HIGH', instruments=('*',),
                      known_at=checked, scheduled_at=NOW+timedelta(minutes=10))
        return [event], {event.event_id: {'title': 'Synthetic calendar release', 'url': module.NYFED_URL}}, [], (
            datetime(2026, 11, 1, 4, tzinfo=NOW.tzinfo), datetime(2026, 12, 1, 5, tzinfo=NOW.tzinfo))

    monkeypatch.setattr(nyfed_calendar, 'parse_nyfed', parse)
    opened = []

    def create(calendar='nyfed'):
        value = module.PublicNews(tmp_path/'synthetic-profiles.sqlite3', calendar=calendar,
                                  fetcher=httpx.MockTransport(upstream))
        opened.append(value)
        return value

    yield upstream, create
    for value in opened:
        value.close()


def test_nyfed_profile_is_explicit_independent_source_with_real_permission_gates(profile):
    upstream, create = profile
    collector = create(); collector.refresh(NOW)
    status = collector.status(READ_AT)
    assert status['state'] == 'READY', status
    assert collector.source == status['source'] == module.NYFED_SOURCE
    assert collector.poll_interval == 30
    assert '不是BLS全部目录' in status['scope']
    assert len(status['feeds']) == 8
    assert all('bls.gov' not in str(request.url) for request in upstream.calls)
    config = risk_config(news_sources=(module.NYFED_SOURCE,))
    assert entry_gate(config, READ_AT, collector.read('BTCUSDT', READ_AT)) == 'HIGH_IMPACT_EVENT'
    old = risk_config(news_sources=(module.SOURCE,))
    assert entry_gate(old, READ_AT, collector.read('BTCUSDT', READ_AT)) == 'NEWS_SOURCE_UNTRUSTED'
    assert collector.read('BTCUSDT', NOW+timedelta(seconds=121)) is None


def test_nyfed_failure_does_not_fallback_to_bls_or_keep_previous_permission(profile):
    upstream, create = profile
    collector = create(); collector.refresh(NOW)
    assert collector.read('XAU/USD', READ_AT) is not None
    upstream.statuses[module.NYFED_URL] = 503
    collector.refresh(NOW)
    assert collector.read('BTCUSDT', READ_AT) is None
    status = collector.status(READ_AT)
    assert status['state'] == 'UNAVAILABLE'
    assert any(row['name'] == 'fed-calendar' and row['events'] for row in status['feeds'])
    assert all('bls.gov' not in str(request.url) for request in upstream.calls)


def test_calendar_profile_change_cannot_reuse_a_stored_clear_grant(profile):
    _, create = profile
    old = create('bls'); old.refresh(NOW)
    assert old.read('BTCUSDT', READ_AT) is not None, old.status(READ_AT)
    old.close()
    new = create()
    assert new.read('BTCUSDT', READ_AT) is None
    assert new.status(READ_AT)['source'] == module.NYFED_SOURCE
    new.refresh(NOW)
    assert new.read('BTCUSDT', READ_AT).source == module.NYFED_SOURCE
    new.close()
    restarted = create()
    assert restarted.status(READ_AT)['state'] == 'WARMUP'
    assert restarted.read('BTCUSDT', READ_AT) is None


def test_runtime_profile_is_explicit_and_unsupported_selection_refuses_start(tmp_path, monkeypatch):
    monkeypatch.setenv('DIRECTIONAL_OFFICIAL_CALENDAR', 'nyfed')
    monkeypatch.delenv('DIRECTIONAL_BLS_RELAY_URL', raising=False)
    real, opened = module.PublicNews, []
    def create(*args, **kwargs):
        value = real(*args, **kwargs)
        opened.append(value)
        return value
    monkeypatch.setattr(module, 'PublicNews', create)
    create_app(tmp_path/'paper.sqlite3', public_news_url='https://paper.example/news', background=False)
    collector, = opened
    assert collector.source == module.NYFED_SOURCE
    collector.close()
    with pytest.raises(ValueError, match='supported official calendar'):
        real(tmp_path/'invalid.sqlite3', calendar='guessed-mirror')


def test_public_snapshot_keeps_source_attribution_and_license_without_changing_news_schema(profile, tmp_path):
    _, create = profile
    collector = create(); collector.refresh(NOW)
    app = create_app(tmp_path/'api-paper.sqlite3', news_provider=collector.read,
        news_source=collector.source, public_news_provider=collector, background=False,
        secure=False, clock=lambda: READ_AT)
    client = TestClient(app)
    for path in ('/news', '/news/status', '/status'):
        response = client.get('/api/v1/directional'+path)
        assert response.status_code == 200
        assert 'rel="license"' in response.headers['Link']
        assert 'Federal Reserve Bank of New York' in response.headers['X-Data-Attribution']
        assert '(c) 2026' in response.headers['X-Data-Attribution']
        assert 'no New York Fed endorsement' in response.headers['X-Data-Modification']
