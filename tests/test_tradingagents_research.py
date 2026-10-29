"""Synthetic model/news tests; never use credentials, network or real accounts."""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from uuid import uuid4

from fastapi.testclient import TestClient
from pydantic import ValidationError
import pytest
import sqlite3

from apps.api.directional_service import create_app
from core.paper.directional import Config, Event, News, Quote
from core.paper.directional_market import source_for
from core.paper.directional_store import Store, Conflict
from core.research.tradingagents import Advice, Candidate, ROLES, UPSTREAM, assess, freeze
from core.research.tradingagents_store import ResearchStore

NOW = datetime(2026, 10, 29, 8, tzinfo=timezone.utc)
ROOT = '/api/v1/directional'


def configuration():
    return Config(mode='RESEARCH_PAPER', instrument='XAU/USD', quote_source=source_for('XAU/USD'),
                  news_sources=('TEST_NEWS',), initial_cash='10000', tick_size='.01', quantity_step='.01',
                  max_quantity='10', max_order_quote='1000', fee_rate='.001', slippage='.05', quote_max_age_seconds=60,
                  decision_ttl_seconds=60, news_max_age_seconds=120, news_pre_seconds=1800, news_post_seconds=1800)


def news(impact=None, at=NOW):
    events = () if impact is None else (Event(event_id='test', instruments=('XAU/USD',), impact=impact,
                                            scheduled_at=at, known_at=at-timedelta(seconds=1)),)
    return News(source='TEST_NEWS', observed_at=at, coverage_start=at-timedelta(days=1), coverage_end=at+timedelta(days=1), events=events)


def account(tmp_path):
    store = Store(tmp_path / 'paper.sqlite3')
    view = store.create('test-owner', configuration(), str(uuid4()), NOW, metadata={'strategy': 'MANUAL'})
    store.on_quote(view['account_id'], Quote(instrument='XAU/USD', source=source_for('XAU/USD'), at=NOW, bid='100', ask='101'), now=NOW, news=news())
    return store, store.read('test-owner', view['account_id'])


def advice(snapshot, **changes):
    value = dict(adapter='fc666-tradingagents-shadow-v1', upstream_commit=UPSTREAM, snapshot_sha256=snapshot['sha256'],
                 instrument='XAU/USD', quote_source=source_for('XAU/USD'), produced_at=NOW,
                 valid_until=NOW+timedelta(minutes=4), rating='Buy', reports=tuple({'role': role, 'text': 'SYNTHETIC TEST ONLY'} for role in ROLES),
                 candidate={'target': 'LONG', 'quantity': '1', 'stop_loss': '95', 'take_profit': '110', 'reason': 'SYNTHETIC TEST ONLY'}, model='SYNTHETIC_MODEL', model_calls=9)
    value.update(changes)
    return Advice(**value)


@pytest.mark.parametrize('changes', [
    {'quantity': float('nan')}, {'quantity': 'NaN'}, {'quantity': 'Infinity'}, {'quantity': True},
    {'stop_loss': '-1'}, {'stop_loss': None}, {'take_profit': None}, {'quantity': 1.1},
    {'target': 'HOLD', 'quantity': '1'}, {'target': 'FLAT', 'quantity': '1'},
])
def test_candidate_cannot_accept_unsafe_or_incomplete_fields(changes):
    data = {'target': 'LONG', 'quantity': '1', 'stop_loss': '95', 'take_profit': '110', 'reason': 'test'}
    data.update(changes)
    with pytest.raises(ValidationError):
        Candidate(**data)


def test_snapshot_preserves_original_gold_market_and_explicit_gaps(tmp_path):
    _, view = account(tmp_path)
    frozen = freeze(view, now=NOW, news=None)
    assert frozen['snapshot']['config']['instrument'] == 'XAU/USD'
    assert 'GC=F' not in str(frozen)
    assert frozen['snapshot']['entry_gate'] == 'NEWS_UNAVAILABLE'
    assert frozen['snapshot']['sample_type'] == 'OBSERVED_QUOTES_NOT_CANDLES'
    assert 'ohlcv_indicators' in frozen['snapshot']['unavailable']


@pytest.mark.parametrize('impact,reason', [(None, 'SHADOW_READY'), ('HIGH', 'HIGH_IMPACT_EVENT')])
def test_assessment_never_executes_and_preserves_account(tmp_path, impact, reason):
    store, view = account(tmp_path)
    frozen = freeze(view, now=NOW, news=news(impact))
    before = store.read('test-owner', view['account_id'])
    result = assess(frozen, advice(frozen), view, now=NOW, news=news(impact))
    assert result['reason'] == reason and not result['execution_enabled'] and not result['trading_enabled']
    assert store.read('test-owner', view['account_id']) == before


@pytest.mark.parametrize('case,expected', [
    ('no-news', 'NEWS_UNAVAILABLE'), ('stale-news', 'NEWS_STALE'), ('future-news', 'NEWS_FROM_FUTURE'),
    ('coverage', 'NEWS_COVERAGE_INCOMPLETE'), ('stale-quote', 'QUOTE_STALE'), ('expired', 'ADVICE_EXPIRED'),
    ('revision', 'ACCOUNT_CHANGED'), ('context', 'CONTEXT_MISMATCH'), ('market', 'MARKET_IDENTITY_MISMATCH'),
    ('review', 'REVIEW_REQUIRED'), ('quantity', 'SIZE_OR_PRICE_LIMIT'), ('tick', 'SIZE_OR_PRICE_LIMIT'),
    ('budget', 'COLLATERAL_OR_ORDER_LIMIT'), ('bracket', 'BRACKET_INVALID_AT_QUOTE'), ('halted', 'ENTRY_HALTED'),
])
def test_fail_closed_boundaries(tmp_path, case, expected):
    _, view = account(tmp_path)
    frozen, current_news = freeze(view, now=NOW, news=news()), news()
    update = {}
    if case == 'no-news': current_news = None
    if case == 'stale-news': current_news = news(at=NOW-timedelta(minutes=3))
    if case == 'future-news': current_news = news(at=NOW+timedelta(seconds=1))
    if case == 'coverage': current_news = news().model_copy(update={'coverage_end': NOW})
    if case == 'stale-quote': view['last_quote']['at'] = (NOW-timedelta(minutes=2)).isoformat()
    if case == 'expired':
        older = deepcopy(view)
        older['last_quote']['at'] = (NOW-timedelta(minutes=2)).isoformat()
        frozen = freeze(older, now=NOW-timedelta(minutes=2), news=news(at=NOW-timedelta(minutes=2)))
        update['valid_until'] = NOW-timedelta(seconds=1)
        update['produced_at'] = NOW-timedelta(seconds=2)
    if case == 'revision': view['revision'] += 1
    if case == 'context': update['snapshot_sha256'] = 'a'*64
    if case == 'market': update['quote_source'] = 'yahoo.GC=F'
    if case == 'review': update['rating'] = 'REVIEW'
    if case in ('quantity', 'tick', 'budget', 'bracket'):
        candidate = {'target': 'LONG', 'quantity': '1', 'stop_loss': '95', 'take_profit': '110', 'reason': 'test'}
        candidate.update({'quantity': '11'} if case == 'quantity' else {'stop_loss': '95.001'} if case == 'tick' else {'quantity': '10'} if case == 'budget' else {'stop_loss': '100.01'})
        update['candidate'] = candidate
    if case == 'halted': view['status'] = 'STOPPED'
    assert assess(frozen, advice(frozen, **update), view, now=NOW, news=current_news)['reason'] == expected


def test_store_idempotency_owner_isolation_and_restart_interruption(tmp_path):
    jobs = ResearchStore(tmp_path / 'research.sqlite3')
    _, view = account(tmp_path)
    context = freeze(view, now=NOW, news=None)
    first = jobs.create('owner', view['account_id'], 'request', 'ANALYZE', context, NOW)
    assert jobs.create('owner', view['account_id'], 'request', 'ANALYZE', {'changed': True}, NOW) == first
    with pytest.raises(KeyError): jobs.read('outsider', first['job_id'])
    with pytest.raises(Conflict): jobs.create('owner', 'different-account', 'request', 'ANALYZE', context, NOW)
    with pytest.raises(Conflict): jobs.create('owner', view['account_id'], 'new-request', 'ANALYZE', context, NOW)
    assert jobs.claim()[1]['state'] == 'RUNNING'
    reopened = ResearchStore(jobs.path)
    reopened.recover(NOW+timedelta(seconds=1))
    assert reopened.read('owner', first['job_id'])['state'] == 'INTERRUPTED'
    assert reopened.claim() is None


def test_concurrent_same_request_creates_only_one_job(tmp_path):
    jobs = ResearchStore(tmp_path / 'research.sqlite3')
    _, view = account(tmp_path)
    context = freeze(view, now=NOW, news=None)
    with ThreadPoolExecutor(max_workers=4) as pool:
        rows = list(pool.map(lambda _: jobs.create('owner', view['account_id'], 'same', 'ANALYZE', context, NOW), range(4)))
    assert len({r['job_id'] for r in rows}) == 1
    assert len(jobs.listing('owner')) == 1


class FakeRunner:
    configured = True
    def __init__(self): self.accounts = set();self.calls = 0;self.bad = False
    def __call__(self, snapshot):
        self.calls += 1
        return advice(snapshot, quote_source='WRONG') if self.bad else advice(snapshot)


def http_account(client):
    body = {'request_id': str(uuid4()), 'instrument': 'XAU/USD', 'strategy': 'MANUAL', 'initial_cash': '10000',
            'tick_size': '.01', 'quantity_step': '.01', 'max_quantity': '10', 'max_order_quote': '1000',
            'fee_rate': '.001', 'slippage': '.05', 'news_pre_seconds': 1800, 'news_post_seconds': 1800}
    response = client.post(ROOT+'/accounts', json=body)
    assert response.status_code == 201
    return response.json()


def test_http_snapshot_analysis_ownership_no_execution_and_retry(tmp_path):
    runner = FakeRunner()
    app = create_app(tmp_path/'paper.sqlite3', background=False, secure=False, research_runner=runner,
                     clock=lambda: NOW, provider=lambda instrument: Quote(instrument=instrument, source=source_for(instrument), at=NOW, bid='100', ask='101'),
                     news_provider=lambda *args: news('HIGH'), news_source='TEST_NEWS')
    with TestClient(app) as client:
        view = http_account(client);runner.accounts.add(view['account_id']);app.state.poll_once()
        view = client.get(ROOT+'/accounts/'+view['account_id']).json()
        path = ROOT+'/research/accounts/'+view['account_id']
        body = {'request_id': str(uuid4()), 'mode': 'ANALYZE', 'expected_revision': view['revision']}
        response = client.post(path, json=body);assert response.status_code == 201
        job = response.json();app.state.research.run_once()
        completed = client.get(ROOT+'/research/jobs/'+job['job_id']).json()
        assert completed['state'] == 'COMPLETED'
        assert completed['result']['assessment']['reason'] == 'HIGH_IMPACT_EVENT'
        assert client.post(path, json=body).json()['job_id'] == job['job_id']
        app.state.research.run_once();assert runner.calls == 1
        assert client.get(ROOT+'/accounts/'+view['account_id']).json() == view
        with TestClient(app) as other:
            assert other.get(ROOT+'/research/jobs/'+job['job_id']).status_code in (401, 404)
        assert client.post(path, json={**body, 'request_id': str(uuid4())}, headers={'origin': 'https://evil.example'}).status_code == 403


def test_unconfigured_model_still_supports_real_snapshot_without_calls(tmp_path):
    runner = FakeRunner();runner.configured = False
    app = create_app(tmp_path/'paper.sqlite3', background=False, secure=False, research_runner=runner, clock=lambda: NOW)
    with TestClient(app) as client:
        view = http_account(client)
        path = ROOT+'/research/accounts/'+view['account_id']
        body = {'request_id': str(uuid4()), 'mode': 'PREPARE', 'expected_revision': view['revision']}
        response = client.post(path, json=body)
        assert response.status_code == 201
        assert response.json()['state'] == 'SNAPSHOT_ONLY'
        assert response.json()['snapshot']['snapshot']['entry_gate'] == 'QUOTE_UNAVAILABLE'
        assert client.post(path, json={**body, 'request_id': str(uuid4()), 'mode': 'ANALYZE'}).status_code == 503
        assert runner.calls == 0


@pytest.mark.parametrize('case', ['config', 'storage'])
def test_research_failure_does_not_disable_account_quote_protection(tmp_path, monkeypatch, case):
    if case == 'config':
        monkeypatch.setenv('FC666_TA_RUNNER_URL', 'https://worker.invalid')
        monkeypatch.delenv('FC666_TA_RUNNER_TOKEN', raising=False)
    else:
        with sqlite3.connect(tmp_path/'tradingagents.research.sqlite3') as db:
            db.execute('CREATE TABLE unrelated_data(value TEXT)')
    app = create_app(tmp_path/'paper.sqlite3', background=False, secure=False, clock=lambda: NOW,
                     provider=lambda instrument: Quote(instrument=instrument, source=source_for(instrument), at=NOW, bid='100', ask='101'))
    with TestClient(app) as client:
        view = http_account(client)
        app.state.poll_once()
        after = client.get(ROOT+'/accounts/'+view['account_id']).json()
        assert after['last_quote']['bid'] == '100' and after['fills_count'] == 0
        status = client.get(ROOT+'/research/status').json()
        assert not status['runner_configured']
        assert status['state'] == ('RESEARCH_CONFIG_INVALID' if case == 'config' else 'RESEARCH_STORAGE_UNAVAILABLE')
        assert client.get('/health/ready').status_code == 200
        if case == 'storage': assert client.get(ROOT+'/research/jobs').status_code == 503
