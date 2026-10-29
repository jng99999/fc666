"""Separate owner-scoped directional paper runtime, with no live execution path."""
from concurrent.futures import ThreadPoolExecutor, as_completed
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from decimal import Decimal, localcontext, ROUND_CEILING, ROUND_FLOOR
import hashlib
import logging
import os
from pathlib import Path
import re
import secrets
import sqlite3
from threading import Event, Lock, Thread
from typing import Literal
from uuid import UUID

from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.responses import JSONResponse
from pydantic import ConfigDict, Field, model_validator

from core.paper.directional import Boundary, Config, News, Proposal, Quote, entry_gate
from core.paper.directional_market import INSTRUMENTS, fetch_quote, instruments, source_for
from core.paper.directional_store import Conflict, Store
from core.backtest.spot import rounded

COOKIE = 'fc666_directional_session'
logger = logging.getLogger('fc666.directional')
UNSET = object()


class Create(Boundary):
    model_config = ConfigDict(frozen=True, extra='forbid', allow_inf_nan=False)
    request_id: UUID
    instrument: str = Field(min_length=1, max_length=32)
    strategy: Literal['MANUAL', 'SMA20_QUOTES']
    initial_cash: Decimal = Field(gt=0, le=1000000)
    tick_size: Decimal = Field(gt=0, le=1)
    quantity_step: Decimal = Field(gt=0, le=1)
    max_quantity: Decimal = Field(gt=0, le=1000)
    max_order_quote: Decimal = Field(gt=0, le=1000000)
    fee_rate: Decimal = Field(ge=0, le=Decimal('.1'))
    slippage: Decimal = Field(ge=0, le=1000)
    news_pre_seconds: int = Field(ge=0, le=86400, strict=True)
    news_post_seconds: int = Field(ge=0, le=86400, strict=True)
    quantity: Decimal | None = Field(default=None, gt=0, le=1000)
    stop_loss_percent: Decimal | None = Field(default=None, ge=Decimal('.0001'), le=Decimal('.5'))
    take_profit_percent: Decimal | None = Field(default=None, ge=Decimal('.0001'), lt=1)

    @model_validator(mode='after')
    def supported(self):
        source_for(self.instrument)
        automatic = (self.quantity, self.stop_loss_percent, self.take_profit_percent)
        if self.strategy == 'SMA20_QUOTES':
            if any(value is None for value in automatic):
                raise ValueError('Automatic quote strategy requires quantity and both protective percentages')
            if self.quantity > self.max_quantity or self.quantity % self.quantity_step:
                raise ValueError('Automatic quantity exceeds limit or does not match quantity step')
        elif any(value is not None for value in automatic):
            raise ValueError('Manual accounts configure protective prices on each decision')
        if self.max_order_quote > self.initial_cash:
            raise ValueError('Order budget cannot exceed initial fully collateralized paper balance')
        if self.instrument == 'XAU/USD' and self.max_quantity > 100:
            raise ValueError('Gold synthetic paper quantity limit is 100 ounces')
        return self


class Decision(Boundary):
    request_id: UUID
    expected_revision: int = Field(ge=0, strict=True)
    target: Literal['LONG', 'SHORT', 'FLAT']
    quantity: Decimal = Field(ge=0, le=1000)
    stop_loss: Decimal | None = Field(default=None, gt=0)
    take_profit: Decimal | None = Field(default=None, gt=0)


class Stop(Boundary):
    request_id: UUID
    expected_revision: int = Field(ge=0, strict=True)


def signal(view):
    """Distinct observed quote samples; deliberately not candles or AI analysis."""
    rows = view['recent_quotes']
    if len(rows) < 20:
        return {'state': 'WARMUP', 'samples': len(rows), 'required': 20, 'target': None, 'sma': None}
    with localcontext() as context:
        context.prec = 60
        mids = [(Decimal(row['bid']) + Decimal(row['ask'])) / 2 for row in rows[-20:]]
        average = sum(mids, Decimal(0)) / 20
        target = 'LONG' if mids[-1] > average else 'SHORT' if mids[-1] < average else 'FLAT'
        return {'state': 'READY', 'samples': 20, 'required': 20, 'target': target, 'sma': str(average),
                'available_at': rows[-1]['at'], 'sample_type': 'OBSERVED_QUOTES_NOT_CANDLES'}


def automatic_budget(view, target):
    """Do not repeatedly enqueue an already unaffordable automatic position."""
    if target == 'FLAT':
        return 'CLEAR'
    config = Config.model_validate(view['config'])
    quote = view['last_quote']
    with localcontext() as context:
        context.prec = 60
        price = rounded(Decimal(quote['ask']) + config.slippage if target == 'LONG'
                        else Decimal(quote['bid']) - config.slippage,
                        config.tick_size, ROUND_CEILING if target == 'LONG' else ROUND_FLOOR)
        quantity = Decimal(view['metadata']['quantity'])
        if price <= 0 or quantity <= 0 or quantity > config.max_quantity or quantity % config.quantity_step:
            return 'SIZE_OR_PRICE_LIMIT'
        notional = price * quantity
        if notional > config.max_order_quote or notional * (1 + config.fee_rate) > Decimal(view['balance']):
            return 'COLLATERAL_OR_ORDER_LIMIT'
        return 'CLEAR'


def file_news(path):
    """Operator-owned snapshot, never restamped or writable from a public route."""
    def read(instrument, now):
        value = Path(path)
        if value.stat().st_size > 2000000:
            raise ValueError('Oversized operator news snapshot')
        return News.model_validate_json(value.read_bytes())
    return read


def create_app(path=None, *, provider=fetch_quote, news_provider=None, news_source=None,
               background=True, secure=True, clock=None, interval=10, allowed_origins=None, research_runner=None):
    if os.environ.get('LIVE_TRADING', 'false').lower() not in ('false', '0'):
        raise ValueError('Directional service requires LIVE_TRADING=false')
    path = path or os.environ.get('DIRECTIONAL_DB_PATH', '/app/.runtime/directional/paper.sqlite3')
    news_source = news_source or os.environ.get('DIRECTIONAL_NEWS_SOURCE')
    if news_provider is None and os.environ.get('DIRECTIONAL_NEWS_FILE'):
        news_provider = file_news(os.environ['DIRECTIONAL_NEWS_FILE'])
    if news_provider is not None and not news_source:
        raise ValueError('A server-authorized news source name is required')
    if interval <= 0:
        raise ValueError('Positive quote polling interval required')
    origins = {value.strip() for value in (allowed_origins if allowed_origins is not None else
               os.environ.get('DIRECTIONAL_ALLOWED_ORIGINS', 'https://fc666-test.vercel.app').split(',')) if value.strip()}
    clock = clock or (lambda: datetime.now(timezone.utc))
    store = Store(path)
    stop = Event()
    lock = Lock()
    feed = {name: {'state': 'WAITING', 'last_checked': None, 'last_quote': None} for name in INSTRUMENTS}

    def snapshot(instrument, now):
        if news_provider is None:
            return None
        try:
            value = news_provider(instrument, now)
            return None if value is None else News.model_validate(value)
        except Exception:
            logger.warning('directional_news_unavailable')
            return None

    def enrich(view, *, news=UNSET, at=None):
        at = at or clock()
        config = Config.model_validate(view['config'])
        news = snapshot(config.instrument, at) if news is UNSET else news
        with lock:
            state = dict(feed[config.instrument])
        quote = None if view['last_quote'] is None else Quote.model_validate(view['last_quote'])
        gate = ('ENTRY_HALTED' if view['status'] == 'STOPPED' or view['entry_halted'] else
                'QUOTE_UNAVAILABLE' if quote is None else
                'QUOTE_STALE' if not 0 <= (at - quote.at).total_seconds() <= config.quote_max_age_seconds else
                entry_gate(config, at, news))
        analysis = signal(view) if view['metadata']['strategy'] == 'SMA20_QUOTES' else None
        if gate == 'CLEAR' and analysis is not None and analysis['state'] == 'READY' and view['position'] is None:
            gate = automatic_budget(view, analysis['target'])
        return {**view, 'entry_gate': gate, 'feed': state,
                'news': {'configured': news_provider is not None, 'source': news_source,
                         'observed_at': news.observed_at.isoformat() if news else None},
                'analysis': analysis}

    def auto_decide(view, now, news):
        if (view['metadata']['strategy'] != 'SMA20_QUOTES' or view['status'] != 'RUNNING'
                or view['pending'] is not None or view['entry_halted']):
            return
        result = signal(view)
        if result['state'] != 'READY':
            return
        current = view['position']['direction'] if view['position'] else 'FLAT'
        target = result['target']
        if current == target:
            return
        # Exit first, then wait for another independent sample before reversal.
        target = 'FLAT' if current != 'FLAT' else target
        if target != 'FLAT' and entry_gate(Config.model_validate(view['config']), now, news) != 'CLEAR':
            return
        if automatic_budget(view, target) != 'CLEAR':
            return
        metadata = view['metadata']
        last = view['last_quote']
        config = Config.model_validate(view['config'])
        with localcontext() as context:
            context.prec = 60
            base = (Decimal(last['ask']) + config.slippage if target == 'LONG'
                    else Decimal(last['bid']) - config.slippage)
            stop_percent = Decimal(metadata['stop_loss_percent'])
            take_percent = Decimal(metadata['take_profit_percent'])
            proposal = Proposal(request_id='sma20:' + last['at'], target=target, available_at=now,
                                quantity='0' if target == 'FLAT' else metadata['quantity'],
                                stop_loss=None if target == 'FLAT' else base * (1 - stop_percent if target == 'LONG' else 1 + stop_percent),
                                take_profit=None if target == 'FLAT' else base * (1 + take_percent if target == 'LONG' else 1 - take_percent))
        # Revision guards ensure a concurrent browser STOP wins over old intent.
        store.submit_automatic(view['account_id'], proposal, expected_revision=view['revision'], now=now, news=news)

    def poll_once():
        accounts = store.active()
        names = sorted({row['config']['instrument'] for row in accounts})
        if not names:
            return
        with ThreadPoolExecutor(max_workers=3, thread_name_prefix='directional-public') as pool:
            futures = {pool.submit(provider, name): name for name in names}
            for future in as_completed(futures):
                name = futures[future]
                stamp = clock()
                try:
                    quote = Quote.model_validate(future.result())
                    if quote.instrument != name or quote.source != source_for(name):
                        raise ValueError('Configured public quote source required')
                    if not 0 <= (stamp - quote.at).total_seconds() <= 60:
                        raise ValueError('Fresh nonfuture public quote required')
                    news = snapshot(name, stamp)
                    with lock:
                        feed[name] = {'state': 'CONNECTED', 'last_checked': stamp.isoformat(),
                                      'last_quote': quote.model_dump(mode='json')}
                    for row in accounts:
                        if row['config']['instrument'] != name:
                            continue
                        try:
                            previous = row['last_quote']
                            after = store.on_quote(row['account_id'], quote, now=stamp, news=news)
                            if previous is None or quote.at > Quote.model_validate(previous).at:
                                auto_decide(after, stamp, news)
                        except Conflict:
                            logger.info('directional_revision_changed')
                        except Exception:
                            logger.warning('directional_account_advance_unavailable')
                except Exception:
                    with lock:
                        feed[name] = {**feed[name], 'state': 'UNAVAILABLE', 'last_checked': stamp.isoformat()}
                    logger.warning('directional_public_quote_unavailable', extra={'instrument': name})

    def collect():
        while not stop.is_set():
            try:
                poll_once()
            except Exception:
                logger.warning('directional_collection_unavailable')
            stop.wait(interval)

    @asynccontextmanager
    async def lifespan(app):
        thread = Thread(target=collect, daemon=True, name='directional-paper-clock') if background else None
        if thread:
            thread.start()
        if background:
            research.start()
        yield
        stop.set()
        if thread:
            thread.join(timeout=8)
        research.close()

    app = FastAPI(title='FC666 Directional Research Paper', lifespan=lifespan)
    app.state.store = store
    app.state.poll_once = poll_once

    @app.middleware('http')
    async def boundary(request, call_next):
        if request.method == 'POST':
            origin = request.headers.get('origin')
            if request.headers.get('sec-fetch-site') == 'cross-site' or (origin and origin not in origins):
                return JSONResponse({'detail': 'Cross-site paper commands are not allowed'}, status_code=403,
                                    headers={'Cache-Control': 'no-store'})
            if request.headers.get('content-type', '').split(';')[0].strip() != 'application/json':
                return JSONResponse({'detail': 'JSON paper requests required'}, status_code=415,
                                    headers={'Cache-Control': 'no-store'})
            body = bytearray()
            async for chunk in request.stream():
                body.extend(chunk)
                if len(body) > 8192:
                    return JSONResponse({'detail': 'Paper request too large'}, status_code=413,
                                        headers={'Cache-Control': 'no-store'})
            request._body = bytes(body)
        result = await call_next(request)
        result.headers['Cache-Control'] = 'no-store'
        return result

    @app.exception_handler(Conflict)
    async def conflict_handler(request, error):
        return JSONResponse({'detail': str(error)}, status_code=409, headers={'Cache-Control': 'no-store'})

    @app.exception_handler(KeyError)
    async def missing_handler(request, error):
        return JSONResponse({'detail': 'Paper account not found in this browser session'}, status_code=404)

    @app.exception_handler(ValueError)
    async def value_handler(request, error):
        return JSONResponse({'detail': str(error)}, status_code=422)

    @app.exception_handler(sqlite3.Error)
    async def persistence_handler(request, error):
        logger.warning('directional_store_unavailable')
        return JSONResponse({'detail': 'Paper persistence temporarily unavailable'}, status_code=503)

    def owner(request, response, *, create=False):
        token = request.cookies.get(COOKIE)
        if not token or not re.fullmatch(r'[A-Za-z0-9_-]{43}', token):
            if not create:
                raise HTTPException(401, 'This browser has no directional paper session')
            token = secrets.token_urlsafe(32)
            response.set_cookie(COOKIE, token, max_age=31536000, httponly=True,
                                secure=secure, samesite='strict', path='/api')
        return hashlib.sha256(token.encode()).hexdigest()

    from apps.api.tradingagents_research import Service as ResearchService, router_for as research_router
    research = ResearchService(Path(path).with_name('tradingagents.research.sqlite3'), store, snapshot, clock, runner=research_runner)
    app.state.research = research
    app.include_router(research_router(research, owner))

    @app.get('/health/live')
    def live():
        return {'status': 'alive', 'service': 'directional-paper-v1', 'trading_enabled': False}

    @app.get('/health/ready')
    def ready():
        try:
            store.active()
            return {'status': 'ready', 'mode': 'RESEARCH_PAPER', 'trading_enabled': False}
        except (sqlite3.Error, ValueError):
            return JSONResponse({'status': 'not_ready', 'trading_enabled': False}, status_code=503)

    @app.get('/api/v1/directional/status')
    def status():
        with lock:
            current = {name: dict(value) for name, value in feed.items()}
        return {'service': 'directional-paper-v1', 'mode': 'RESEARCH_PAPER', 'trading_enabled': False,
                'ai': {'configured': False}, 'news': {'configured': news_provider is not None,
                'state': 'SERVER_CONFIGURED' if news_provider is not None else 'NEWS_UNAVAILABLE', 'source': news_source},
                'instruments': instruments(), 'feed': current,
                'strategies': ['MANUAL', 'SMA20_QUOTES'], 'server_time': clock().isoformat()}

    @app.get('/api/v1/directional/accounts')
    def accounts(request: Request, response: Response):
        identity = owner(request, response, create=True)
        return {'accounts': [enrich(row) for row in store.listing(identity)], 'trading_enabled': False}

    @app.post('/api/v1/directional/accounts', status_code=201)
    def create(body: Create, request: Request, response: Response):
        identity = owner(request, response, create=True)
        config = Config(mode='RESEARCH_PAPER', instrument=body.instrument, quote_source=source_for(body.instrument),
                        news_sources=(news_source or 'operator.calendar.v1',),
                        initial_cash=body.initial_cash, tick_size=body.tick_size, quantity_step=body.quantity_step,
                        max_quantity=body.max_quantity, max_order_quote=body.max_order_quote,
                        fee_rate=body.fee_rate, slippage=body.slippage, quote_max_age_seconds=60,
                        decision_ttl_seconds=60, news_max_age_seconds=120,
                        news_pre_seconds=body.news_pre_seconds, news_post_seconds=body.news_post_seconds)
        metadata = body.model_dump(mode='json', include={'strategy', 'quantity', 'stop_loss_percent', 'take_profit_percent'})
        return enrich(store.create(identity, config, str(body.request_id), clock(), metadata=metadata))

    @app.get('/api/v1/directional/accounts/{account_id}')
    def account(account_id: UUID, request: Request, response: Response):
        return enrich(store.read(owner(request, response), str(account_id)))

    @app.post('/api/v1/directional/accounts/{account_id}/decision')
    def decision(account_id: UUID, body: Decision, request: Request, response: Response):
        identity = owner(request, response)
        view = store.read(identity, str(account_id))
        if view['metadata']['strategy'] != 'MANUAL' and body.target != 'FLAT':
            raise HTTPException(409, 'Automatic accounts accept manual reduce-only FLAT decisions')
        stamp = clock()
        news = snapshot(view['config']['instrument'], stamp)
        previous = store.decision_request(identity, str(account_id), str(body.request_id))
        proposal = Proposal(request_id=str(body.request_id), target=body.target,
                            available_at=previous['available_at'] if previous else stamp,
                            quantity=body.quantity, stop_loss=body.stop_loss, take_profit=body.take_profit)
        return enrich(store.submit(identity, str(account_id), proposal, expected_revision=body.expected_revision,
                                   now=stamp, news=news), news=news, at=stamp)

    @app.post('/api/v1/directional/accounts/{account_id}/stop')
    def halt(account_id: UUID, body: Stop, request: Request, response: Response):
        identity = owner(request, response)
        return enrich(store.stop(identity, str(account_id), str(body.request_id), body.expected_revision, clock()))

    return app
