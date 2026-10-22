"""Isolated XAU/USD paper service; no private broker client or exchange keys."""
from contextlib import asynccontextmanager
from decimal import Decimal
import hashlib
import logging
import os
from pathlib import Path
import re
import secrets
from threading import Event, Thread
import time
from typing import Literal, Optional
from uuid import UUID

from fastapi import FastAPI, HTTPException, Query, Request, Response
from pydantic import BaseModel, ConfigDict, Field, field_validator

from core.gold.ledger import Store, Conflict
from core.gold.market import Quote, fetch_quote
from core.gold.history import Catalogue
from core.gold.research import Research
from apps.api.gold_research import router_for
from core.gold.policy_runtime import PolicyRuntime, Registry

COOKIE = 'fc666_gold_session'


class Config(BaseModel):
    model_config = ConfigDict(extra='forbid')
    mode: Literal['MANUAL', 'SMA20']
    initial_cash: Decimal = Field(gt=0, le=1000000)
    allocation: Decimal = Field(gt=0, le=Decimal('.5'))
    max_spread: Decimal = Field(gt=0, le=10)
    slippage: Decimal = Field(ge=0, le=10)
    commission: Decimal = Field(ge=0, le=10)

    @field_validator('initial_cash', 'allocation', 'max_spread', 'slippage', 'commission', mode='before')
    @classmethod
    def exact(cls, value):
        if not isinstance(value, (str, Decimal, int)) or isinstance(value, bool):
            raise ValueError('Use decimal strings for gold account money')
        return value


class Create(Config):
    request_id: UUID


class Command(BaseModel):
    model_config = ConfigDict(extra='forbid')
    request_id: UUID
    expected_revision: int = Field(ge=1, strict=True)
    action: Literal['BUY', 'SELL', 'PAUSE', 'RESUME']
    ounces: Optional[str] = Field(default=None, max_length=32)


class PolicyCreate(Config):
    mode: Literal['POLICY']
    model_id: str = Field(pattern=r'^[a-zA-Z0-9_-]{1,64}$')
    experimental: bool = Field(strict=True)
    request_id: UUID


class PolicyCommand(BaseModel):
    model_config = ConfigDict(extra='forbid')
    request_id: UUID
    expected_revision: int = Field(ge=1, strict=True)
    action: Literal['DECIDE', 'PAUSE', 'RESUME', 'STOP']


def create_app(path=None, *, provider=fetch_quote, background=True, secure=True, clock=None, history_path=None,
               policy_registry_path=None, policy_enabled=None):
    if os.environ.get('LIVE_TRADING', 'false').lower() not in ('false', '0'):
        raise ValueError('Gold service only supports LIVE_TRADING=false')
    path = path or os.environ.get('GOLD_DB_PATH', '/app/.runtime/gold/paper.sqlite3')
    store = Store(path)
    clock = clock or (lambda: int(time.time()*1000))
    policy = PolicyRuntime(store, Registry(policy_registry_path or os.environ.get('GOLD_POLICY_REGISTRY')),
                           clock, enabled=(os.environ.get('GOLD_POLICY_ENABLED', 'false') == 'true'
                                           if policy_enabled is None else policy_enabled))
    research = Research(store, Catalogue(history_path or os.environ.get('GOLD_HISTORY_PATH', str(Path(path).parent/'history'))), clock)
    stop = Event()
    feed = {'state': 'WAITING', 'last_checked': None}

    def collect():
        while not stop.is_set():
            try:
                quote = provider()
                store.put_quote(quote, clock())
                store.advance(quote, clock())
                policy.advance()
                feed.update(state='CONNECTED', last_checked=clock())
            except Exception:
                feed.update(state='UNAVAILABLE', last_checked=clock())
                logging.getLogger('fc666.gold').warning('gold_public_sampling_unavailable')
            stop.wait(10)

    @asynccontextmanager
    async def lifespan(app):
        thread = Thread(target=collect, daemon=True, name='gold-paper-clock') if background else None
        if thread:
            thread.start()
        researcher = Thread(target=research.work, args=(stop,), daemon=True, name='gold-research') if background else None
        if researcher:
            research.recover()
            researcher.start()
        yield
        stop.set()
        if thread:
            thread.join(timeout=8)
        if researcher:
            researcher.join(timeout=1)

    app = FastAPI(title='FC666 XAU/USD Paper', lifespan=lifespan)
    app.state.store = store
    app.state.research = research
    app.state.policy = policy

    def owner(request, response, create=False):
        token = request.cookies.get(COOKIE)
        if not token or not re.fullmatch(r'[A-Za-z0-9_-]{43}', token):
            if not create:
                raise HTTPException(401, 'This browser has no gold paper session')
            token = secrets.token_urlsafe(32)
            response.set_cookie(COOKIE, token, max_age=31536000, httponly=True,
                                secure=secure, samesite='strict', path='/api')
        return hashlib.sha256(token.encode()).hexdigest()

    @app.exception_handler(Conflict)
    async def conflict_handler(request, error):
        from fastapi.responses import JSONResponse
        return JSONResponse({'detail': str(error)}, status_code=409)

    @app.exception_handler(ValueError)
    async def value_handler(request, error):
        from fastapi.responses import JSONResponse
        return JSONResponse({'detail': str(error)}, status_code=422)

    @app.exception_handler(KeyError)
    async def missing_handler(request, error):
        from fastapi.responses import JSONResponse
        message = '黄金研究任务或数据不存在，或不属于此浏览器会话' if '/research/' in request.url.path else 'Gold account not found in this browser session'
        return JSONResponse({'detail': message}, status_code=404)

    @app.middleware('http')
    async def boundary(request, call_next):
        if request.method == 'POST':
            if int(request.headers.get('content-length', '0')) > 8192:
                from fastapi.responses import JSONResponse
                return JSONResponse({'detail': 'Gold request too large'}, status_code=413)
            if request.headers.get('content-type', '').split(';')[0] != 'application/json':
                from fastapi.responses import JSONResponse
                return JSONResponse({'detail': 'JSON gold requests required'}, status_code=415)
        result = await call_next(request)
        result.headers['Cache-Control'] = 'no-store'
        return result

    @app.get('/health/live')
    def health():
        return {'status': 'alive', 'service': 'gold-paper-v1', 'trading_enabled': False, 'feed': feed}

    @app.get('/api/v1/gold/market')
    def market():
        return {**store.market(clock()), 'feed': dict(feed)}

    @app.get('/api/v1/gold/accounts')
    def accounts(request: Request, response: Response):
        identity = owner(request, response, create=True)
        return {'accounts': [row for row in store.accounts(identity) if row['mode'] != 'POLICY'], 'trading_enabled': False}

    @app.post('/api/v1/gold/accounts')
    def create(body: Create, request: Request, response: Response):
        identity = owner(request, response, create=True)
        config = body.model_dump(mode='json', exclude={'request_id'})
        return store.create(identity, config, str(body.request_id), clock())

    @app.get('/api/v1/gold/accounts/{account_id}')
    def account(account_id: UUID, request: Request, response: Response):
        result = store.read(str(account_id), owner(request, response))
        if result['mode'] == 'POLICY':
            raise KeyError(str(account_id))
        return result

    @app.post('/api/v1/gold/accounts/{account_id}/command')
    def command(account_id: UUID, body: Command, request: Request, response: Response):
        data = store.market(clock())
        row = data['quote']
        quote = Quote(row['ts'], Decimal(row['bid']), Decimal(row['ask'])) if row else None
        return store.command(str(account_id), owner(request, response), body.model_dump(mode='json'), clock(), quote)

    @app.get('/api/v1/gold/accounts/{account_id}/events')
    def events(account_id: UUID, request: Request, response: Response, before: Optional[int] = Query(default=None, ge=1)):
        result = store.read(str(account_id), owner(request, response), before)
        if result['mode'] == 'POLICY':
            raise KeyError(str(account_id))
        return {'events': result['events'], 'next_before': result['events'][-1]['seq'] if len(result['events']) == 100 else None}

    @app.get('/api/v1/gold/policy/status')
    def policy_status():
        return policy.status()

    @app.get('/api/v1/gold/policy/accounts')
    def policy_accounts(request: Request, response: Response):
        identity = owner(request, response, create=True)
        return {'accounts': [row for row in store.accounts(identity) if row['mode'] == 'POLICY'],
                'enabled': policy.enabled, 'trading_enabled': False}

    @app.get('/api/v1/gold/policy/accounts/{account_id}')
    def policy_account(account_id: UUID, request: Request, response: Response,
                       before: Optional[int] = Query(default=None, ge=1)):
        result = store.read(str(account_id), owner(request, response), before)
        if result['mode'] != 'POLICY':
            raise KeyError(str(account_id))
        return {**result, 'next_before': result['events'][-1]['seq'] if len(result['events']) == 100 else None}

    @app.post('/api/v1/gold/policy/accounts')
    def policy_create(body: PolicyCreate, request: Request, response: Response):
        config = body.model_dump(mode='json', exclude={'request_id', 'model_id', 'experimental'})
        return policy.create(owner(request, response, create=True), config, body.model_id,
                             str(body.request_id), body.experimental)

    @app.post('/api/v1/gold/policy/accounts/{account_id}/command')
    def policy_command(account_id: UUID, body: PolicyCommand, request: Request, response: Response):
        return policy.command(str(account_id), owner(request, response), body.model_dump(mode='json'))

    app.include_router(router_for(research, owner))
    return app
