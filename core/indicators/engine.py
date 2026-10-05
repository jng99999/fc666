"""Causal, closed-bar research indicators. Monetary accounting never uses these floats."""
from collections import deque
from datetime import datetime
import math
from core.models import Candle


class Average:
    def __init__(self, period, wilder=False):
        self.period, self.alpha = period, 1 / period if wilder else 2 / (period + 1)
        self.seed = []
        self.value = None

    def push(self, value):
        if self.value is None:
            self.seed.append(value)
            if len(self.seed) == self.period:
                self.value = math.fsum(self.seed) / self.period
                self.seed.clear()
        else:
            self.value += self.alpha * (value - self.value)
        return self.value


class IndicatorEngine:
    """One instrument/interval, strictly contiguous finalized bars; gap = explicit error.

    EMA seeds with SMA; RSI/ATR use Wilder smoothing. VWAP is UTC-session
    typical-price weighted by base volume, not exchange tick VWAP. Swings are
    emitted only on confirmation, never retroactively inserted into old outputs.
    """
    def __init__(self, period=20, oscillator_period=14, swing_radius=2):
        for value in (period, oscillator_period, swing_radius):
            if type(value) is not int or not 1 <= value <= 500:
                raise ValueError('Periods must be integers in [1, 500]')
        self.period, self.radius = period, swing_radius
        self.window = deque(maxlen=period)
        self.regime_window = deque(maxlen=period+1)
        self.swings = deque(maxlen=2 * swing_radius + 1)
        self.ema = Average(period)
        self.fast, self.slow, self.signal = Average(12), Average(26), Average(9)
        self.gain, self.loss, self.atr = Average(oscillator_period, True), Average(oscillator_period, True), Average(oscillator_period, True)
        self.previous = None
        self.session = None
        self.weight = self.weighted = 0.0
        self.last_high = self.last_low = None
        self.high_broken = self.low_broken = False
        self.structure_direction = None

    def push(self, bar: Candle):
        if not bar.is_closed:
            raise ValueError('Indicators require finalized candles')
        prev = self.previous
        if prev and (bar.instrument_id != prev.instrument_id or bar.timeframe != prev.timeframe or bar.open_time != prev.close_time):
            raise ValueError('Indicators require one contiguous ordered series')
        close, high, low, volume = (float(v) for v in (bar.close, bar.high, bar.low, bar.volume))
        if not all(math.isfinite(v) for v in (close, high, low, volume)):
            raise ValueError('Non-finite float research conversion')
        self.window.append(close)
        self.regime_window.append(close)
        sma = math.fsum(self.window) / self.period if len(self.window) == self.period else None
        std = math.sqrt(math.fsum((v - sma) ** 2 for v in self.window) / self.period) if sma is not None else None
        ema = self.ema.push(close)
        fast, slow = self.fast.push(close), self.slow.push(close)
        macd = fast - slow if fast is not None and slow is not None else None
        signal = self.signal.push(macd) if macd is not None else None
        tr = high - low if prev is None else max(high - low, abs(high - float(prev.close)), abs(low - float(prev.close)))
        atr = self.atr.push(tr)
        rsi = None
        if prev:
            delta = close - float(prev.close)
            gain, loss = self.gain.push(max(delta, 0)), self.loss.push(max(-delta, 0))
            if gain is not None and loss is not None:
                rsi = 50.0 if gain == loss == 0 else 100.0 if loss == 0 else 100 - 100 / (1 + gain / loss)
        if bar.open_time.date() != self.session:
            self.session = bar.open_time.date()
            self.weight = self.weighted = 0.0
        self.weight += volume
        self.weighted += ((high + low + close) / 3) * volume
        vwap = self.weighted / self.weight if self.weight else None
        self.swings.append(bar)
        confirmed = []
        if len(self.swings) == self.swings.maxlen:
            candidate = self.swings[self.radius]
            others = [b for i, b in enumerate(self.swings) if i != self.radius]
            for kind, price, is_pivot in (
                ('high', candidate.high, all(candidate.high > b.high for b in others)),
                ('low', candidate.low, all(candidate.low < b.low for b in others)),
            ):
                if is_pivot:
                    old = self.last_high if kind == 'high' else self.last_low
                    label = None if old is None else ('EH' if price == old else 'HH' if price > old else 'LH') if kind == 'high' else ('EL' if price == old else 'HL' if price > old else 'LL')
                    confirmed.append({'kind': kind, 'price': str(price), 'pivot_time': candidate.open_time.isoformat(), 'confirmed_at': bar.close_time.isoformat(), 'label': label})
                    if kind == 'high':
                        self.last_high = price
                        self.high_broken = False
                    else:
                        self.last_low = price
                        self.low_broken = False
        breaks = []
        for direction, level, crossed in (
            ('up', self.last_high, self.last_high is not None and not self.high_broken and bar.close > self.last_high),
            ('down', self.last_low, self.last_low is not None and not self.low_broken and bar.close < self.last_low),
        ):
            if crossed:
                breaks.append({'direction': direction, 'kind': 'CHOCH' if self.structure_direction is not None and direction != self.structure_direction else 'BOS', 'level': str(level), 'available_at': bar.close_time.isoformat()})
                self.structure_direction = direction
                if direction == 'up': self.high_broken = True
                else: self.low_broken = True
        self.previous = bar
        values = dict(sma=sma, ema=ema, rsi=rsi, atr=atr, macd=macd, macd_signal=signal, macd_histogram=None if signal is None else macd - signal,
                      bollinger_mid=sma, bollinger_upper=None if std is None else sma + 2 * std, bollinger_lower=None if std is None else sma - 2 * std, vwap=vwap)
        if any(v is not None and not math.isfinite(v) for v in values.values()):
            raise ValueError('Non-finite indicator output')
        ready=len(self.regime_window)==self.regime_window.maxlen and atr is not None
        movement=math.fsum(abs(b-a) for a,b in zip(self.regime_window,list(self.regime_window)[1:]))
        change=self.regime_window[-1]-self.regime_window[0]
        efficiency=abs(change)/movement if ready and movement else 0.0 if ready else None
        atr_fraction=atr/close if atr is not None else None
        regime={'rule_version':'er-atr-v1','available_at':bar.close_time.isoformat(),'lookback_changes':self.period,
                'label':'UNAVAILABLE' if not ready else 'RANGE' if efficiency<.35 else 'TREND_UP' if change>0 else 'TREND_DOWN',
                'efficiency_ratio':efficiency,'atr_fraction':atr_fraction,
                'volatility':'UNAVAILABLE' if atr_fraction is None else 'HIGH' if atr_fraction>=.02 else 'LOW' if atr_fraction<.005 else 'NORMAL'}
        return {'regime':regime,'open_time': bar.open_time.isoformat(), 'available_at': bar.close_time.isoformat(), 'values': values, 'confirmed_swings': confirmed, 'structure_breaks': breaks,
                'trend': 'unavailable' if ema is None or sma is None else 'up' if close > ema and ema > sma else 'down' if close < ema and ema < sma else 'mixed'}


def calculate(bars, *, as_of: datetime, period=20, oscillator_period=14, swing_radius=2):
    if as_of.tzinfo is None or as_of.utcoffset() is None:
        raise ValueError('as_of must be timezone-aware')
    engine = IndicatorEngine(period, oscillator_period, swing_radius)
    return [engine.push(bar) for bar in bars if bar.is_closed and bar.close_time <= as_of]
