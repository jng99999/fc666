"""Isolated quote-driven directional research; not a spot ledger or broker adapter.

One fully collateralized synthetic position, no leverage/financing/liquidation model.
Only observed executable bid/ask quotes trigger stops; no fabricated intrabar fills.
This module is deliberately not registered with the public account API or worker.
"""
from datetime import datetime, timedelta, timezone
from decimal import Decimal, localcontext, ROUND_FLOOR, ROUND_CEILING
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from core.backtest.spot import digest, rounded

VERSION = 'directional-quote-research-v1'
Number = Decimal


class Boundary(BaseModel):
    model_config = ConfigDict(frozen=True, extra='forbid', allow_inf_nan=False)

    @field_validator('*', mode='before')
    @classmethod
    def exact(cls, value):
        if isinstance(value, (float, bool)):
            raise ValueError('Exact values required; floats and booleans are not accepted')
        if isinstance(value, datetime):
            if value.tzinfo is None or value.utcoffset() is None:
                raise ValueError('Timezone-aware timestamp required')
            return value.astimezone(timezone.utc)
        return value

    @field_validator('*', mode='after')
    @classmethod
    def aware_timestamps(cls, value):
        if isinstance(value, datetime):
            if value.tzinfo is None or value.utcoffset() is None:
                raise ValueError('Timezone-aware timestamp required')
            return value.astimezone(timezone.utc)
        return value


class Quote(Boundary):
    instrument: str = Field(min_length=1, max_length=128)
    source: str = Field(min_length=1, max_length=128)
    at: datetime
    bid: Number = Field(gt=0, max_digits=38, decimal_places=18)
    ask: Number = Field(gt=0, max_digits=38, decimal_places=18)

    @model_validator(mode='after')
    def valid(self):
        if self.bid > self.ask or self.at.tzinfo is None:
            raise ValueError('Uncrossed bid/ask and aware clock required')
        return self


class Event(Boundary):
    event_id: str = Field(min_length=1, max_length=256)
    instruments: tuple[str, ...] = Field(min_length=1, max_length=100)
    impact: Literal['LOW', 'MEDIUM', 'HIGH']
    scheduled_at: datetime
    known_at: datetime


class News(Boundary):
    source: str = Field(min_length=1, max_length=128)
    observed_at: datetime
    coverage_start: datetime
    coverage_end: datetime
    events: tuple[Event, ...] = Field(max_length=10000)

    @model_validator(mode='after')
    def valid(self):
        if self.coverage_start >= self.coverage_end:
            raise ValueError('Ordered news coverage required')
        if any(e.known_at > self.observed_at for e in self.events):
            raise ValueError('News snapshot contains not-yet-known event')
        if len({e.event_id for e in self.events}) != len(self.events):
            raise ValueError('Unique news event IDs required')
        return self


class Config(Boundary):
    mode: Literal['RESEARCH_PAPER']
    instrument: str = Field(min_length=1, max_length=128)
    quote_source: str = Field(min_length=1, max_length=128)
    news_sources: tuple[str, ...] = Field(min_length=1, max_length=20)
    initial_cash: Number = Field(gt=0, le=1000000000)
    tick_size: Number = Field(gt=0, le=1000000)
    quantity_step: Number = Field(gt=0, le=1000000)
    max_quantity: Number = Field(gt=0, le=1000000)
    max_order_quote: Number = Field(gt=0, le=1000000000)
    fee_rate: Number = Field(ge=0, le=Decimal('.1'))
    slippage: Number = Field(ge=0, le=1000000)
    quote_max_age_seconds: int = Field(ge=1, le=300, strict=True)
    decision_ttl_seconds: int = Field(ge=1, le=3600, strict=True)
    news_max_age_seconds: int = Field(ge=1, le=3600, strict=True)
    news_pre_seconds: int = Field(ge=0, le=86400, strict=True)
    news_post_seconds: int = Field(ge=0, le=86400, strict=True)


class Proposal(Boundary):
    request_id: str = Field(min_length=1, max_length=128)
    target: Literal['LONG', 'SHORT', 'FLAT']
    available_at: datetime
    quantity: Number = Field(ge=0, le=1000000)
    stop_loss: Number | None = Field(default=None, gt=0)
    take_profit: Number | None = Field(default=None, gt=0)

    @model_validator(mode='after')
    def valid(self):
        if self.target == 'FLAT':
            if self.quantity != 0 or self.stop_loss is not None or self.take_profit is not None:
                raise ValueError('FLAT is reduce-only with no size or bracket')
        elif self.quantity <= 0 or self.stop_loss is None or self.take_profit is None:
            raise ValueError('Entries require positive size and both protective prices')
        return self


def aware(stamp):
    if not isinstance(stamp, datetime) or stamp.tzinfo is None or stamp.utcoffset() is None:
        raise ValueError('Timezone-aware observation time required')
    return stamp.astimezone(timezone.utc)


def entry_gate(config: Config, at: datetime, news: News | None):
    at = aware(at)
    if news is None:
        return 'NEWS_UNAVAILABLE'
    if news.source not in config.news_sources:
        return 'NEWS_SOURCE_UNTRUSTED'
    age = (at - news.observed_at).total_seconds()
    if age < 0:
        return 'NEWS_FROM_FUTURE'
    if age > config.news_max_age_seconds:
        return 'NEWS_STALE'
    # Coverage must include the entire embargo lookahead/lookback, not just now.
    before, after = timedelta(seconds=config.news_pre_seconds), timedelta(seconds=config.news_post_seconds)
    if news.coverage_start > at - after or news.coverage_end < at + before:
        return 'NEWS_COVERAGE_INCOMPLETE'
    for event in news.events:
        if (event.impact == 'HIGH' and (config.instrument in event.instruments or '*' in event.instruments)
                and event.scheduled_at - before <= at <= event.scheduled_at + after):
            return 'HIGH_IMPACT_EVENT'
    return 'CLEAR'


class DirectionalPaper:
    """Bounded offline state machine. Calls are sequential; persistence is not provided."""

    def __init__(self, config: Config):
        self.config = config
        self.balance = config.initial_cash
        self.fees = Decimal(0)
        self.gross_pnl = Decimal(0)
        self.position = None
        self.pending = None
        self.pending_accepted_at = None
        self.quote = None
        self.observed_at = None
        self.halted = False
        self.fills = []
        self.journal = []
        self.requests = {}
        self.news_snapshots = {}

    def _news_evidence(self, news):
        if news is None:
            return None
        payload = news.model_dump(mode='json')
        identity = digest(payload)
        self.news_snapshots[identity] = payload
        return identity

    def _record(self, kind, details):
        payload = {'seq': len(self.journal) + 1, 'kind': kind, 'details': details,
                   'previous_hash': self.journal[-1]['sha256'] if self.journal else None}
        self.journal.append({**payload, 'sha256': digest(payload)})

    def _fresh(self, quote, now):
        if quote.instrument != self.config.instrument or quote.source != self.config.quote_source:
            raise ValueError('Matching configured instrument and quote source required')
        if not 0 <= (now - quote.at).total_seconds() <= self.config.quote_max_age_seconds:
            raise ValueError('Fresh nonfuture quote required')
        if quote.bid - self.config.slippage < self.config.tick_size:
            raise ValueError('No positive executable price after adverse cost and tick rounding')

    def submit(self, proposal: Proposal, *, now: datetime, news: News | None):
        now = aware(now)
        encoded = proposal.model_dump(mode='json')
        old = self.requests.get(proposal.request_id)
        if old is not None:
            if old['proposal'] != encoded:
                raise ValueError('Decision request ID conflict')
            return dict(old['result'])
        if len(self.requests) >= 10000:
            raise ValueError('Research decision limit reached')
        if self.quote is None:
            raise ValueError('Observe executable quote before submitting decision')
        if now < self.observed_at:
            raise ValueError('Observation clock cannot move backwards')
        self._fresh(self.quote, now)
        if proposal.available_at < self.quote.at or proposal.available_at > now:
            raise ValueError('Decision must be available at or after last quote and not in future')
        if (now - proposal.available_at).total_seconds() > self.config.decision_ttl_seconds:
            raise ValueError('Decision expired')
        if self.pending is not None:
            raise ValueError('A decision is already pending')
        reason = 'CLEAR'
        if proposal.target != 'FLAT':
            reason = 'ENTRY_HALTED' if self.halted or self.balance <= 0 else entry_gate(self.config, now, news)
            if self.position is not None:
                reason = 'FLATTEN_BEFORE_REENTRY'
        result = {'status': 'ACCEPTED' if reason == 'CLEAR' else 'REJECTED', 'reason': reason}
        if reason == 'CLEAR':
            self.pending = proposal
            self.pending_accepted_at = now
        self.observed_at = now
        self.requests[proposal.request_id] = {'proposal': encoded, 'result': result}
        self._record('DECISION', {'proposal': encoded, 'observed_at': now.isoformat(),
                                  'news_sha256': self._news_evidence(news), **result})
        return dict(result)

    def on_quote(self, quote: Quote, *, now: datetime, news: News | None):
        now = aware(now)
        self._fresh(quote, now)
        if self.observed_at is not None and now < self.observed_at:
            raise ValueError('Observation clock cannot move backwards')
        if self.quote is not None and quote.at <= self.quote.at:
            if quote == self.quote:
                return self.view()
            raise ValueError('Quotes must increase; timestamp revisions are forbidden')
        if len(self.journal) >= 100000:
            raise ValueError('Research event limit reached')
        self.quote, self.observed_at = quote, now
        with localcontext() as ctx:
            ctx.prec = 60
            # Protection runs before news/entry controls, including during entry halt.
            if self.position is not None:
                p = self.position
                mark = quote.bid if p['direction'] == 'LONG' else quote.ask
                stop = mark <= p['stop_loss'] if p['direction'] == 'LONG' else mark >= p['stop_loss']
                profit = mark >= p['take_profit'] if p['direction'] == 'LONG' else mark <= p['take_profit']
                if stop or profit:
                    self._close('STOP_LOSS' if stop else 'TAKE_PROFIT')
                    self.pending = None  # never re-enter on a protective-exit quote
                    self.pending_accepted_at = None
            pending = self.pending
            if pending is not None and quote.at > max(pending.available_at, self.pending_accepted_at):
                accepted_at = self.pending_accepted_at
                self.pending = None
                self.pending_accepted_at = None
                if pending.target == 'FLAT':
                    if self.position is not None:
                        self._close('SIGNAL_EXIT')
                else:
                    reason = ('DECISION_EXPIRED' if (now - pending.available_at).total_seconds() > self.config.decision_ttl_seconds
                              else 'ENTRY_HALTED' if self.halted or self.balance <= 0 else entry_gate(self.config, now, news))
                    if reason == 'CLEAR':
                        reason = self._open(pending, accepted_at)
                    if reason != 'CLEAR':
                        self._record('ENTRY_REJECTED', {'request_id': pending.request_id, 'reason': reason})
            self._record('QUOTE', {'quote': quote.model_dump(mode='json'), 'observed_at': now.isoformat(),
                                   'news_sha256': self._news_evidence(news)})
            return self.view()

    def _open(self, proposal, accepted_at):
        c = self.config
        direction = proposal.target
        price = rounded(self.quote.ask + c.slippage if direction == 'LONG' else self.quote.bid - c.slippage,
                        c.tick_size, ROUND_CEILING if direction == 'LONG' else ROUND_FLOOR)
        quantity = (proposal.quantity / c.quantity_step).to_integral_value(rounding=ROUND_FLOOR) * c.quantity_step
        if price <= 0 or quantity <= 0 or quantity != proposal.quantity or quantity > c.max_quantity:
            return 'SIZE_OR_PRICE_LIMIT'
        if not (proposal.stop_loss < price < proposal.take_profit if direction == 'LONG'
                else proposal.take_profit < price < proposal.stop_loss):
            return 'BRACKET_INVALID_AT_FILL'
        mark = self.quote.bid if direction == 'LONG' else self.quote.ask
        if not (proposal.stop_loss < mark < proposal.take_profit if direction == 'LONG'
                else proposal.take_profit < mark < proposal.stop_loss):
            return 'BRACKET_ALREADY_TRIGGERED'
        notional = quantity * price
        fee = notional * c.fee_rate
        if notional > c.max_order_quote or notional + fee > self.balance:
            return 'COLLATERAL_OR_ORDER_LIMIT'
        self.balance -= fee
        self.fees += fee
        self.position = {'direction': direction, 'quantity': quantity, 'entry': price,
                         'entry_decision_id': proposal.request_id, 'reserved_collateral': notional,
                         'stop_loss': proposal.stop_loss, 'take_profit': proposal.take_profit}
        self._fill('OPEN', direction, quantity, price, fee, Decimal(0), 'SIGNAL_ENTRY',
                   decision_at=proposal.available_at.isoformat(), accepted_at=accepted_at.isoformat())
        return 'CLEAR'

    def _close(self, reason):
        p = self.position
        direction = p['direction']
        price = rounded(self.quote.bid - self.config.slippage if direction == 'LONG' else self.quote.ask + self.config.slippage,
                        self.config.tick_size, ROUND_FLOOR if direction == 'LONG' else ROUND_CEILING)
        pnl = p['quantity'] * (price - p['entry']) * (1 if direction == 'LONG' else -1)
        fee = p['quantity'] * price * self.config.fee_rate
        self.balance += pnl - fee
        self.gross_pnl += pnl
        self.fees += fee
        self.position = None
        if self.balance <= 0:
            self.halted = True
        self._fill('CLOSE', direction, p['quantity'], price, fee, pnl, reason)

    def _fill(self, action, direction, quantity, price, fee, pnl, reason, **timing):
        fill = {'action': action, 'direction': direction, 'quantity': str(quantity), 'price': str(price),
                'fee': str(fee), 'gross_pnl': str(pnl), 'reason': reason,
                'quote_at': self.quote.at.isoformat(), 'observed_at': self.observed_at.isoformat(), 'simulated': True,
                'quote_source': self.quote.source, 'reference_bid': str(self.quote.bid), 'reference_ask': str(self.quote.ask), **timing}
        self.fills.append(fill)
        self._record('FILL', fill)

    def view(self):
        with localcontext() as ctx:
            ctx.prec = 60
            unrealized = Decimal(0)
            if self.position is not None:
                p = self.position
                mark = self.quote.bid if p['direction'] == 'LONG' else self.quote.ask
                unrealized = p['quantity'] * (mark - p['entry']) * (1 if p['direction'] == 'LONG' else -1)
            if abs(self.balance - self.config.initial_cash - self.gross_pnl + self.fees) > Decimal('1e-40'):
                raise ArithmeticError('Directional paper balance conservation failed')
            return {'engine_version': VERSION, 'mode': 'RESEARCH_PAPER', 'trading_enabled': False,
                    'balance': str(self.balance), 'equity': str(self.balance + unrealized), 'fees': str(self.fees),
                    'gross_pnl': str(self.gross_pnl), 'unrealized_pnl': str(unrealized), 'entry_halted': self.halted,
                    'position': None if self.position is None else {k: str(v) for k, v in self.position.items()},
                    'pending': None if self.pending is None else {**self.pending.model_dump(mode='json'),
                                                                 'accepted_at': self.pending_accepted_at.isoformat()},
                    'fills': [dict(fill) for fill in self.fills], 'events': len(self.journal),
                    'journal_sha256': self.journal[-1]['sha256'] if self.journal else None}
