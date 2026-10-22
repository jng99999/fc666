"""Paired quote, next-observed-open XAU/USD long/flat research. No account mutations."""
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation, ROUND_FLOOR, localcontext
import hashlib
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, field_validator

from core.gold.history import HOUR, aggregate, sha
from core.strategy.contracts import StrategyContext, TargetSignal
from core.strategy.registry import lookback, resolve
from core.strategy.research import SeededEma

VERSION = 'gold-paired-next-open-v3'
_ROOT = Path(__file__).resolve().parents[1]
_FILES = ['gold/backtest.py', 'gold/history.py', 'strategy/contracts.py', 'strategy/registry.py', 'strategy/research.py']
CODE_SHA256 = {name: hashlib.sha256((_ROOT/name).read_bytes()).hexdigest() for name in _FILES}


class Costs(BaseModel):
    model_config = ConfigDict(extra='forbid', frozen=True)
    initial_cash: Decimal = Field(gt=0, le=1000000)
    allocation: Decimal = Field(gt=0, le=Decimal('.5'))
    commission: Decimal = Field(ge=0, le=10)
    slippage: Decimal = Field(ge=0, le=10)
    spread_multiplier: Decimal = Field(ge=0, le=5)

    @field_validator('initial_cash', 'allocation', 'commission', 'slippage', 'spread_multiplier', mode='before')
    @classmethod
    def exact(cls, value):
        if not isinstance(value, (str, int, Decimal)) or isinstance(value, bool) or len(str(value)) > 32:
            raise ValueError('Use bounded decimal strings for gold research costs')
        try:
            number = Decimal(value)
        except InvalidOperation as error:
            raise ValueError('Invalid decimal research cost') from error
        if not number.is_finite() or number.as_tuple().exponent < -8:
            raise ValueError('Gold research costs require finite values with at most eight decimal places')
        return value


def iso(moment):
    return datetime.fromtimestamp(moment/1000, timezone.utc).isoformat()


def simulate(bars, hours, start, end, costs, variant, warmup=100, opens=None):
    opens = opens if opens is not None else {bar.start: (bar.first, bar.bid_open, bar.ask_open) for bar in bars}
    benchmark = variant['strategy'] == 'buy_hold_reference'
    definition = resolve('ema_long_flat_v1' if benchmark else variant['strategy'])
    strategy, parameters = definition.create(variant['parameters'])
    selected = [i for i, bar in enumerate(bars) if start <= bar.start and bar.start+hours*HOUR <= end]
    if not selected:
        raise ValueError('No complete historical bars in requested gold window')
    first, final = max(selected[0], warmup), selected[-1]
    if first >= final:
        raise ValueError('Insufficient complete gold history after common warmup')
    effective_start = max(start, bars[first-1].start+hours*HOUR)
    prefix = max(0, first-warmup)
    ema = SeededEma(getattr(parameters, 'period', 20))
    cash, quantity, basis = costs.initial_cash, Decimal(0), Decimal(0)
    realized = fees = spread_cost = slippage_cost = Decimal(0)
    peak = cash
    drawdown = Decimal(0)
    pending = None
    fills, closed, curve = [], [], []
    expired = holding = 0
    stride = max(1, (final-first+1)//240)
    with localcontext() as precision:
        precision.prec = 60
        for index in range(prefix, final+1):
            bar = bars[index]
            if index >= first:
                if pending is not None:
                    available = int(pending.available_at.timestamp()*1000)
                    opening = opens.get(available)
                    if (available < effective_start or opening is None or
                            not available <= opening[0] <= available+120000 or opening[0] >= end):
                        expired += 1
                    else:
                        side = 'BUY' if pending.target == 'LONG' and quantity == 0 else 'SELL' if pending.target == 'FLAT' and quantity > 0 else None
                        if side:
                            quote_at, bid, ask = opening
                            mid = (bid+ask)/2
                            half = (ask-bid)*costs.spread_multiplier/2
                            price = mid+half+costs.slippage if side == 'BUY' else mid-half-costs.slippage
                            if price <= 0:
                                raise ValueError('Costs produce nonpositive gold fill price')
                            units = min(Decimal(10), cash*costs.allocation/(price+costs.commission)) if side == 'BUY' else quantity
                            units = units.quantize(Decimal('.01'), rounding=ROUND_FLOOR)
                            if units > 0:
                                fee = units*costs.commission
                                pnl = Decimal(0)
                                if side == 'BUY':
                                    cash -= units*price+fee
                                    quantity += units
                                    basis += units*price+fee
                                else:
                                    allocated = basis*units/quantity
                                    cash += units*price-fee
                                    quantity -= units
                                    basis -= allocated
                                    pnl = units*price-fee-allocated
                                    realized += pnl
                                    closed.append(pnl)
                                    if quantity == 0:
                                        basis = Decimal(0)
                                fees += fee
                                spread_cost += units*half
                                slippage_cost += units*costs.slippage
                                fills.append({'side': side, 'decision_at': available, 'execution_at': quote_at,
                                              'bid': str(bid), 'ask': str(ask), 'price': str(price),
                                              'ounces': str(units), 'fee': str(fee), 'spread_cost': str(units*half),
                                              'slippage_cost': str(units*costs.slippage), 'realized_pnl': str(pnl),
                                              'cash_after': str(cash), 'quantity_after': str(quantity), 'simulated': True})
                mark = bar.close-(bar.ask_close-bar.bid_close)*costs.spread_multiplier/2
                value = cash+quantity*mark
                if cash < 0 or quantity < 0 or value <= 0:
                    raise ArithmeticError('Gold research cash/inventory/equity invariant failed')
                if abs(value-costs.initial_cash-(realized+quantity*mark-basis)) > Decimal('1e-30'):
                    raise ArithmeticError('Gold research PnL conservation failed')
                peak = max(peak, value)
                drawdown = max(drawdown, (peak-value)/peak)
                holding += int(quantity > 0)
                if index == first or index == final or (index-first) % stride == 0:
                    curve.append({'at': bar.start+hours*HOUR, 'equity': str(value)})
            ema_value = ema.update(bar.close)
            context = StrategyContext(datetime.fromtimestamp((bar.start+hours*HOUR)/1000, timezone.utc),
                                      bar.close, ema_value, bar.high, bar.low)
            pending = TargetSignal('LONG', context.as_of) if benchmark else strategy.decide(context)
        wins = sum((p for p in closed if p > 0), Decimal(0))
        losses = -sum((p for p in closed if p < 0), Decimal(0))
        return {'strategy': variant['strategy'], 'parameters': {} if benchmark else parameters.model_dump(mode='json'),
                'timeframe': f'{hours}h', 'requested_start': start, 'requested_end': end,
                'effective_start': effective_start, 'effective_end': bars[final].start+hours*HOUR,
                'warmup_bars': first-prefix, 'bars': final-first+1, 'config': costs.model_dump(mode='json'),
                'metrics': {'total_return': str(value/costs.initial_cash-1), 'net_pnl': str(value-costs.initial_cash),
                            'final_equity': str(value), 'realized_pnl': str(realized), 'unrealized_pnl': str(quantity*mark-basis),
                            'max_drawdown': str(drawdown), 'fees': str(fees), 'spread_cost': str(spread_cost),
                            'slippage_cost': str(slippage_cost), 'fill_count': len(fills), 'closed_trades': len(closed),
                            'win_rate': str(Decimal(sum(p > 0 for p in closed))/len(closed)) if closed else None,
                            'profit_factor': str(wins/losses) if losses else None,
                            'open_ounces': str(quantity), 'exposure_bar_fraction': str(Decimal(holding)/(final-first+1)),
                            'expired_gap_intents': expired}, 'equity': curve, 'fills': fills,
                'pending_final_signal': pending.target if pending else None, 'trading_enabled': False}


def compare(data, hourly, request, checkpoint=lambda done, total: None):
    start, end = request['start'], request['end']
    base = Costs.model_validate(request['costs'])
    opens = {row[0]: (row[1], Decimal(row[2]), Decimal(row[3])) for row in data['opens']}
    submitted = request['variants']
    variants = submitted+[{'strategy': 'buy_hold_reference', 'parameters': {}}]
    warmup = max(100, *(lookback(v['strategy'], v['parameters']) for v in submitted))
    scenarios = [('frictionless_reference', base.model_copy(update={'commission': Decimal(0), 'slippage': Decimal(0), 'spread_multiplier': Decimal(0)})),
                 ('configured_costs', base), ('double_cost_stress', base.model_copy(update={
                     'commission': base.commission*2, 'slippage': base.slippage*2, 'spread_multiplier': base.spread_multiplier*2}))]
    years = []
    first_year, last_year = datetime.fromtimestamp(start/1000, timezone.utc).year, datetime.fromtimestamp((end-1)/1000, timezone.utc).year
    for year in range(first_year, last_year+1):
        a = max(start, int(datetime(year, 1, 1, tzinfo=timezone.utc).timestamp()*1000))
        b = min(end, int(datetime(year+1, 1, 1, tzinfo=timezone.utc).timestamp()*1000))
        years.append((str(year), a, b))
    plan = [('full', start, end, name, costs) for name, costs in scenarios]
    plan += [(name, a, b, 'configured_costs', base) for name, a, b in years]
    total = len(plan)*len(request['timeframes'])*len(variants)
    results = []
    for timeframe in request['timeframes']:
        hours = int(timeframe[0])
        bars = aggregate(hourly, hours)
        for variant in variants:
            for window, a, b, scenario, costs in plan:
                result = simulate(bars, hours, a, b, costs, variant, warmup, opens)
                role = 'full' if window == 'full' else ('descriptive' if len(years) < 3 else
                        'holdout' if window == years[-1][0] else 'validation' if window == years[-2][0] else 'development')
                result.update(window=window, scenario=scenario, role=role)
                results.append(result)
                checkpoint(len(results), total)
    references = {(r['timeframe'], r['window'], r['scenario']): Decimal(r['metrics']['total_return'])
                  for r in results if r['strategy'] == 'buy_hold_reference'}
    for result in results:
        result['metrics']['return_difference_vs_buy_hold'] = str(Decimal(result['metrics']['total_return'])-
            references[(result['timeframe'], result['window'], result['scenario'])])
    manifest = {'engine_version': VERSION, 'dataset_id': data['identity'], 'source': data['source'],
                'code_sha256': CODE_SHA256,
                'history_provenance': {key: data[key] for key in ('version', 'start', 'end', 'quality', 'sources')},
                'request': request, 'strategy_definitions': [resolve(v['strategy']).describe() for v in submitted],
                'benchmark': 'first eligible buy; same initial cash, allocation, unit constraints and costs; no rebalancing',
                'year_roles': [{'year': year, 'start': a, 'end': b, 'role': 'descriptive' if len(years)<3 else
                               'holdout' if year==years[-1][0] else 'validation' if year==years[-2][0] else 'development'} for year,a,b in years],
                'selection_policy': 'fixed submitted parameters; no optimization against yearly holdouts',
                'assumptions': ['1x long/flat independent USD; 0.01oz step; 10oz per fill; no broker submission',
                                'observed closed mid bars; next UTC boundary first paired quote independent of later candle coverage',
                                'missing or late opening quotes cancel intents; no post-gap backfill',
                                'bid/ask spread recorded in source; fee and adverse slippage are explicit research assumptions',
                                'double-cost stress is a counterfactual; zero-cost reference uses mid marking',
                                'indicators use observed bars across closures/gaps; no fabricated missing candles',
                                'common prehistory warmup; insufficient first-window history shifts effective_start explicitly',
                                'no forced terminal liquidation; last pending signal is not executed',
                                'no financing, overnight swaps, historical executable depth or liquidity guarantees',
                                'historical HistData and live Swissquote are different feeds; results do not predict broker fills']}
    return {'run_id': sha(manifest), 'manifest': manifest, 'quality': data['quality'], 'results': results, 'trading_enabled': False}
