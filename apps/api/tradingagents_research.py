"""Owner-scoped, bounded shadow jobs. This module never submits an account decision."""
from datetime import datetime
import json
import logging
import os
from pathlib import Path
import sqlite3
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


def unique(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('Duplicate research JSON key')
        result[key] = value
    return result


class Runner:
    def __init__(self):
        self.url = os.environ.get('FC666_TA_RUNNER_URL')
        self.token = os.environ.get('FC666_TA_RUNNER_TOKEN')
        values = json.loads(os.environ.get('FC666_TA_ACCOUNT_IDS', '[]'))
        if not isinstance(values, list) or len(values) > 20 or len(set(values)) != len(values):
            raise ValueError('At most twenty explicit research account IDs')
        self.accounts = {str(UUID(value)) for value in values}
        if bool(self.url) != bool(self.token):
            raise ValueError('Research runner URL and token must be configured together')
        if self.url:
            parsed = urlsplit(self.url)
            if parsed.scheme not in ('http', 'https') or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
                raise ValueError('Operator-configured research endpoint required')
            if len(self.token) < 32:
                raise ValueError('Research runner token must contain at least 32 characters')
        self.configured = bool(self.url and self.token)

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
    def __init__(self, path, accounts, news, clock, runner=None):
        self.error = None
        try:
            self.jobs = ResearchStore(path)
        except (OSError, sqlite3.Error, ValueError):
            self.jobs = None
            self.error = 'RESEARCH_STORAGE_UNAVAILABLE'
            logger.warning('research_storage_unavailable')
        self.accounts, self.news, self.clock = accounts, news, clock
        try:
            self.runner = runner if runner is not None else Runner()
        except (ValueError, TypeError):
            self.runner = type('UnavailableRunner', (), {'configured': False, 'accounts': frozenset()})()
            self.error = self.error or 'RESEARCH_CONFIG_INVALID'
            logger.warning('research_config_invalid')
        self.stop = Event()
        self.thread = None

    def status(self):
        return {'framework': 'TradingAgents', 'upstream_commit': UPSTREAM,
                'runner_configured': bool(self.runner.configured and self.jobs is not None), 'mode': 'SHADOW_ONLY',
                'state': self.error or ('READY' if self.runner.configured else 'WAIT_MODEL_CONFIG'),
                'automatic_execution': False, 'trading_enabled': False}

    def storage(self):
        if self.jobs is None:
            raise HTTPException(503, 'Research storage is unavailable; paper accounts are unchanged')
        return self.jobs

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
        if self.runner.configured:
            self.thread = Thread(target=self.collect, daemon=True, name='tradingagents-shadow')
            self.thread.start()

    def close(self):
        self.stop.set()
        if self.thread:
            self.thread.join(timeout=1)

    def collect(self):
        while not self.stop.is_set():
            try:
                self.run_once()
            except Exception:
                logger.warning('research_job_loop_unavailable')
            self.stop.wait(1)

    def run_once(self):
        claimed = self.storage().claim()
        if claimed is None:
            return
        owner, job = claimed
        try:
            if not self.runner.configured or job['account_id'] not in self.runner.accounts:
                raise ValueError('RESEARCH_NOT_AUTHORIZED')
            if self.clock() >= datetime.fromisoformat(job['snapshot']['snapshot']['valid_until']):
                raise ValueError('RESEARCH_SNAPSHOT_EXPIRED')
            advice = Advice.model_validate(self.runner(job['snapshot']))
            view = self.accounts.read(owner, job['account_id'])
            now = self.clock()
            check = assess(job['snapshot'], advice, view, now=now, news=self.news(view['config']['instrument'], now))
            self.jobs.finish(owner, job['job_id'], 'COMPLETED',
                             {'advice': advice.model_dump(mode='json'), 'assessment': check}, now)
        except Exception:
            # Do not persist provider bodies, exception text, tokens or prompts.
            logger.warning('research_job_failed')
            self.jobs.finish(owner, job['job_id'], 'FAILED', {'reason': 'RESEARCH_INVALID_OR_UNAVAILABLE'}, self.clock())


def router_for(service, owner):
    router = APIRouter(prefix='/api/v1/directional/research')

    @router.get('/status')
    def status():
        return service.status()

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
        if body.mode == 'ANALYZE' and (not service.runner.configured or account not in service.runner.accounts):
            raise HTTPException(503, 'AI research is not configured or this account is not authorized; no model call was made')
        now = service.clock()
        frozen = freeze(view, now=now, news=service.news(view['config']['instrument'], now))
        if body.mode == 'ANALYZE' and frozen['snapshot']['entry_gate'] in ('QUOTE_UNAVAILABLE', 'QUOTE_STALE'):
            raise HTTPException(409, 'A fresh real quote is required for model research')
        return jobs.create(identity, account, str(body.request_id), body.mode, frozen, now)

    return router
