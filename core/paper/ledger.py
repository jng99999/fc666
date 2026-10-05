"""Causal, deterministic Spot paper ledger. No exchange calls or live orders."""
from decimal import Decimal, localcontext, ROUND_CEILING, ROUND_FLOOR
from pydantic import BaseModel, ConfigDict, Field, field_validator
from core.backtest.spot import BacktestConfig, digest, rounded
from core.indicators.engine import IndicatorEngine
from core.strategy.registry import resolve
from core.strategy.contracts import StrategyContext

VERSION = 'spot-paper-next-open-v1'


class RiskLimits(BaseModel):
    model_config = ConfigDict(frozen=True, extra='forbid')
    max_order_quote: Decimal = Field(default=Decimal('5000'), gt=0, le=1000000000, max_digits=38, decimal_places=18)
    max_position_quote: Decimal = Field(default=Decimal('10000'), gt=0, le=1000000000, max_digits=38, decimal_places=18)
    max_drawdown: Decimal = Field(default=Decimal('.2'), gt=0, le=1, max_digits=38, decimal_places=18)

    @field_validator('max_order_quote', 'max_position_quote', 'max_drawdown', mode='before')
    @classmethod
    def exact(cls, value):
        if isinstance(value, (float, bool)):
            raise ValueError('Use decimal strings for paper risk limits')
        return value


def evaluate(bars, instrument, config: BacktestConfig, limits: RiskLimits, *, strategy_id, parameters, snapshot_sha256, halt_at=None, ended=False):
    """Input is only the reached prefix. Entries are long/flat transitions, not rebalancing."""
    strategy, validated = resolve(strategy_id).create(parameters)
    if strategy_id == 'ema_long_flat_v1' and validated.period != config.period:
        raise ValueError('EMA period must match config.period')
    if halt_at is not None and (isinstance(halt_at, bool) or not isinstance(halt_at, int) or not 0 <= halt_at <= len(bars)):
        raise ValueError('Invalid manual halt cursor')
    with localcontext() as ctx:
        ctx.prec = 60
        cash = config.initial_cash
        quantity = cost = fees = realized = Decimal(0)
        peak = config.initial_cash
        drawdown = Decimal(0)
        risk_halted = False
        pending = None
        orders, fills, equity, signals, risk_events = [], [], [], [], []
        indicator = IndicatorEngine(period=config.period)

        def mark(value, when):
            nonlocal peak, drawdown, risk_halted
            peak = max(peak, value)
            current = (peak - value) / peak
            drawdown = max(drawdown, current)
            if not risk_halted and current >= limits.max_drawdown:
                risk_halted = True
                risk_events.append({'reason': 'MAX_DRAWDOWN', 'available_at': when.isoformat(), 'drawdown': str(current)})

        for index, bar in enumerate(bars):
            # Open is known before a fill; no use of this bar's close/high/low/volume.
            mark(cash + quantity * bar.open, bar.open_time)
            if pending is not None:
                side = 'BUY' if pending.target == 'LONG' and quantity == 0 else 'SELL' if pending.target == 'FLAT' and quantity > 0 else None
                if side:
                    if index == 0 or pending.available_at > bar.open_time:
                        raise ValueError('Decision is unavailable at execution')
                    order = {'side': side, 'decision_at': pending.available_at.isoformat(), 'execution_at': bar.open_time.isoformat(), 'simulated': True}
                    order['order_id'] = digest({'snapshot_sha256': snapshot_sha256, 'engine': VERSION, 'order': order})
                    price = rounded(bar.open * (1 + config.slippage_rate if side == 'BUY' else 1 - config.slippage_rate), instrument.tick_size, ROUND_CEILING if side == 'BUY' else ROUND_FLOOR)
                    manual_halted = halt_at is not None and index >= halt_at
                    reason = None
                    if side == 'BUY' and (manual_halted or risk_halted):
                        reason = 'MANUAL_HALT' if manual_halted else 'MAX_DRAWDOWN'
                    elif price <= 0:
                        reason = 'NONPOSITIVE_PRICE'
                    else:
                        wanted = cash * config.allocation / (price * (1 + config.fee_rate)) if side == 'BUY' else quantity
                        size = rounded(min(wanted, bars[index-1].volume * config.participation), instrument.quantity_step, ROUND_FLOOR)
                        wanted_step = rounded(wanted, instrument.quantity_step, ROUND_FLOOR)
                        notional = size * price
                        if size <= 0 or size < instrument.min_quantity or notional < instrument.min_notional:
                            reason = 'MARKET_RULES_OR_CAPACITY'
                        elif side == 'BUY' and notional > limits.max_order_quote:
                            reason = 'MAX_ORDER_QUOTE'
                        elif side == 'BUY' and (quantity + size) * price > limits.max_position_quote:
                            reason = 'MAX_POSITION_QUOTE'
                    if reason is not None:
                        orders.append({**order, 'status': 'REJECTED', 'reason': reason})
                    else:
                        fee = notional * config.fee_rate
                        fill_pnl = Decimal(0)
                        if side == 'BUY':
                            if notional + fee > cash:
                                raise ArithmeticError('Insufficient paper cash')
                            cash -= notional + fee
                            quantity += size
                            cost += notional + fee
                        else:
                            basis = cost * size / quantity
                            cash += notional - fee
                            quantity -= size
                            cost -= basis
                            fill_pnl = notional - fee - basis
                            realized += fill_pnl
                        fees += fee
                        if cash < 0 or quantity < 0 or cost < 0:
                            raise ArithmeticError('Negative paper cash, inventory or cost')
                        fills.append({**order, 'quantity': str(size), 'price': str(price), 'fee': str(fee), 'realized_pnl': str(fill_pnl), 'reference_open': str(bar.open), 'cash_after': str(cash), 'quantity_after': str(quantity)})
                        orders.append({**order, 'status': 'FILLED' if size >= wanted_step else 'PARTIAL_CANCELLED', 'quantity': str(size)})
            row = indicator.push(bar)
            pending = strategy.decide(StrategyContext(bar.close_time, bar.close, row['values']['ema']))
            if pending is not None:
                signals.append({'target': pending.target, 'available_at': pending.available_at.isoformat()})
            value = cash + quantity * bar.close
            mark(value, bar.close_time)
            unrealized = quantity * bar.close - cost
            if abs(value - config.initial_cash - realized - unrealized) > Decimal('1e-40'):
                raise ArithmeticError('Paper PnL conservation violated')
            equity.append({'as_of': bar.close_time.isoformat(), 'cash': str(cash), 'quantity': str(quantity), 'equity': str(value)})
        value = cash + quantity * bars[-1].close if bars else cash
        unrealized = quantity * bars[-1].close - cost if bars else Decimal(0)
        return {'account': {'cash': str(cash), 'quantity': str(quantity), 'cost_basis': str(cost), 'equity': str(value), 'realized_pnl': str(realized), 'unrealized_pnl': str(unrealized), 'net_pnl': str(value-config.initial_cash), 'fees': str(fees), 'peak_equity': str(peak), 'max_drawdown': str(drawdown)},
                'risk': {'entry_halted': risk_halted or halt_at is not None, 'drawdown_halted': risk_halted, 'manual_halt_at': halt_at, 'events': risk_events},
                'orders': orders, 'fills': fills, 'equity': equity, 'signals': signals,
                'pending': None if pending is None else {'target': pending.target, 'available_at': pending.available_at.isoformat(), 'status': 'NO_NEXT_BAR' if ended else 'AWAIT_NEXT_REACHED_BAR'}}
