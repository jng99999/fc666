from datetime import datetime, timezone

import httpx
import pytest

from apps.api.directional_service import http_news


@pytest.mark.parametrize('url', ['http://calendar.test', 'https://u:p@calendar.test', 'https://calendar.test/#x', 'file:///calendar'])
def test_news_configuration_rejects_unsafe_urls(url):
    with pytest.raises(ValueError):
        http_news(url)


def test_news_adapter_preserves_source_timestamps_and_auth(monkeypatch):
    stamp = '2026-11-02T00:00:00Z'
    def respond(request):
        assert request.headers['authorization'] == 'Bearer SYNTHETIC_SERVER_TOKEN'
        assert request.url.params['instrument'] == 'XAU/USD'
        return httpx.Response(200, json=dict(source='TEST_CALENDAR', observed_at=stamp,
            coverage_start=stamp, coverage_end='2026-11-03T00:00:00Z', events=[]))
    original = httpx.Client
    monkeypatch.setattr(httpx, 'Client', lambda **kw: original(transport=httpx.MockTransport(respond), **kw))
    value = http_news('https://calendar.test', 'SYNTHETIC_SERVER_TOKEN')('XAU/USD', datetime(2026, 11, 2, 1, tzinfo=timezone.utc))
    assert value.observed_at.isoformat() == '2026-11-02T00:00:00+00:00'
    assert value.source == 'TEST_CALENDAR'


@pytest.mark.parametrize('status,body', [(302, ''), (429, ''), (200, '{}'), (200, 'x'*2000001)])
def test_news_failure_does_not_become_a_clear_calendar(monkeypatch, status, body):
    original = httpx.Client
    monkeypatch.setattr(httpx, 'Client', lambda **kw: original(
        transport=httpx.MockTransport(lambda _: httpx.Response(status, text=body)), **kw))
    with pytest.raises((ValueError, httpx.HTTPError)):
        http_news('https://calendar.test')('XAU/USD', datetime.now(timezone.utc))
