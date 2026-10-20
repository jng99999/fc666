"""Isolated XAU/USD paper service; no private broker client or exchange keys."""
from contextlib import asynccontextmanager
from decimal import Decimal
import hashlib
import logging
import os
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


def create_app(path=None, *, provider=fetch_quote, background=True, secure=True, clock=None):
    if os.environ.get('LIVE_TRADING', 'false').lower() not in ('false', '0'):
        raise ValueError('Gold service only supports LIVE_TRADING=false')
    path = path or os.environ.get('GOLD_DB_PATH', '/app/.runtime/gold/paper.sqlite3')
    store = Store(path)
    clock = clock or (lambda: int(time.time()*1000))
    stop = Event()
    feed = {'state': 'WAITING', 'last_checked': None}

    def collect():
        while not stop.is_set():
            try:
                quote = provider()
                store.put_quote(quote, clock())
                store.advance(quote, clock())
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
        yield
        stop.set()
        if thread:
            thread.join(timeout=8)

    app = FastAPI(title='FC666 XAU/USD Paper', lifespan=lifespan)
    app.state.store = store

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
        return JSONResponse({'detail': 'Gold account not found in this browser session'}, status_code=404)

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
        return {'accounts': store.accounts(identity), 'trading_enabled': False}

    @app.post('/api/v1/gold/accounts')
    def create(body: Create, request: Request, response: Response):
        identity = owner(request, response, create=True)
        config = body.model_dump(mode='json', exclude={'request_id'})
        return store.create(identity, config, str(body.request_id), clock())

    @app.get('/api/v1/gold/accounts/{account_id}')
    def account(account_id: UUID, request: Request, response: Response):
        return store.read(str(account_id), owner(request, response))

    @app.post('/api/v1/gold/accounts/{account_id}/command')
    def command(account_id: UUID, body: Command, request: Request, response: Response):
        data = store.market(clock())
        row = data['quote']
        quote = Quote(row['ts'], Decimal(row['bid']), Decimal(row['ask'])) if row else None
        return store.command(str(account_id), owner(request, response), body.model_dump(mode='json'), clock(), quote)

    @app.get('/api/v1/gold/accounts/{account_id}/events')
    def events(account_id: UUID, request: Request, response: Response, before: Optional[int] = Query(default=None, ge=1)):
        result = store.read(str(account_id), owner(request, response), before)
        return {'events': result['events'], 'next_before': result['events'][-1]['seq'] if len(result['events']) == 100 else None}

    return app
