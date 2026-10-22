"""Isolated causal XAU/USD PPO environment. No service, account or broker imports."""
from collections import Counter
from decimal import Decimal, ROUND_FLOOR, localcontext
import math

import gymnasium as gym
from gymnasium import spaces
import numpy as np

from core.gold.backtest import Costs
from core.gold.history import HOUR

VERSION = 'gold-ppo-target-v1'
WEIGHTS = (Decimal('0'), Decimal('.25'), Decimal('.5'))
FEATURES = ('return_1', 'return_4', 'return_20', 'range_20', 'volatility_20',
            'spread_fraction', 'elapsed_hours_log', 'cash_fraction', 'exposure', 'drawdown')


def market_features(bars):
    """Bounded, dimensionless, prefix-only features; no fitted or future fill values."""
    rows = np.zeros((len(bars), 7), dtype=np.float32)
    for i in range(20, len(bars)):
        close = float(bars[i].close)
        returns = [math.log(float(bars[j].close / bars[j-1].close)) for j in range(i-19, i+1)]
        values = [math.log(close/float(bars[i-n].close)) for n in (1, 4, 20)]
        values += [float((max(b.high for b in bars[i-19:i+1])-min(b.low for b in bars[i-19:i+1]))/bars[i].close),
                   float(np.std(returns)), float((bars[i].ask_close-bars[i].bid_close)/bars[i].close),
                   math.log1p((bars[i].start-bars[i-1].start)/HOUR)]
        if not all(math.isfinite(v) for v in values):
            raise ValueError('Non-finite causal gold features')
        rows[i] = np.clip(values, -10, 10)
    return rows


class GoldEnv(gym.Env):
    """A frozen action at close, next boundary paired fill, next observed close reward.

    An episode is an explicit date window, not a shuffled sample. End-of-window
    observations cannot trigger a new fill. Missing quotes cancel just that action.
    """
    metadata = {'render_modes': []}

    def __init__(self, bars, opens, start, end, costs, *, features=None, receipts=True):
        super().__init__()
        self.bars = tuple(bars)
        if any(b.start <= a.start for a, b in zip(self.bars, self.bars[1:])):
            raise ValueError('Gold RL bars must be strictly increasing')
        self.opens = opens
        self.start, self.end = start, end
        self.costs = Costs.model_validate(costs)
        if self.costs.allocation != Decimal('.5'):
            raise ValueError('Gold PPO v1 requires an explicit 50% maximum allocation')
        indices = [i for i, b in enumerate(bars) if start <= b.start and b.start+HOUR <= end]
        if not indices or max(indices[0], 100) >= indices[-1]:
            raise ValueError('Insufficient gold RL window after 100-bar warmup')
        self.first, self.final = max(indices[0], 100), indices[-1]
        self.effective_start = max(start, bars[self.first-1].start+HOUR)
        self.features = market_features(bars) if features is None else features
        if self.features.shape != (len(bars), 7) or not np.isfinite(self.features).all():
            raise ValueError('Invalid gold RL feature matrix')
        self.keep_receipts = receipts
        self.action_space = spaces.Discrete(len(WEIGHTS))
        self.observation_space = spaces.Box(-10, 10, shape=(len(FEATURES),), dtype=np.float32)
        self.reset()

    def _mark(self, index):
        bar = self.bars[index]
        return bar.close-(bar.ask_close-bar.bid_close)*self.costs.spread_multiplier/2

    def _observation(self):
        value = self.cash+self.quantity*self._mark(self.index)
        state = [float(self.cash/value), float(self.quantity*self._mark(self.index)/value),
                 float((self.peak-value)/self.peak)]
        observation = np.concatenate((self.features[self.index], np.array(state, dtype=np.float32)))
        if not np.isfinite(observation).all():
            raise ArithmeticError('Non-finite gold RL account observation')
        return np.clip(observation, -10, 10).astype(np.float32)

    def reset(self, *, seed=None, options=None):
        super().reset(seed=seed)
        self.index = self.first-1
        self.cash = self.peak = self.value = self.costs.initial_cash
        self.quantity = self.basis = self.realized = Decimal(0)
        self.fees = self.spread_cost = self.slippage_cost = self.drawdown = Decimal(0)
        self.fills, self.curve = [], []
        self.fill_count = self.expired = self.holding = 0
        self.actions = Counter()
        self.finished = False
        return self._observation(), {}

    def _execute(self, action, at):
        opening = self.opens.get(at)
        if (at < self.effective_start or opening is None or not at <= opening[0] <= at+120000
                or opening[0] >= self.end):
            self.expired += 1
            return
        ts, bid, ask = opening
        if not all(p.is_finite() and p > 0 for p in (bid, ask)) or ask < bid:
            raise ValueError('Invalid gold RL paired opening quote')
        mid = (bid+ask)/2
        half = (ask-bid)*self.costs.spread_multiplier/2
        buy, sell = mid+half+self.costs.slippage, mid-half-self.costs.slippage
        if sell <= 0:
            raise ValueError('Costs produce nonpositive gold RL price')
        # Conservative execution-time sizing; the action itself used only the prior close.
        equity = self.cash+self.quantity*(mid-half)
        target = (equity*WEIGHTS[action]/(buy+self.costs.commission)).quantize(Decimal('.01'), rounding=ROUND_FLOOR)
        difference = target-self.quantity
        if not difference:
            return
        side = 'BUY' if difference > 0 else 'SELL'
        units = min(abs(difference), Decimal(10))
        if side == 'BUY':
            units = min(units, (self.cash/(buy+self.costs.commission)).quantize(Decimal('.01'), rounding=ROUND_FLOOR))
        if units <= 0:
            return
        price = buy if side == 'BUY' else sell
        fee = units*self.costs.commission
        pnl = Decimal(0)
        if side == 'BUY':
            self.cash -= units*price+fee
            self.quantity += units
            self.basis += units*price+fee
        else:
            allocated = self.basis*units/self.quantity
            self.cash += units*price-fee
            self.quantity -= units
            self.basis -= allocated
            pnl = units*price-fee-allocated
            self.realized += pnl
            if not self.quantity:
                self.basis = Decimal(0)
        self.fees += fee
        self.spread_cost += units*half
        self.slippage_cost += units*self.costs.slippage
        self.fill_count += 1
        if self.keep_receipts:
            self.fills.append({'side': side, 'decision_at': at, 'execution_at': ts, 'action': int(action),
                               'target_weight': str(WEIGHTS[action]), 'bid': str(bid), 'ask': str(ask),
                               'price': str(price), 'ounces': str(units), 'fee': str(fee),
                               'cash_after': str(self.cash), 'quantity_after': str(self.quantity), 'simulated': True})

    def step(self, action):
        if self.finished:
            raise RuntimeError('Gold RL episode finished; reset required')
        if isinstance(action, (bool, np.bool_)) or not isinstance(action, (int, np.integer)) or not self.action_space.contains(action):
            raise ValueError('Invalid gold RL action; no fallback policy')
        before = self.value
        with localcontext() as precision:
            precision.prec = 60
            self.actions[int(action)] += 1
            self._execute(int(action), self.bars[self.index].start+HOUR)
            self.index += 1
            mark = self._mark(self.index)
            self.value = self.cash+self.quantity*mark
            if self.cash < 0 or self.quantity < 0 or self.value <= 0:
                raise ArithmeticError('Gold RL balance invariant failed')
            if abs(self.value-self.costs.initial_cash-(self.realized+self.quantity*mark-self.basis)) > Decimal('1e-30'):
                raise ArithmeticError('Gold RL PnL conservation failed')
            self.peak = max(self.peak, self.value)
            self.drawdown = max(self.drawdown, (self.peak-self.value)/self.peak)
            self.holding += int(self.quantity > 0)
            if self.keep_receipts:
                self.curve.append({'at': self.bars[self.index].start+HOUR, 'equity': str(self.value)})
            # End of a historical window is a time-limit truncation, not insolvency.
            self.finished = self.index == self.final
            reward = math.log(float(self.value/before))*100
        return self._observation(), reward, False, self.finished, {}

    def result(self):
        if not self.finished:
            raise RuntimeError('Cannot report incomplete gold RL episode')
        return {'metrics': {'total_return': str(self.value/self.costs.initial_cash-1),
                'final_equity': str(self.value), 'max_drawdown': str(self.drawdown),
                'fees': str(self.fees), 'spread_cost': str(self.spread_cost), 'slippage_cost': str(self.slippage_cost),
                'fill_count': self.fill_count, 'net_pnl': str(self.value-self.costs.initial_cash),
                'realized_pnl': str(self.realized), 'open_ounces': str(self.quantity),
                'expired_gap_intents': self.expired}, 'action_counts': [self.actions[i] for i in range(3)],
                'effective_start': self.effective_start, 'effective_end': self.bars[self.final].start+HOUR,
                'fills': self.fills, 'equity': self.curve, 'trading_enabled': False}
