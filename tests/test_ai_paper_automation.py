"""Synthetic AI PAPER integration tests: no external models or live accounts."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
import sqlite3
from uuid import uuid4

import pytest

from apps.api.ai_paper import Automation
from apps.api.tradingagents_research import Service
from core.paper.directional import Config, Event, News, Quote
from core.paper.directional_market import source_for
from core.paper.directional_store import Conflict, Store
from core.research.tradingagents import Advice, ROLES, UPSTREAM, assess, freeze


BASE = datetime(2026, 11, 2, 8, tzinfo=timezone.utc)
OWNER = 'synthetic-ai-owner'


class FakeRunner:
    def __init__(self):
        self.configured = True
        self.accounts = set()
        self.calls = 0
        self.before_return = None

    def __call__(self, snapshot):
        self.calls += 1
        if self.before_return is not None:
            self.before_return()
        return make_advice(snapshot)


def make_advice(snapshot, **updates):
    value = dict(
        adapter='fc666-tradingagents-shadow-v1', upstream_commit=UPSTREAM,
        snapshot_sha256=snapshot['sha256'], instrument='XAU/USD', quote_source=source_for('XAU/USD'),
        produced_at=datetime.fromisoformat(snapshot['snapshot']['at']),
        valid_until=datetime.fromisoformat(snapshot['snapshot']['valid_until']), rating='Buy',
        reports=tuple({'role': role, 'text': 'SYNTHETIC FIXTURE ONLY'} for role in ROLES),
        candidate={'target': 'LONG', 'quantity': '1', 'stop_loss': '95', 'take_profit': '110',
                   'reason': 'SYNTHETIC FIXTURE ONLY'}, model='SYNTHETIC_NO_API_CALL', model_calls=9,
    )
    value.update(updates)
    return Advice(**value)


class Scene:
    def __init__(self, tmp_path, *, strategy='AI_RESEARCH'):
        self.now = BASE
        self.news_state = 'CLEAR'
        self.store = Store(tmp_path / 'paper.sqlite3')
        config = Config(
            mode='RESEARCH_PAPER', instrument='XAU/USD', quote_source=source_for('XAU/USD'),
            news_sources=('SYNTHETIC_NEWS',), initial_cash='10000', tick_size='.01', quantity_step='.01',
            max_quantity='10', max_order_quote='1000', fee_rate='.001', slippage='.05',
            quote_max_age_seconds=60, decision_ttl_seconds=60, news_max_age_seconds=120,
            news_pre_seconds=1800, news_post_seconds=1800,
        )
        row = self.store.create(OWNER, config, str(uuid4()), self.now, {'strategy': strategy})
        self.account_id = row['account_id']
        self.runner = FakeRunner()
        self.runner.accounts.add(self.account_id)
        self.service = Service(tmp_path / 'research.sqlite3', self.store, self.news, lambda: self.now,
                               runner=self.runner)
        self.service.freeze = lambda view, now, news: freeze(view, now=now, news=news, evidence={
            'instrument': 'XAU/USD', 'quote_source': source_for('XAU/USD'), 'status': 'READY',
            'synthetic_fixture': True,
        })
        self.automation = Automation(self.service)
        self.service.automation = self.automation
        self.quote(0)

    def news(self, instrument, now):
        if self.news_state == 'MISSING':
            return None
        observed = now - timedelta(seconds=121) if self.news_state == 'STALE' else now
        events = () if self.news_state != 'HIGH' else (
            Event(event_id='synthetic-high', instruments=(instrument,), impact='HIGH',
                  scheduled_at=now, known_at=observed - timedelta(seconds=1)),
        )
        return News(source='SYNTHETIC_NEWS', observed_at=observed,
                    coverage_start=now - timedelta(days=1), coverage_end=now + timedelta(days=1),
                    events=events)

    def account(self):
        return self.store.read(OWNER, self.account_id)

    def quote(self, seconds, bid='100', ask='101'):
        self.now = BASE + timedelta(seconds=seconds)
        return self.store.on_quote(
            self.account_id, Quote(instrument='XAU/USD', source=source_for('XAU/USD'), at=self.now,
                                   bid=bid, ask=ask), self.now, self.news('XAU/USD', self.now),
        )

    def command(self, action, **updates):
        state = self.automation.view(OWNER, self.account_id)
        body = {'request_id': str(uuid4()), 'expected_epoch': state['epoch'],
                'expected_revision': self.account()['revision'], 'action': action, 'interval_seconds': 300}
        body.update(updates)
        return self.automation.control(OWNER, self.account_id, body)

    def claim(self):
        self.automation.schedule()
        claimed = self.service.jobs.claim()
        assert claimed is not None
        owner, job = claimed
        assert owner == OWNER and job['account_id'] == self.account_id
        return job

    def apply(self, job, advice=None, check=None):
        advice = make_advice(job['snapshot']) if advice is None else advice
        check = assess(job['snapshot'], advice, self.account(), now=self.now,
                       news=self.news('XAU/USD', self.now)) if check is None else check
        return self.automation.apply(OWNER, job, advice, check)


def test_unconfigured_start_waits_without_creating_jobs_or_calling_model(tmp_path):
    scene = Scene(tmp_path)
    scene.runner.configured = False
    scene.command('START')
    scene.automation.schedule()
    scene.service.run_once()
    state = scene.automation.view(OWNER, scene.account_id)
    assert state['state'] == 'WAIT_MODEL_CONFIG'
    assert state['trading_enabled'] is False
    assert scene.service.jobs.listing(OWNER) == [] and scene.runner.calls == 0
    assert scene.account()['pending'] is None and scene.account()['fills_count'] == 0


def test_missing_history_prevents_new_job_and_model_call(tmp_path):
    scene = Scene(tmp_path)
    scene.service.freeze = lambda view, now, news: freeze(view, now=now, news=news, evidence={
        'instrument': 'XAU/USD', 'quote_source': source_for('XAU/USD'), 'status': 'UNAVAILABLE',
        'synthetic_fixture': True,
    })
    scene.command('START')
    scene.automation.schedule()
    scene.service.run_once()
    assert scene.automation.view(OWNER, scene.account_id)['state'] == 'HISTORY_UNAVAILABLE'
    assert scene.service.jobs.listing(OWNER) == [] and scene.runner.calls == 0


def test_control_view_never_fetches_remote_history_or_http(tmp_path, monkeypatch):
    scene = Scene(tmp_path)
    scene.command('START')

    def forbidden(*args, **kwargs):
        raise AssertionError('Read-only control view must not request external services')

    monkeypatch.setattr('core.research.market_evidence.evidence', forbidden)
    monkeypatch.setattr('httpx.Client.request', forbidden)
    monkeypatch.setattr(scene.service, 'freeze', forbidden)
    for _ in range(3):
        assert scene.automation.view(OWNER, scene.account_id)['enabled']
    assert scene.runner.calls == 0


@pytest.mark.parametrize('strategy', ['MANUAL', 'SMA20_QUOTES'])
def test_start_cannot_take_over_existing_non_ai_strategy(tmp_path, strategy):
    scene = Scene(tmp_path, strategy=strategy)
    before = scene.account()
    with pytest.raises(Conflict):
        scene.command('START')
    assert scene.account() == before


def test_owner_isolation_and_stale_control_epoch(tmp_path):
    scene = Scene(tmp_path)
    with pytest.raises(KeyError):
        scene.automation.view('other-owner', scene.account_id)
    with pytest.raises(KeyError):
        scene.automation.control('other-owner', scene.account_id, {
            'request_id': str(uuid4()), 'expected_epoch': 0, 'expected_revision': scene.account()['revision'],
            'action': 'START', 'interval_seconds': 300,
        })
    scene.command('START')
    old_epoch = scene.automation.view(OWNER, scene.account_id)['epoch']
    scene.command('PAUSE')
    with pytest.raises(Conflict):
        scene.command('START', expected_epoch=old_epoch)
    assert not scene.automation.view(OWNER, scene.account_id)['enabled']


def test_concurrent_controls_with_one_epoch_allow_only_one_transition(tmp_path):
    scene = Scene(tmp_path)
    state = scene.automation.view(OWNER, scene.account_id)
    revision = scene.account()['revision']

    def start(_):
        try:
            return scene.automation.control(OWNER, scene.account_id, {
                'request_id': str(uuid4()), 'expected_epoch': state['epoch'], 'expected_revision': revision,
                'action': 'START', 'interval_seconds': 300,
            })
        except Conflict:
            return None

    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(start, range(4)))
    assert sum(result is not None for result in results) == 1


def test_pause_control_retry_recovers_when_sidecar_write_was_interrupted(tmp_path, monkeypatch):
    scene = Scene(tmp_path)
    scene.command('START')
    state = scene.automation.view(OWNER, scene.account_id)
    body = {'request_id': str(uuid4()), 'expected_epoch': state['epoch'],
            'expected_revision': scene.account()['revision'], 'action': 'PAUSE', 'interval_seconds': 300}

    def unavailable(*args):
        raise sqlite3.OperationalError('SYNTHETIC control receipt interruption')

    with monkeypatch.context() as patch:
        patch.setattr(scene.automation, 'save', unavailable)
        with pytest.raises(sqlite3.OperationalError):
            scene.automation.control(OWNER, scene.account_id, body)
    account = scene.account()
    assert account['metadata']['ai_paused'] is True
    restored = scene.automation.control(OWNER, scene.account_id, body)
    assert restored['enabled'] is False and restored['epoch'] == state['epoch'] + 1
    assert scene.automation.control(OWNER, scene.account_id, body) == restored
    assert scene.account()['revision'] == account['revision']


def test_schedule_and_runner_apply_once_then_fill_on_next_quote(tmp_path):
    scene = Scene(tmp_path)
    scene.command('START')
    scene.automation.schedule()
    scene.automation.schedule()
    assert len(scene.service.jobs.listing(OWNER)) == 1
    scene.service.run_once()
    scene.service.run_once()
    assert scene.runner.calls == 1
    assert scene.account()['pending']['target'] == 'LONG'
    assert scene.account()['fills_count'] == 0
    opened = scene.quote(1)
    assert opened['position']['direction'] == 'LONG' and opened['fills_count'] == 1
    assert opened['trading_enabled'] is False


def test_duplicate_job_reply_never_creates_a_second_fill(tmp_path):
    scene = Scene(tmp_path)
    scene.command('START')
    job = scene.claim()
    scene.apply(job)
    scene.apply(job)
    assert scene.account()['pending']['target'] == 'LONG'
    scene.quote(1)
    scene.apply(job)
    assert scene.account()['fills_count'] == 1 and scene.account()['pending'] is None


@pytest.mark.parametrize('target', ['LONG', 'SHORT'])
def test_fill_binds_job_and_updates_view_receipt_through_protective_exit(tmp_path, target):
    scene = Scene(tmp_path)
    scene.command('START')
    job = scene.claim()
    candidate = {'target': target, 'quantity': '1', 'reason': 'SYNTHETIC binding test',
                 'stop_loss': '95' if target == 'LONG' else '110',
                 'take_profit': '110' if target == 'LONG' else '95'}
    scene.apply(job, make_advice(job['snapshot'], candidate=candidate))
    opened = scene.quote(1)
    assert opened['fills'][0]['request_id'] == job['job_id']
    receipt = scene.automation.view(OWNER, scene.account_id)['receipt']
    assert receipt['job_id'] == job['job_id'] and receipt['status'] == 'FILLED'
    assert receipt['reason'] == 'SIGNAL_ENTRY'
    closed = scene.quote(2, '94', '95') if target == 'LONG' else scene.quote(2, '111', '112')
    assert closed['fills'][-1]['request_id'] == job['job_id']
    receipt = scene.automation.view(OWNER, scene.account_id)['receipt']
    assert receipt['status'] == 'FILLED' and receipt['reason'] == 'STOP_LOSS'


def test_restart_recovers_control_and_job_receipt_without_duplicate_order(tmp_path):
    scene = Scene(tmp_path)
    scene.command('START')
    job = scene.claim()
    scene.apply(job)
    before = scene.automation.view(OWNER, scene.account_id)
    scene.automation = Automation(scene.service)
    scene.service.automation = scene.automation
    restored = scene.automation.view(OWNER, scene.account_id)
    assert restored['epoch'] == before['epoch'] and restored['last_job'] == before['last_job']
    scene.apply(job)
    assert scene.quote(1)['fills_count'] == 1
    scene.apply(job)
    assert scene.account()['pending'] is None and scene.account()['fills_count'] == 1


def test_receipt_recovers_when_control_write_fails_after_paper_acceptance(tmp_path, monkeypatch):
    scene = Scene(tmp_path)
    scene.command('START')
    job = scene.claim()

    def unavailable(*args):
        raise sqlite3.OperationalError('SYNTHETIC control write interruption')

    with monkeypatch.context() as patch:
        patch.setattr(scene.automation, 'save', unavailable)
        with pytest.raises(sqlite3.OperationalError):
            scene.apply(job)
    original = scene.store.decision_request(OWNER, scene.account_id, job['job_id'])
    assert original is not None and scene.account()['pending'] is not None
    scene.now = BASE + timedelta(seconds=1)
    receipt = scene.apply(job)
    assert receipt['status'] == 'ACCEPTED'
    assert scene.store.decision_request(OWNER, scene.account_id, job['job_id']) == original
    assert scene.quote(2)['fills_count'] == 1


def test_read_only_view_recovers_committed_receipt_without_replaying_apply(tmp_path, monkeypatch):
    scene = Scene(tmp_path)
    scene.command('START')
    job = scene.claim()

    def unavailable(*args):
        raise sqlite3.OperationalError('SYNTHETIC control write interruption')

    with monkeypatch.context() as patch:
        patch.setattr(scene.automation, 'save', unavailable)
        with pytest.raises(sqlite3.OperationalError):
            scene.apply(job)
    before = scene.account()
    with scene.automation.connect() as db:
        control_before = db.execute('SELECT value FROM controls WHERE account=?', (scene.account_id,)).fetchone()[0]

    def forbidden(*args, **kwargs):
        raise AssertionError('Read-only recovery must not apply, submit, advance or persist control')

    monkeypatch.setattr(scene.automation, 'apply', forbidden)
    monkeypatch.setattr(scene.automation, 'save', forbidden)
    monkeypatch.setattr(scene.store, 'submit_ai', forbidden)
    monkeypatch.setattr(scene.store, 'on_quote', forbidden)
    for _ in range(2):
        receipt = scene.automation.view(OWNER, scene.account_id)['receipt']
        assert receipt['status'] == 'ACCEPTED' and receipt['request_id'] == job['job_id']
    assert scene.account() == before
    with scene.automation.connect() as db:
        control_after = db.execute('SELECT value FROM controls WHERE account=?', (scene.account_id,)).fetchone()[0]
    assert control_after == control_before


def test_pause_cancels_pending_and_old_in_flight_reply(tmp_path):
    scene = Scene(tmp_path)
    scene.command('START')
    job = scene.claim()
    scene.apply(job)
    assert scene.account()['pending'] is not None
    scene.command('PAUSE')
    assert scene.account()['pending'] is None
    scene.apply(job)
    assert scene.quote(1)['fills_count'] == 0
    assert not scene.automation.view(OWNER, scene.account_id)['enabled']


def test_pause_keeps_open_position_protection_during_news_outage(tmp_path):
    scene = Scene(tmp_path)
    scene.command('START')
    scene.apply(scene.claim())
    assert scene.quote(1)['position'] is not None
    scene.command('PAUSE')
    scene.news_state = 'MISSING'
    closed = scene.quote(2, '94', '95')
    assert closed['position'] is None and closed['fills_count'] == 2
    assert closed['fills'][-1]['reason'] == 'STOP_LOSS'


def test_hold_records_decision_without_an_order(tmp_path):
    scene = Scene(tmp_path)
    scene.command('START')
    job = scene.claim()
    receipt = scene.apply(job, make_advice(job['snapshot'], candidate={
        'target': 'HOLD', 'quantity': '0', 'reason': 'SYNTHETIC insufficient evidence',
    }))
    assert receipt['status'] == 'HOLD'
    assert scene.account()['pending'] is None and scene.account()['fills_count'] == 0


@pytest.mark.parametrize('state', ['MISSING', 'STALE', 'HIGH'])
def test_ai_reduce_only_exit_remains_available_when_news_blocks_entries(tmp_path, state):
    scene = Scene(tmp_path)
    scene.command('START')
    first = scene.claim()
    scene.apply(first)
    scene.service.jobs.finish(OWNER, first['job_id'], 'COMPLETED', {}, scene.now)
    assert scene.quote(1)['position'] is not None
    scene.news_state = state
    scene.quote(300)
    closing = scene.claim()
    receipt = scene.apply(closing, make_advice(closing['snapshot'], candidate={
        'target': 'FLAT', 'quantity': '0', 'reason': 'SYNTHETIC reduce existing risk',
    }))
    assert receipt['status'] == 'ACCEPTED'
    reduced = scene.quote(301)
    assert reduced['position'] is None and reduced['fills_count'] == 2
    assert reduced['fills'][-1]['reason'] == 'SIGNAL_EXIT'
    assert reduced['fills'][-1]['request_id'] == closing['job_id']
    assert scene.automation.view(OWNER, scene.account_id)['receipt']['status'] == 'FILLED'


def test_expired_flat_is_rejected_without_delaying_existing_stop_protection(tmp_path):
    scene = Scene(tmp_path)
    scene.command('START')
    opening = scene.claim()
    scene.apply(opening)
    scene.service.jobs.finish(OWNER, opening['job_id'], 'COMPLETED', {}, scene.now)
    scene.quote(1)
    scene.quote(300)
    closing = scene.claim()
    receipt = scene.apply(closing, make_advice(closing['snapshot'], candidate={
        'target': 'FLAT', 'quantity': '0', 'reason': 'SYNTHETIC expiring reduction',
    }))
    assert receipt['status'] == 'ACCEPTED'
    scene.news_state = 'MISSING'
    expired = scene.quote(361)
    assert expired['position'] is not None and expired['pending'] is None and expired['fills_count'] == 1
    assert expired['last_outcome']['reason'] == 'DECISION_EXPIRED'
    assert expired['last_outcome']['request_id'] == closing['job_id']
    rejected = scene.automation.view(OWNER, scene.account_id)['receipt']
    assert rejected['status'] == 'REJECTED' and rejected['reason'] == 'DECISION_EXPIRED'
    closed = scene.quote(362, '94', '95')
    assert closed['position'] is None and closed['fills_count'] == 2
    assert closed['fills'][-1]['reason'] == 'STOP_LOSS'
    assert closed['fills'][-1]['request_id'] == opening['job_id']
    rejected = scene.automation.view(OWNER, scene.account_id)['receipt']
    assert rejected['status'] == 'REJECTED' and rejected['reason'] == 'DECISION_EXPIRED'


@pytest.mark.parametrize('state', ['MISSING', 'STALE', 'HIGH'])
def test_current_news_rechecked_before_ai_submission(tmp_path, state):
    scene = Scene(tmp_path)
    scene.command('START')
    job = scene.claim()
    advice = make_advice(job['snapshot'])
    previously_clear = assess(job['snapshot'], advice, scene.account(), now=scene.now,
                              news=scene.news('XAU/USD', scene.now))
    assert previously_clear['reason'] == 'SHADOW_READY'
    scene.news_state = state
    scene.apply(job, advice, previously_clear)
    assert scene.account()['pending'] is None and scene.account()['fills_count'] == 0


@pytest.mark.parametrize('state', ['MISSING', 'STALE', 'HIGH'])
def test_news_rechecked_again_at_fill(tmp_path, state):
    scene = Scene(tmp_path)
    scene.command('START')
    scene.apply(scene.claim())
    assert scene.account()['pending'] is not None
    scene.news_state = state
    rejected = scene.quote(1)
    assert rejected['pending'] is None and rejected['fills_count'] == 0


@pytest.mark.parametrize('case', ['expired', 'market', 'context', 'review'])
def test_invalid_or_expired_advice_never_enters_paper_ledger(tmp_path, case):
    scene = Scene(tmp_path)
    scene.command('START')
    job = scene.claim()
    updates = {'quote_source': 'WRONG'} if case == 'market' else {'snapshot_sha256': 'a' * 64} if case == 'context' else {'rating': 'REVIEW'} if case == 'review' else {
        'produced_at': BASE - timedelta(seconds=2), 'valid_until': BASE - timedelta(seconds=1),
    }
    scene.apply(job, make_advice(job['snapshot'], **updates))
    assert scene.account()['pending'] is None and scene.account()['fills_count'] == 0


def test_advice_deadline_cannot_be_extended_by_paper_decision_ttl(tmp_path):
    scene = Scene(tmp_path)
    scene.command('START')
    job = scene.claim()
    scene.apply(job, make_advice(job['snapshot'], valid_until=BASE + timedelta(seconds=5)))
    expired = scene.quote(6)
    assert expired['position'] is None and expired['fills_count'] == 0


def test_pause_during_model_call_rejects_the_old_epoch_reply(tmp_path):
    scene = Scene(tmp_path)
    scene.command('START')
    scene.automation.schedule()
    scene.runner.before_return = lambda: scene.command('PAUSE')
    scene.service.run_once()
    assert scene.runner.calls == 1
    assert scene.account()['pending'] is None and scene.account()['fills_count'] == 0
    assert not scene.automation.view(OWNER, scene.account_id)['enabled']


def test_paper_admission_failure_cannot_leave_job_marked_completed(tmp_path, monkeypatch):
    scene = Scene(tmp_path)
    scene.command('START')
    scene.automation.schedule()

    def unavailable(*args):
        raise sqlite3.OperationalError('SYNTHETIC admission interruption')

    monkeypatch.setattr(scene.automation, 'apply', unavailable)
    scene.service.run_once()
    job = scene.service.jobs.listing(OWNER)[0]
    assert job['state'] == 'FAILED'
    assert scene.account()['pending'] is None and scene.account()['fills_count'] == 0
