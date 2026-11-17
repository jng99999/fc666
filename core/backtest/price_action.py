"""Frozen rolling-500 PAPER rules with explicitly modeled OHLC execution costs.

Trade/quote OHLC is not historical bid/ask or tick evidence. This module has no
broker, account-store, credential or live-order dependency.
"""
from datetime import datetime
from decimal import Decimal, localcontext, ROUND_CEILING, ROUND_FLOOR
from functools import lru_cache
import json
from typing import Literal, Sequence

from pydantic import Field, model_validator

from core.backtest.spot import digest, rounded
from core.paper.directional import Boundary, Config, News, aware, entry_gate, pending_entry_gate
from core.paper.price_action_rules import RuleBar, Rule, VERSION as RULE_VERSION, analyze as rule_analyze

VERSION = 'price-action-ohlc-next-open-v1'
D = Decimal
NewsPolicy = Literal['REQUIRE_HISTORICAL', 'UNCOVERED_RESEARCH']


class PriceActionBacktestConfig(Boundary):
    rule: Rule
    initial_cash: Decimal = Field(gt=0, le=1000000000, max_digits=38, decimal_places=18)
    quantity: Decimal = Field(gt=0, le=1000000, max_digits=38, decimal_places=18)
    fee_rate: Decimal = Field(ge=0, le=D('.1'), max_digits=38, decimal_places=18)
    spread: Decimal = Field(ge=0, le=1000000, max_digits=38, decimal_places=18)
    slippage: Decimal = Field(ge=0, le=1000000, max_digits=38, decimal_places=18)
    stop_loss_percent: Decimal = Field(ge=D('.0001'), le=D('.5'), max_digits=38, decimal_places=18)
    take_profit_percent: Decimal = Field(ge=D('.0001'), lt=1, max_digits=38, decimal_places=18)
    tick_size: Decimal = Field(gt=0, le=1000000, max_digits=38, decimal_places=18)
    quantity_step: Decimal = Field(gt=0, le=1000000, max_digits=38, decimal_places=18)
    max_quantity: Decimal = Field(default=D(1000000), gt=0, le=1000000)
    max_order_quote: Decimal = Field(default=D(1000000000), gt=0, le=1000000000)
    news_sources: tuple[str, ...] = Field(default=(), max_length=20)
    news_pre_seconds: int = Field(default=1800, ge=0, le=86400, strict=True)
    news_post_seconds: int = Field(default=1800, ge=0, le=86400, strict=True)
    news_max_age_seconds: int = Field(default=120, ge=1, le=3600, strict=True)

    @model_validator(mode='after')
    def limits(self):
        if self.quantity > self.max_quantity or self.quantity % self.quantity_step:
            raise ValueError('Fixed quantity must meet its explicit maximum and step')
        if any(not name or name != name.strip() or len(name) > 128 for name in self.news_sources):
            raise ValueError('Explicit bounded historical news source names required')
        return self


def _json(value):
    if isinstance(value, Decimal):
        return format(value, 'f')
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, dict):
        return {key: _json(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json(item) for item in value]
    return value


@lru_cache(maxsize=12)
def _signal_series(bars, instrument, source, timeframe, rule, data_sha256):
    """Cost-independent JSON bytes; callers cannot mutate cached signal evidence."""
    result = []
    for index, row in enumerate(bars):
        report = rule_analyze(bars[max(0, index - 499):index + 1], instrument=instrument,
                              source=source, timeframe=timeframe, now=row.close_time, rule=rule)
        evidence = report['evidence']
        compact = {key: report[key] for key in ('rule_version', 'state', 'target', 'available_at', 'reason', 'samples', 'required')}
        compact['evidence_sha256'] = digest(evidence)
        if report['target'] is not None:
            compact['indicators'] = {key: evidence.get(key) for key in ('ema20', 'ema60', 'rsi14', 'atr14')}
        result.append(json.dumps(compact, sort_keys=True, separators=(',', ':')))
    return tuple(result)


def clear_signal_cache():
    """Clear only in-memory pure research memoization, useful for timing checks."""
    _signal_series.cache_clear()


def simulate(bars: Sequence[RuleBar], config: PriceActionBacktestConfig, *, instrument: str,
             source: str, timeframe: str, as_of: datetime, news_snapshots: Sequence[News] = (),
             news_policy: NewsPolicy = 'REQUIRE_HISTORICAL', checkpoint=None) -> dict:
    """One independently funded synthetic position; never an actual market fill.

    Signals exactly reuse the live rule's rolling 500-bar reconstruction. Missing
    bars cancel pending execution; no prices/fills are invented inside a gap.
    """
    as_of = aware(as_of)
    config = PriceActionBacktestConfig.model_validate(config.model_dump() if isinstance(config, PriceActionBacktestConfig) else config)
    if news_policy not in ('REQUIRE_HISTORICAL', 'UNCOVERED_RESEARCH'):
        raise ValueError('Explicit historical-news policy required')
    if not isinstance(bars, (list, tuple)) or not 2 <= len(bars) <= 5000:
        raise ValueError('Require 2..5000 explicit closed bars')
    checked = tuple(RuleBar.model_validate(row.model_dump() if isinstance(row, RuleBar) else row) for row in bars)
    if any((row.instrument, row.source, row.timeframe) != (instrument, source, timeframe) or row.close_time > as_of for row in checked):
        raise ValueError('Matching product/source/timeframe and nonfuture closed history required')
    if any(left.open_time >= right.open_time or left.close_time > right.open_time for left, right in zip(checked, checked[1:])):
        raise ValueError('Duplicate, overlapping or unordered history rejected')
    snapshots = tuple(News.model_validate(row.model_dump() if isinstance(row, News) else row) for row in news_snapshots)
    if snapshots and not config.news_sources:
        raise ValueError('Authorize historical news sources explicitly before using snapshots')
    identities = {}
    for row in snapshots:
        if row.observed_at > as_of:
            raise ValueError('Future-observed historical news rejected')
        key = (row.source, row.observed_at)
        encoded = digest(row.model_dump(mode='json'))
        if key in identities and identities[key] != encoded:
            raise ValueError('Conflicting historical news snapshot identity')
        identities[key] = encoded
    snapshots = tuple(sorted(snapshots, key=lambda row: (row.observed_at, row.source)))
    dataset = [row.model_dump(mode='json') for row in checked]
    manifest = {'engine_version': VERSION, 'rule_version': RULE_VERSION, 'rule': config.rule,
        'instrument': instrument, 'source': source, 'timeframe': timeframe,
        'config': config.model_dump(mode='json'), 'news_policy': news_policy,
        'data_sha256': digest(dataset), 'news_sha256': digest([row.model_dump(mode='json') for row in snapshots]),
        'bars': len(checked), 'start': checked[0].open_time.isoformat(), 'end': checked[-1].close_time.isoformat(),
        'as_of': as_of.isoformat(), 'rule_history': 'EXACT_PUBLIC_ANALYZE_ROLLING_500',
        'price_reference': 'PROVIDER_OHLC_AS_MODEL_CENTER_NOT_OBSERVED_MID',
        'assumptions': ['OHLC_MODEL_NOT_HISTORICAL_BID_ASK_OR_TICKS; no actual fill proof',
            'Closed-bar decision, next contiguous bar open model fill; never same-close execution',
            'Explicit constant full spread in absolute price units; half each side, plus adverse per-side absolute slippage and tick rounding',
            'SL/TP frozen from modeled decision-close executable base; next open must still fit both brackets',
            'Opening protection evaluated first; otherwise both intrabar thresholds touched means stop first',
            'Intrabar event time is unknown; execution window covers the observed bar and accounting timestamp is its close',
            'Single full-collateral synthetic 1x position; no spot-short capability, borrow, funding, leverage or liquidation model',
            'Opposite signal queues exit only; a subsequent independent signal is required to open the other direction',
            'Missing bars cancel pending intent; existing protection uses only the next observed open/range, never an invented missing-path fill',
            'Close equity marks use assumed executable side of OHLC close, excluding future exit slippage/fee; drawdown is close-only',
            'No terminal forced liquidation, parameter fitting, real liquidity/volume capacity or live eligibility']}
    if checkpoint is not None:
        checkpoint(0, len(checked))
    reports = _signal_series(checked, instrument, source, timeframe, config.rule, manifest['data_sha256'])
    with localcontext() as precision:
        precision.prec = 60
        return _simulate(checked, config, snapshots, news_policy, manifest, reports, checkpoint)


def _simulate(bars, config, snapshots, news_policy, manifest, reports, checkpoint):
    news_config = Config(mode='RESEARCH_PAPER', instrument=manifest['instrument'], quote_source=manifest['source'],
        news_sources=config.news_sources or ('HISTORICAL_NEWS_NOT_CONFIGURED',), initial_cash=config.initial_cash,
        tick_size=config.tick_size, quantity_step=config.quantity_step, max_quantity=config.max_quantity,
        max_order_quote=config.max_order_quote, fee_rate=config.fee_rate, slippage=config.slippage,
        quote_max_age_seconds=60, decision_ttl_seconds=60, news_max_age_seconds=config.news_max_age_seconds,
        news_pre_seconds=config.news_pre_seconds, news_post_seconds=config.news_post_seconds)
    coverage = {}
    def news_gate(at, accepted_at=None):
        eligible = [row for row in snapshots if row.observed_at <= at and row.source in config.news_sources]
        news = eligible[-1] if eligible else None
        status = entry_gate(news_config, at, news) if accepted_at is None else pending_entry_gate(news_config, accepted_at, at, news)
        coverage[at.isoformat()] = status
        # Known authorized high events remain forbidden even in the explicitly
        # uncovered diagnostic mode, including an embargo crossed between checks.
        start = at if accepted_at is None else accepted_at
        for snapshot in eligible:
            for event in snapshot.events:
                if (event.impact == 'HIGH' and (manifest['instrument'] in event.instruments or '*' in event.instruments)
                        and event.known_at <= at and event.scheduled_at.timestamp() - config.news_pre_seconds <= at.timestamp()
                        and event.scheduled_at.timestamp() + config.news_post_seconds >= start.timestamp()):
                    return ('HIGH_IMPACT_EVENT' if accepted_at is None else 'HIGH_IMPACT_EVENT_CROSSED'), status
        if status == 'CLEAR':
            return 'CLEAR', status
        return ('CLEAR' if news_policy == 'UNCOVERED_RESEARCH' else status), status

    balance = config.initial_cash
    fees = gross = realized = spread_cost = slippage_cost = tick_cost = D(0)
    peak, max_drawdown = balance, D(0)
    position = pending = None
    fills, orders, signals, equity, trades, gaps = [], [], [], [], [], []
    ambiguous = 0
    half_spread = config.spread / 2

    def modeled_price(mid, buying):
        raw = mid + (half_spread + config.slippage) * (1 if buying else -1)
        price = rounded(raw, config.tick_size, ROUND_CEILING if buying else ROUND_FLOOR)
        if price <= 0:
            raise ValueError('Cost assumptions exceed a positive executable model price')
        return price, abs(price - raw)

    def fill(action, direction, mid, at, reason, decision_at, window=None):
        nonlocal fees, spread_cost, slippage_cost, tick_cost
        buying = (action == 'OPEN') == (direction == 'LONG')
        price, rounding = modeled_price(mid, buying)
        fee = price * config.quantity * config.fee_rate
        row = {'action': action, 'direction': direction, 'quantity': config.quantity, 'price': price, 'fee': fee,
            'reason': reason, 'decision_at': decision_at, 'execution_at': at, 'reference_ohlc_price': mid,
            'spread_cost': half_spread * config.quantity, 'slippage_cost': config.slippage * config.quantity,
            'tick_rounding_cost': rounding * config.quantity, 'simulated': True, 'model_fill': True,
            'execution_model': 'OHLC_WITH_ASSUMED_COSTS_NOT_TICK', 'execution_window': window}
        fees += fee
        spread_cost += row['spread_cost']; slippage_cost += row['slippage_cost']; tick_cost += row['tick_rounding_cost']
        fills.append(row)
        return row

    def close(mid, at, reason, decision_at, window=None):
        nonlocal position, balance, gross, realized
        row = fill('CLOSE', position['direction'], mid, at, reason, decision_at, window)
        pnl = (row['price'] - position['entry']) * config.quantity * (1 if position['direction'] == 'LONG' else -1)
        net = pnl - position['entry_fee'] - row['fee']
        balance += pnl - row['fee']; gross += pnl; realized += net
        row['gross_pnl'] = pnl
        trades.append({'direction': position['direction'], 'entry_at': position['entry_at'], 'exit_at': at,
            'entry': position['entry'], 'exit': row['price'], 'quantity': config.quantity,
            'gross_pnl': pnl, 'fees': position['entry_fee'] + row['fee'], 'net_pnl': net,
            'reason': reason, 'model_fill': True})
        position = None

    def protection(bar, *, opening):
        nonlocal ambiguous
        if position is None:
            return False
        long = position['direction'] == 'LONG'
        stop, target = position['stop_loss'], position['take_profit']
        offset = -half_spread if long else half_spread
        if opening:
            mark = bar.open + offset
            stopped, won = (mark <= stop, mark >= target) if long else (mark >= stop, mark <= target)
        else:
            stopped, won = (bar.low + offset <= stop, bar.high + offset >= target) if long else (bar.high + offset >= stop, bar.low + offset <= target)
        if not stopped and not won:
            return False
        if stopped and won:
            ambiguous += 1
        if stopped:
            reference = bar.open if opening else stop - offset
        else:
            reference = target - offset  # Never grant a better-than-target favorable opening gap.
        close(reference, bar.open_time if opening else bar.close_time, 'STOP_LOSS' if stopped else 'TAKE_PROFIT',
              position['signal_at'], None if opening else {'from': bar.open_time, 'to': bar.close_time, 'order': 'UNKNOWN_STOP_FIRST'})
        return True

    for index, bar in enumerate(bars):
        news_gate(bar.open_time)
        gap = index > 0 and bars[index - 1].close_time != bar.open_time
        if gap:
            gaps.append({'from': bars[index - 1].close_time, 'to': bar.open_time, 'open_position_path_unknown': position is not None})
            if pending is not None:
                orders.append({**pending, 'status': 'CANCELLED', 'reason': 'MISSING_NEXT_BAR_NO_FILL', 'at': bar.open_time})
                pending = None
        protected = protection(bar, opening=True)
        if protected:
            pending = None
        if pending is not None and not protected:
            intent, pending = pending, None
            if intent['action'] == 'CLOSE':
                if position is not None:
                    close(bar.open, bar.open_time, 'SIGNAL_EXIT', intent['signal_at'])
                    orders.append({**intent, 'status': 'FILLED', 'at': bar.open_time})
            elif position is None:
                gate, news_status = news_gate(bar.open_time, intent['signal_at'])
                price, _ = modeled_price(bar.open, intent['direction'] == 'LONG')
                mark = bar.open - half_spread if intent['direction'] == 'LONG' else bar.open + half_spread
                stop, target = intent['stop_loss'], intent['take_profit']
                correct = stop < price < target if intent['direction'] == 'LONG' else target < price < stop
                untriggered = stop < mark < target if intent['direction'] == 'LONG' else target < mark < stop
                if gate == 'CLEAR' and not correct:
                    gate = 'BRACKET_INVALID_AT_FILL'
                if gate == 'CLEAR' and not untriggered:
                    gate = 'BRACKET_ALREADY_TRIGGERED'
                notional, fee = price * config.quantity, price * config.quantity * config.fee_rate
                if gate == 'CLEAR' and (notional > config.max_order_quote or notional + fee > balance):
                    gate = 'COLLATERAL_OR_ORDER_LIMIT'
                if gate == 'CLEAR':
                    row = fill('OPEN', intent['direction'], bar.open, bar.open_time, 'SIGNAL_ENTRY', intent['signal_at'])
                    balance -= row['fee']
                    position = {'direction': intent['direction'], 'entry': row['price'], 'entry_fee': row['fee'],
                        'entry_at': bar.open_time, 'signal_at': intent['signal_at'], 'quantity': config.quantity,
                        'reserved_collateral': notional, 'stop_loss': stop, 'take_profit': target}
                orders.append({**intent, 'status': 'FILLED' if gate == 'CLEAR' else 'REJECTED',
                               'reason': gate, 'news_status': news_status, 'at': bar.open_time})
        protection(bar, opening=False)
        mark = bar.close - half_spread if position and position['direction'] == 'LONG' else bar.close + half_spread
        unrealized = (mark - position['entry']) * config.quantity * (1 if position['direction'] == 'LONG' else -1) if position else D(0)
        value = balance + unrealized
        peak = max(peak, value); max_drawdown = max(max_drawdown, (peak - value) / peak)
        equity.append({'as_of': bar.close_time, 'balance': balance, 'equity': value,
            'quantity': config.quantity if position else D(0), 'direction': position['direction'] if position else None})
        gate, news_status = news_gate(bar.close_time)
        report = json.loads(reports[index])
        if report['state'] == 'UNAVAILABLE':
            raise ValueError('Frozen rule rejected validated history: ' + report['reason'])
        if report['target'] is not None:
            signal = {**report, 'at': bar.close_time, 'news_status': news_status}
            direction = report['target']
            current = position['direction'] if position else None
            if current == direction:
                signal['status'] = 'SAME_DIRECTION'
            elif current is not None:
                pending = {'action': 'CLOSE', 'direction': current, 'signal_at': bar.close_time}
                signal['status'] = 'EXIT_FIRST_NEW_SIGNAL_REQUIRED'
            elif balance <= 0:
                signal['status'] = 'ENTRY_HALTED_INSOLVENT'
            elif gate != 'CLEAR':
                signal['status'] = gate
            else:
                base, _ = modeled_price(bar.close, direction == 'LONG')
                long = direction == 'LONG'
                stop = rounded(base * (1 - config.stop_loss_percent if long else 1 + config.stop_loss_percent), config.tick_size, ROUND_FLOOR if long else ROUND_CEILING)
                target = rounded(base * (1 + config.take_profit_percent if long else 1 - config.take_profit_percent), config.tick_size, ROUND_CEILING if long else ROUND_FLOOR)
                if min(stop, target) <= 0:
                    signal['status'] = 'SIZE_OR_PRICE_LIMIT'
                elif base * config.quantity > config.max_order_quote or base * config.quantity * (1 + config.fee_rate) > balance:
                    signal['status'] = 'COLLATERAL_OR_ORDER_LIMIT'
                else:
                    pending = {'action': 'OPEN', 'direction': direction, 'signal_at': bar.close_time,
                               'decision_base': base, 'stop_loss': stop, 'take_profit': target}
                    signal['status'] = 'QUEUED_NEXT_OPEN_MODEL'
            signals.append(signal)
        if checkpoint is not None and index % 25 == 0:
            checkpoint(index + 1, len(bars))

    final = equity[-1]['equity']
    unrealized_net = final - config.initial_cash - realized
    if abs(balance - config.initial_cash - gross + fees) > D('1e-40'):
        raise ArithmeticError('Synthetic PAPER balance conservation failed')
    wins = [row['net_pnl'] for row in trades if row['net_pnl'] > 0]
    losses = [-row['net_pnl'] for row in trades if row['net_pnl'] < 0]
    statuses = {}
    for status in coverage.values():
        statuses[status] = statuses.get(status, 0) + 1
    covered = sum(status in ('CLEAR', 'HIGH_IMPACT_EVENT', 'HIGH_IMPACT_EVENT_CROSSED') for status in coverage.values())
    news_coverage = {'policy': news_policy, 'snapshots': len(snapshots), 'checks': len(coverage), 'covered_checks': covered,
        'uncovered_checks': len(coverage) - covered, 'statuses': statuses, 'complete': bool(snapshots) and covered == len(coverage),
        'status': 'NOT_COVERED' if not snapshots else 'COVERAGE_CHECKS_PASSED_UNVERIFIED_PROVENANCE' if covered == len(coverage) else 'PARTIAL_OR_UNCOVERED',
        'news_validated': False, 'provenance_independently_verified': False}
    metrics = {'initial_cash': config.initial_cash, 'final_equity': final, 'net_pnl': final - config.initial_cash,
        'realized_pnl': realized, 'unrealized_pnl': unrealized_net, 'gross_pnl': gross, 'fees': fees,
        'spread_cost': spread_cost, 'slippage_cost': slippage_cost, 'tick_rounding_cost': tick_cost,
        'total_cost': fees + spread_cost + slippage_cost + tick_cost, 'total_return': final / config.initial_cash - 1,
        'max_drawdown': max_drawdown, 'fill_count': len(fills), 'closed_trade_count': len(trades),
        'win_rate': D(len(wins)) / len(trades) if trades else None,
        'profit_factor': sum(wins, D(0)) / sum(losses, D(0)) if losses else None,
        'open_quantity': config.quantity if position else D(0), 'insolvent': final <= 0,
        'ambiguous_stop_first': ambiguous, 'gap_count': len(gaps)}
    if checkpoint is not None:
        checkpoint(len(bars), len(bars))
    return _json({'run_id': digest(manifest), 'manifest': manifest, 'dataset': [row.model_dump(mode='json') for row in bars],
        'metrics': metrics, 'equity': equity, 'signals': signals, 'orders': orders, 'fills': fills, 'trades': trades,
        'gaps': gaps, 'open_position': position, 'pending_final_signal': {**pending, 'status': 'NO_NEXT_BAR'} if pending else None,
        'news_coverage': news_coverage, 'news_validated': False, 'trading_enabled': False, 'live_eligible': False,
        'warnings': ['All fills are OHLC model results, not historical bid/ask or tick executions.',
            'Missing historical news is never validated; uncovered diagnostic returns do not establish production suitability.',
            'No liquidity capacity, borrow/funding or liquidation model; synthetic short research is not spot short capability.']})
