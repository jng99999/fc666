"""Owner-scoped, bounded shadow jobs. This module never submits an account decision."""
from datetime import datetime
import json
import logging
import os
from pathlib import Path
import sqlite3
import time
from threading import Event, Thread
from urllib.parse import urlsplit
from uuid import UUID

from fastapi import APIRouter, HTTPException, Request, Response
import httpx
from pydantic import Field

from core.paper.directional import Boundary
from core.research.tradingagents import Advice, UPSTREAM, assess, freeze
from core.research.tradingagents_store import ResearchStore

logger = logging.getLogger('fc666.tradingagents')


class Create(Boundary):
    request_id: UUID
    mode: str = Field(pattern=r'^(PREPARE|ANALYZE)$')
    expected_revision: int = Field(ge=0, strict=True)


class Control(Boundary):
    request_id: UUID
    expected_epoch: int = Field(ge=0, strict=True)
    expected_revision: int = Field(ge=0, strict=True)
    action: str = Field(pattern=r'^(START|PAUSE)$')
    interval_seconds: int = Field(ge=300, le=86400, strict=True)


def unique(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('Duplicate research JSON key')
        result[key] = value
    return result


class Runner:
    def __init__(self, settings=None):
        self.settings = settings
        self.url = os.environ.get('FC666_TA_RUNNER_URL')
        self.token = os.environ.get('FC666_TA_RUNNER_TOKEN')
        values = json.loads(os.environ.get('FC666_TA_ACCOUNT_IDS', '[]')) if settings is None else []
        if not isinstance(values, list) or len(values) > 20 or len(set(values)) != len(values):
            raise ValueError('At most twenty explicit research account IDs')
        self._accounts = {str(UUID(value)) for value in values}
        if bool(self.url) != bool(self.token):
            raise ValueError('Research runner URL and token must be configured together')
        if self.url:
            parsed = urlsplit(self.url)
            if parsed.scheme not in ('http', 'https') or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
                raise ValueError('Operator-configured research endpoint required')
            if len(self.token) < 32:
                raise ValueError('Research runner token must contain at least 32 characters')
        self.configured = bool(self.url and self.token)
        self.cached_ready = (0.0, False, 'WAIT_MODEL_CONFIG')
        self.cached_revision = None

    @property
    def accounts(self):
        if self.settings is not None:
            return set(self.settings.read()['authorized_accounts'])
        return self._accounts

    def availability(self):
        if not self.configured:
            return False, 'WAIT_MODEL_CONFIG'
        revision = self.settings.revision() if self.settings is not None else None
        if revision != self.cached_revision:
            self.cached_ready = (0.0, False, 'WAIT_MODEL_CONFIG')
            self.cached_revision = revision
        if time.monotonic() - self.cached_ready[0] < 15:
            return self.cached_ready[1:]
        try:
            with httpx.Client(timeout=4, follow_redirects=False, trust_env=False) as client:
                response = client.get(self.url.rstrip('/') + '/health/ready')
                response.raise_for_status()
                value = response.json()
                ready = value.get('model_configured') is True and value.get('trading_enabled') is False
                state = 'READY' if ready else 'WAIT_MODEL_CONFIG'
        except Exception:
            ready, state = False, 'MODEL_UNAVAILABLE'
        self.cached_ready = (time.monotonic(), ready, state)
        return ready, state

    def __call__(self, snapshot):
        with httpx.Client(timeout=httpx.Timeout(110, connect=4), follow_redirects=False) as client:
            with client.stream('POST', self.url.rstrip('/') + '/analyze',
                               headers={'Authorization': 'Bearer ' + self.token}, json=snapshot) as response:
                if response.status_code != 200:
                    raise ValueError('RESEARCH_RUNNER_UNAVAILABLE')
                body = bytearray()
                for chunk in response.iter_bytes():
                    body.extend(chunk)
                    if len(body) > 160000:
                        raise ValueError('RESEARCH_RESPONSE_TOO_LARGE')
        return Advice.model_validate(json.loads(body, object_pairs_hook=unique))


class Service:
    def __init__(self, path, accounts, news, clock, runner=None, settings=None, require_operator=None):
        self.error = None
        try:
            self.jobs = ResearchStore(path)
        except (OSError, sqlite3.Error, ValueError):
            self.jobs = None
            self.error = 'RESEARCH_STORAGE_UNAVAILABLE'
            logger.warning('research_storage_unavailable')
        self.accounts, self.news, self.clock = accounts, news, clock
        self.settings, self.require_operator = settings, require_operator
        try:
            self.runner = runner if runner is not None else Runner(settings=settings)
        except (ValueError, TypeError):
            self.runner = type('UnavailableRunner', (), {'configured': False, 'accounts': frozenset()})()
            self.error = self.error or 'RESEARCH_CONFIG_INVALID'
            logger.warning('research_config_invalid')
        self.stop = Event()
        self.thread = None
        self.evidence_cache = {}
        self.automation = None
        if self.jobs is not None:
            try:
                from apps.api.ai_paper import Automation
                self.automation = Automation(self)
            except (OSError, sqlite3.Error, ValueError):
                logger.warning('ai_paper_controls_unavailable')

    def status(self):
        ready, state = self.runner.availability() if hasattr(self.runner, 'availability') else (self.runner.configured, 'READY' if self.runner.configured else 'WAIT_MODEL_CONFIG')
        return {'framework': 'TradingAgents', 'upstream_commit': UPSTREAM,
                'runner_configured': bool(self.runner.configured and self.jobs is not None), 'mode': 'SHADOW_ONLY',
                'model_configured': bool(ready and self.jobs is not None),
                'automation_supported': self.automation is not None,
                'state': self.error or state,
                'automatic_execution': False, 'trading_enabled': False}

    def storage(self):
        if self.jobs is None:
            raise HTTPException(503, 'Research storage is unavailable; paper accounts are unchanged')
        return self.jobs

    def freeze(self, view, now, news):
        from core.research.market_evidence import evidence
        instrument = view['config']['instrument']
        key = (instrument, int(now.timestamp()) // 60,
               self.settings.revision() if self.settings is not None else None)
        if key not in self.evidence_cache:
            self.evidence_cache = {k: v for k, v in self.evidence_cache.items() if k[1] == key[1]}
            self.evidence_cache[key] = (evidence(instrument, now,
                gold_settings=self.settings.read()['gold']) if self.settings is not None else evidence(instrument, now))
        return freeze(view, now=now, news=news, evidence=self.evidence_cache[key])

    def start(self):
        if self.jobs is None:
            return
        try:
            self.jobs.recover(self.clock())
        except (sqlite3.Error, OSError):
            self.jobs = None
            self.error = 'RESEARCH_STORAGE_UNAVAILABLE'
            logger.warning('research_recovery_unavailable')
            return
        self.thread = Thread(target=self.collect, daemon=True, name='tradingagents-paper')
        self.thread.start()

    def close(self):
        self.stop.set()
        if self.thread:
            self.thread.join(timeout=1)

    def collect(self):
        while not self.stop.is_set():
            try:
                if self.automation:
                    self.automation.schedule()
                self.run_once()
            except Exception:
                logger.warning('research_job_loop_unavailable')
            self.stop.wait(1)

    def run_once(self):
        claimed = self.storage().claim()
        if claimed is None:
            return
        owner, job = claimed
        result = None
        try:
            if not self.runner.configured or job['account_id'] not in self.runner.accounts:
                raise ValueError('RESEARCH_NOT_AUTHORIZED')
            if self.clock() >= datetime.fromisoformat(job['snapshot']['snapshot']['valid_until']):
                raise ValueError('RESEARCH_SNAPSHOT_EXPIRED')
            advice = Advice.model_validate(self.runner(job['snapshot']))
            view = self.accounts.read(owner, job['account_id'])
            now = self.clock()
            check = assess(job['snapshot'], advice, view, now=now, news=self.news(view['config']['instrument'], now))
            result = {'advice': advice.model_dump(mode='json'), 'assessment': check}
            # Persist model evidence before account admission so recovery never recalls a paid model.
            self.jobs.save_result(owner, job['job_id'], result)
            if job['mode'] == 'AUTO' and self.automation:
                self.automation.apply(owner, job, advice, check)
            self.jobs.finish(owner, job['job_id'], 'COMPLETED', result, now)
        except Exception:
            # Do not persist provider bodies, exception text, tokens or prompts.
            logger.warning('research_job_failed')
            self.jobs.finish(owner, job['job_id'], 'FAILED', result or {'reason': 'RESEARCH_INVALID_OR_UNAVAILABLE'}, self.clock())


def router_for(service, owner):
    router = APIRouter(prefix='/api/v1/directional/research')

    @router.get('/status')
    def status():
        return service.status()

    @router.get('/automation/{account_id}')
    def automation_status(account_id: UUID, request: Request, response: Response):
        if service.automation is None:
            raise HTTPException(503, 'AI paper controls unavailable')
        return service.automation.view(owner(request, response), str(account_id))

    @router.post('/automation/{account_id}')
    def automation_control(account_id: UUID, body: Control, request: Request, response: Response):
        if service.automation is None:
            raise HTTPException(503, 'AI paper controls unavailable')
        if body.action == 'START' and service.require_operator is not None:
            service.require_operator(request)
        return service.automation.control(owner(request, response), str(account_id), body.model_dump(mode='json'))

    @router.get('/jobs')
    def jobs(request: Request, response: Response):
        identity = owner(request, response, create=True)
        allowed = [row['account_id'] for row in service.accounts.listing(identity) if row['account_id'] in service.runner.accounts]
        return {'jobs': service.storage().listing(identity), 'authorized_accounts': allowed, **service.status()}

    @router.get('/jobs/{job_id}')
    def job(job_id: UUID, request: Request, response: Response):
        return service.storage().read(owner(request, response), str(job_id))

    @router.post('/accounts/{account_id}', status_code=201)
    def create(account_id: UUID, body: Create, request: Request, response: Response):
        if body.mode == 'ANALYZE' and service.require_operator is not None:
            service.require_operator(request)
        identity = owner(request, response)
        account = str(account_id)
        # Authenticate even idempotent reads; never trust account IDs from another session.
        view = service.accounts.read(identity, account)
        jobs = service.storage()
        previous = jobs.prior(identity, str(body.request_id), account, body.mode)
        if previous is not None:
            return previous
        if view['revision'] != body.expected_revision:
            raise HTTPException(409, 'Account changed; refresh before creating research snapshot')
        if body.mode == 'ANALYZE' and (not service.status()['model_configured'] or account not in service.runner.accounts):
            raise HTTPException(503, 'AI research is not configured or this account is not authorized; no model call was made')
        now = service.clock()
        frozen = service.freeze(view, now, service.news(view['config']['instrument'], now))
        if body.mode == 'ANALYZE' and frozen['snapshot']['entry_gate'] in ('QUOTE_UNAVAILABLE', 'QUOTE_STALE'):
            raise HTTPException(409, 'A fresh real quote is required for model research')
        return jobs.create(identity, account, str(body.request_id), body.mode, frozen, now)

    return router
