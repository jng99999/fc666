"""TradingAgents shadow contract. Research results never write account state."""
from datetime import datetime, timedelta
from decimal import Decimal, ROUND_CEILING, ROUND_FLOOR, localcontext
from typing import Literal

from pydantic import Field, model_validator

from core.backtest.spot import digest, rounded
from core.paper.directional import Boundary, Config, Quote, entry_gate, aware

UPSTREAM = 'c4544913a14ed3df5f1ed7b9e8483270ce20682a'
ROLES = ('bull', 'bear', 'research_manager', 'trader', 'aggressive', 'conservative', 'neutral', 'portfolio_manager')


class Candidate(Boundary):
    target: Literal['HOLD', 'LONG', 'SHORT', 'FLAT']
    quantity: Decimal = Field(ge=0, le=1000000, max_digits=38, decimal_places=18)
    stop_loss: Decimal | None = Field(default=None, gt=0, max_digits=38, decimal_places=18)
    take_profit: Decimal | None = Field(default=None, gt=0, max_digits=38, decimal_places=18)
    reason: str = Field(min_length=1, max_length=2000)

    @model_validator(mode='after')
    def protection(self):
        if self.target in ('HOLD', 'FLAT'):
            if self.quantity != 0 or self.stop_loss is not None or self.take_profit is not None:
                raise ValueError('No size or bracket on HOLD/FLAT')
        elif self.quantity <= 0 or self.stop_loss is None or self.take_profit is None:
            raise ValueError('Directional research requires exact quantity and both protective prices')
        return self


class Report(Boundary):
    role: Literal['bull', 'bear', 'research_manager', 'trader', 'aggressive', 'conservative', 'neutral', 'portfolio_manager']
    text: str = Field(min_length=1, max_length=12000)


class Advice(Boundary):
    adapter: Literal['fc666-tradingagents-shadow-v1']
    upstream_commit: Literal['c4544913a14ed3df5f1ed7b9e8483270ce20682a']
    snapshot_sha256: str = Field(pattern=r'^[a-f0-9]{64}$')
    instrument: str = Field(min_length=1, max_length=32)
    quote_source: str = Field(min_length=1, max_length=128)
    produced_at: datetime
    valid_until: datetime
    rating: Literal['Buy', 'Overweight', 'Hold', 'Underweight', 'Sell', 'REVIEW']
    reports: tuple[Report, ...] = Field(min_length=8, max_length=8)
    candidate: Candidate
    model: str = Field(min_length=1, max_length=128)
    model_calls: Literal[9]

    @model_validator(mode='after')
    def complete(self):
        if tuple(row.role for row in self.reports) != ROLES:
            raise ValueError('Complete ordered upstream role evidence required')
        if self.valid_until <= self.produced_at:
            raise ValueError('Ordered advice lifetime required')
        return self


def freeze(view, *, now, news):
    now = aware(now)
    config = Config.model_validate(view['config'])
    quote = None if view['last_quote'] is None else Quote.model_validate(view['last_quote'])
    gate = ('ENTRY_HALTED' if view['status'] == 'STOPPED' or view['entry_halted'] else
            'QUOTE_UNAVAILABLE' if quote is None else
            'QUOTE_STALE' if not 0 <= (now - quote.at).total_seconds() <= config.quote_max_age_seconds else
            entry_gate(config, now, news))
    news_summary = None
    if news is not None:
        relevant = sorted((e for e in news.events if config.instrument in e.instruments or '*' in e.instruments),
                          key=lambda e: abs((e.scheduled_at - now).total_seconds()))
        news_summary = {**news.model_dump(mode='json', exclude={'events'}),
                        'events': [{**e.model_dump(mode='json', exclude={'instruments'}),
                                    'instruments': [name for name in e.instruments if name in (config.instrument, '*')]}
                                   for e in relevant[:20]],
                        'events_total': len(news.events), 'relevant_events': len(relevant),
                        'events_truncated': len(relevant) > 20, 'sha256': digest(news.model_dump(mode='json'))}
    result = {'version': 'fc666-research-snapshot-v1', 'account_id': view['account_id'], 'revision': view['revision'],
              'at': now.isoformat(), 'valid_until': (now + timedelta(minutes=5)).isoformat(),
              'config': config.model_dump(mode='json'), 'status': view['status'], 'entry_halted': view['entry_halted'],
              'balance': view['balance'], 'position': view['position'], 'pending': view['pending'],
              'quote': None if quote is None else quote.model_dump(mode='json'),
              'recent_quotes': view['recent_quotes'][-20:], 'sample_type': 'OBSERVED_QUOTES_NOT_CANDLES',
              'news': news_summary, 'entry_gate': gate,
              'unavailable': ['closed_candles', 'ohlcv_indicators', 'social_sentiment', 'company_fundamentals'],
              'trading_enabled': False}
    return {'snapshot': result, 'sha256': digest(result)}


def assess(snapshot, advice, view, *, now, news):
    """Read-only checks against current account/quote/news, never calls Store.submit."""
    now = aware(now)
    advice = Advice.model_validate(advice)
    data = snapshot['snapshot']
    config = Config.model_validate(view['config'])
    reason = 'SHADOW_READY'
    if digest(data) != snapshot['sha256'] or advice.snapshot_sha256 != snapshot['sha256']:
        reason = 'CONTEXT_MISMATCH'
    elif advice.instrument != config.instrument or advice.quote_source != config.quote_source:
        reason = 'MARKET_IDENTITY_MISMATCH'
    elif view['account_id'] != data['account_id'] or view['revision'] != data['revision'] or config.model_dump(mode='json') != data['config']:
        reason = 'ACCOUNT_CHANGED'
    elif advice.produced_at > now or advice.produced_at < datetime.fromisoformat(data['at']):
        reason = 'ADVICE_TIME_INVALID'
    elif not now < advice.valid_until <= datetime.fromisoformat(data['valid_until']):
        reason = 'ADVICE_EXPIRED'
    elif advice.rating == 'REVIEW':
        reason = 'REVIEW_REQUIRED'
    elif advice.candidate.target == 'HOLD':
        reason = 'HOLD'
    elif advice.candidate.target == 'FLAT':
        if view['position'] is None:
            reason = 'NO_POSITION'
        elif view['pending'] is not None:
            reason = 'ACCOUNT_BUSY'
        elif view['last_quote'] is None:
            reason = 'QUOTE_UNAVAILABLE'
        else:
            quote = Quote.model_validate(view['last_quote'])
            reason = ('MARKET_IDENTITY_MISMATCH' if quote.instrument != config.instrument or quote.source != config.quote_source else
                      'QUOTE_STALE' if not 0 <= (now - quote.at).total_seconds() <= config.quote_max_age_seconds else 'SHADOW_REDUCE_ONLY')
    elif view['status'] == 'STOPPED' or view['entry_halted']:
        reason = 'ENTRY_HALTED'
    elif view['position'] is not None or view['pending'] is not None:
        reason = 'ACCOUNT_BUSY'
    elif view['last_quote'] is None:
        reason = 'QUOTE_UNAVAILABLE'
    else:
        quote = Quote.model_validate(view['last_quote'])
        if quote.instrument != config.instrument or quote.source != config.quote_source:
            reason = 'MARKET_IDENTITY_MISMATCH'
        elif not 0 <= (now - quote.at).total_seconds() <= config.quote_max_age_seconds:
            reason = 'QUOTE_STALE'
        else:
            reason = entry_gate(config, now, news)
            if reason == 'CLEAR':
                with localcontext() as precision:
                    precision.prec = 60
                    p = advice.candidate
                    price = rounded(quote.ask + config.slippage if p.target == 'LONG' else quote.bid - config.slippage,
                                    config.tick_size, ROUND_CEILING if p.target == 'LONG' else ROUND_FLOOR)
                    mark = quote.bid if p.target == 'LONG' else quote.ask
                    if p.quantity > config.max_quantity or p.quantity % config.quantity_step or p.stop_loss % config.tick_size or p.take_profit % config.tick_size:
                        reason = 'SIZE_OR_PRICE_LIMIT'
                    elif not (p.stop_loss < min(price, mark) <= max(price, mark) < p.take_profit if p.target == 'LONG'
                              else p.take_profit < min(price, mark) <= max(price, mark) < p.stop_loss):
                        reason = 'BRACKET_INVALID_AT_QUOTE'
                    elif price <= 0 or price * p.quantity > config.max_order_quote or price * p.quantity * (1 + config.fee_rate) > Decimal(view['balance']):
                        reason = 'COLLATERAL_OR_ORDER_LIMIT'
                    else:
                        reason = 'SHADOW_READY'
    return {'reason': reason, 'validation_passed': reason in ('HOLD', 'SHADOW_READY', 'SHADOW_REDUCE_ONLY'),
            'execution_enabled': False, 'trading_enabled': False, 'checked_at': now.isoformat()}
