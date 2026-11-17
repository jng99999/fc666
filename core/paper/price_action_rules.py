"""Deterministic closed-bar Decimal research signals for synthetic directional PAPER.

No orders, quotes, protection prices, news bypass or spot-short capability live here.
Every region is confirmed causally and keeps its original formation boundaries.
"""
from datetime import datetime, timedelta, timezone
from decimal import Decimal, localcontext
from typing import Literal, Sequence

from pydantic import Field, ValidationInfo, field_validator, model_validator

from core.paper.directional import Boundary

VERSION = 'closed-price-action-paper-v1'
REQUIRED = 64
Rule = Literal['STRUCTURE_FVG', 'RANGE_REVERSION', 'TREND_PULLBACK']
INTERVALS = {'1m': 60, '5m': 300, '15m': 900, '1h': 3600, '4h': 14400, '1d': 86400}
RULES = ('STRUCTURE_FVG', 'RANGE_REVERSION', 'TREND_PULLBACK')
D = Decimal
EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)


class RuleBar(Boundary):
    instrument: str = Field(min_length=1, max_length=128)
    source: str = Field(min_length=1, max_length=128)
    timeframe: Literal['1m', '5m', '15m', '1h', '4h', '1d']
    open_time: datetime
    close_time: datetime
    open: Decimal = Field(gt=0, max_digits=38, decimal_places=18)
    high: Decimal = Field(gt=0, max_digits=38, decimal_places=18)
    low: Decimal = Field(gt=0, max_digits=38, decimal_places=18)
    close: Decimal = Field(gt=0, max_digits=38, decimal_places=18)
    volume: Decimal | None = Field(default=None, ge=0, max_digits=38, decimal_places=18)
    is_closed: Literal[True]

    @field_validator('*', mode='before')
    @classmethod
    def exact(cls, value, info: ValidationInfo):
        # Boundary rejects booleans generally; this single explicit flag requires True.
        if info.field_name == 'is_closed':
            if value is not True:
                raise ValueError('Only explicitly finalized bars are accepted')
            return value
        if info.field_name in ('open_time', 'close_time') and not isinstance(value, (str, datetime)):
            raise ValueError('An explicit timezone-aware timestamp is required')
        return super().exact(value)

    @model_validator(mode='after')
    def valid(self):
        seconds = INTERVALS[self.timeframe]
        elapsed = self.open_time - EPOCH
        if (self.open_time.microsecond or self.open_time < EPOCH
                or (elapsed.days * 86400 + elapsed.seconds) % seconds
                or self.close_time - self.open_time != timedelta(seconds=seconds)):
            raise ValueError('Bar must cover one aligned complete UTC interval')
        if self.low > min(self.open, self.close) or self.high < max(self.open, self.close, self.low):
            raise ValueError('OHLC bounds are inconsistent')
        return self


class _Average:
    def __init__(self, period: int, *, wilder: bool = False):
        self.period = period
        self.alpha = D(1) / period if wilder else D(2) / (period + 1)
        self.seed: list[Decimal] = []
        self.value: Decimal | None = None

    def push(self, value: Decimal) -> Decimal | None:
        if self.value is None:
            self.seed.append(value)
            if len(self.seed) == self.period:
                self.value = sum(self.seed, D(0)) / self.period
                self.seed.clear()
        else:
            self.value += self.alpha * (value - self.value)
        return self.value


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


class _Engine:
    def __init__(self):
        self.bars: list[RuleBar] = []
        self.ema20 = _Average(20)
        self.ema60 = _Average(60)
        self.atr = _Average(14, wilder=True)
        self.gain = _Average(14, wilder=True)
        self.loss = _Average(14, wilder=True)
        self.rsi: Decimal | None = None
        self.high = self.low = None
        self.high_broken = self.low_broken = False
        self.direction: str | None = None
        self.breaks: list[dict] = []
        self.fvgs: list[dict] = []
        self.ranges: list[dict] = []
        self.range: dict | None = None
        self.last_range_break = -1000
        self.previous_condition: dict[str, str | None] = dict.fromkeys(RULES)

    def push(self, bar: RuleBar) -> dict[str, str | None]:
        index = len(self.bars)
        previous = self.bars[-1] if self.bars else None
        tr = max(bar.high - bar.low, abs(bar.high - previous.close), abs(bar.low - previous.close)) if previous else bar.high - bar.low
        atr = self.atr.push(tr)
        ema20, ema60 = self.ema20.push(bar.close), self.ema60.push(bar.close)
        if previous:
            change = bar.close - previous.close
            gain, loss = self.gain.push(max(change, D(0))), self.loss.push(max(-change, D(0)))
            if gain is not None and loss is not None:
                self.rsi = D(50) if gain == loss == 0 else D(100) if loss == 0 else D(100) - D(100) / (D(1) + gain / loss)
        self.bars.append(bar)
        conditions: dict[str, str | None] = dict.fromkeys(RULES)
        trending_up = ema20 is not None and ema60 is not None and ema20 > ema60 and bar.close > ema20
        trending_down = ema20 is not None and ema60 is not None and ema20 < ema60 and bar.close < ema20

        # Observe only older regions; a forming candle never trades its own new FVG/box.
        for gap in self.fvgs:
            if gap['state'] == 'MITIGATED':
                continue
            filled = bar.low <= gap['low'] if gap['direction'] == 'LONG' else bar.high >= gap['high']
            if filled:
                gap['state'] = 'MITIGATED'
                gap['invalidated_at'] = bar.close_time
                continue
            touch = bar.low <= gap['high'] and bar.high >= gap['low']
            if touch:
                gap['state'] = 'TOUCHED'
                gap['last_touch_at'] = bar.close_time
            since_break = bool(self.breaks) and gap['confirmed_at'] >= self.breaks[-1]['available_at']
            if touch and since_break and self.rsi is not None:
                if (gap['direction'] == self.direction == 'LONG' and trending_up
                        and D(50) <= self.rsi <= D(75) and bar.close > gap['high'] and bar.close > bar.open):
                    conditions['STRUCTURE_FVG'] = 'LONG'
                if (gap['direction'] == self.direction == 'SHORT' and trending_down
                        and D(25) <= self.rsi <= D(50) and bar.close < gap['low'] and bar.close < bar.open):
                    conditions['STRUCTURE_FVG'] = 'SHORT'

        if self.range and self.range['state'] != 'BROKEN':
            box = self.range
            if bar.close > box['high'] + box['buffer'] or bar.close < box['low'] - box['buffer']:
                box['state'] = 'BROKEN'
                box['invalidated_at'] = bar.close_time
                self.last_range_break = index
            else:
                edge = box['edge']
                midpoint = (box['low'] + box['high']) / 2
                if self.rsi is not None:
                    if (bar.low <= box['low'] + edge and box['low'] + edge < bar.close < midpoint
                            and bar.close > bar.open and self.rsi <= D(45)):
                        conditions['RANGE_REVERSION'] = 'LONG'
                    if (bar.high >= box['high'] - edge and midpoint < bar.close < box['high'] - edge
                            and bar.close < bar.open and self.rsi >= D(55)):
                        conditions['RANGE_REVERSION'] = 'SHORT'

        # A close must cross a previously confirmed pivot, not a pivot discovered now.
        for direction, pivot, crossed in (
            ('LONG', self.high, self.high is not None and not self.high_broken and bar.close > self.high['price']),
            ('SHORT', self.low, self.low is not None and not self.low_broken and bar.close < self.low['price']),
        ):
            if crossed:
                kind = 'CHOCH' if self.direction is not None and direction != self.direction else 'BOS'
                self.direction = direction
                self.breaks.append({'kind': kind, 'direction': direction, 'price': pivot['price'], 'pivot_time': pivot['time'], 'available_at': bar.close_time})
                if direction == 'LONG':
                    self.high_broken = True
                else:
                    self.low_broken = True
                # Wait for a later retest after this new break; no same-bar shortcut.
                conditions['STRUCTURE_FVG'] = None

        if index >= 4:
            candidate = self.bars[index - 2]
            neighbours = self.bars[index - 4:index - 2] + self.bars[index - 1:index + 1]
            if all(candidate.high > row.high for row in neighbours):
                self.high = {'price': candidate.high, 'time': candidate.open_time, 'confirmed_at': bar.close_time}
                self.high_broken = False
            if all(candidate.low < row.low for row in neighbours):
                self.low = {'price': candidate.low, 'time': candidate.open_time, 'confirmed_at': bar.close_time}
                self.low_broken = False

        if index >= 2 and atr is not None:
            first = self.bars[index - 2]
            if bar.low > first.high and bar.low - first.high >= atr * D('.2'):
                self.fvgs.append({'direction': 'LONG', 'low': first.high, 'high': bar.low, 'formed_at': first.open_time, 'confirmed_at': bar.close_time, 'state': 'ACTIVE'})
            if first.low > bar.high and first.low - bar.high >= atr * D('.2'):
                self.fvgs.append({'direction': 'SHORT', 'low': bar.high, 'high': first.low, 'formed_at': first.open_time, 'confirmed_at': bar.close_time, 'state': 'ACTIVE'})

        if index >= 23 and atr is not None and (not self.range or self.range['state'] == 'BROKEN') and index - self.last_range_break >= 24:
            window = self.bars[-24:]
            low, high = min(row.low for row in window), max(row.high for row in window)
            height = high - low
            distance = sum((abs(right.close - left.close) for left, right in zip(window, window[1:])), D(0))
            efficiency = abs(window[-1].close - window[0].close) / distance if distance else D(0)
            edge = max(atr * D('.2'), height * D('.04'))
            def hits(side):
                count, last = 0, -1000
                for i, row in enumerate(window):
                    if i - last >= 3 and (row.high >= high - edge if side == 'high' else row.low <= low + edge):
                        count, last = count + 1, i
                return count
            upper, lower = hits('high'), hits('low')
            if 0 < height <= atr * 6 and efficiency <= D('.35') and upper >= 2 and lower >= 2:
                self.range = {'low': low, 'high': high, 'edge': edge, 'buffer': atr * D('.1'), 'formed_at': window[0].open_time, 'confirmed_at': bar.close_time, 'upper_touches': upper, 'lower_touches': lower, 'efficiency': efficiency, 'state': 'ACTIVE'}
                self.ranges.append(self.range)

        if self.rsi is not None and previous:
            if trending_up and bar.low <= ema20 and bar.close > bar.open and D(50) <= self.rsi <= D(70):
                conditions['TREND_PULLBACK'] = 'LONG'
            if trending_down and bar.high >= ema20 and bar.close < bar.open and D(30) <= self.rsi <= D(50):
                conditions['TREND_PULLBACK'] = 'SHORT'

        signals = dict.fromkeys(RULES)
        if len(self.bars) >= REQUIRED:
            signals = {rule: direction if direction is not None and direction != self.previous_condition[rule] else None for rule, direction in conditions.items()}
            self.previous_condition = conditions
        return signals

    def evidence(self, gaps: int, total: int):
        return _json({'parameters': {'required': REQUIRED, 'pivot_radius': 2, 'atr_period': 14, 'ema_periods': [20, 60], 'rsi_period': 14, 'range_window': 24, 'range_max_efficiency': D('.35'), 'range_max_atr_height': D(6), 'range_edge_atr': D('.2'), 'range_edge_height': D('.04'), 'range_break_buffer_atr': D('.1'), 'fvg_min_atr': D('.2'), 'structure_rsi_long': [D(50), D(75)], 'structure_rsi_short': [D(25), D(50)], 'range_rsi_long_max': D(45), 'range_rsi_short_min': D(55), 'trend_rsi_long': [D(50), D(70)], 'trend_rsi_short': [D(30), D(50)]}, 'input_samples': total, 'gaps': gaps, 'segment_samples': len(self.bars), 'ema20': self.ema20.value, 'ema60': self.ema60.value, 'rsi14': self.rsi, 'atr14': self.atr.value, 'structure': {'direction': self.direction, 'high': self.high, 'low': self.low, 'breaks': self.breaks[-12:]}, 'ranges': self.ranges[-8:], 'fvgs': self.fvgs[-24:], 'fvg_count': len(self.fvgs), 'conditions': self.previous_condition, 'scope': 'SYNTHETIC_DIRECTIONAL_PAPER_ONLY', 'execution': 'No orders or protective prices; executable quote, news, limits and protection remain engine gates.'})


def analyze(bars: Sequence[RuleBar], *, instrument: str, source: str, timeframe: str, now: datetime, rule: Rule) -> dict:
    """Fail closed on invalid data; None means no new risk signal, never FLAT.

    All history must already be closed at ``now``. A missing interval discards the
    preceding segment's indicators/regions and requires 64 fresh contiguous bars.
    """
    unavailable = {'rule_version': VERSION, 'state': 'UNAVAILABLE', 'samples': 0, 'required': REQUIRED, 'target': None, 'available_at': None, 'reason': '', 'evidence': {}}
    try:
        if rule not in RULES or timeframe not in INTERVALS or not instrument or not source:
            raise ValueError('Invalid rule or explicit instrument/source/timeframe scope')
        if not isinstance(now, datetime) or now.tzinfo is None or now.utcoffset() is None:
            raise ValueError('now must be timezone-aware')
        if not isinstance(bars, (list, tuple)) or len(bars) > 500:
            raise ValueError('At most 500 explicitly closed bars are accepted')
        # Revalidate even model_construct instances instead of trusting their flags.
        checked = [RuleBar.model_validate(row.model_dump()) if isinstance(row, RuleBar) else RuleBar.model_validate(row) for row in bars]
        previous = None
        for row in checked:
            if (row.instrument, row.source, row.timeframe) != (instrument, source, timeframe):
                raise ValueError('Mixed instrument, source or timeframe')
            if row.close_time > now:
                raise ValueError('Future or unfinished bar rejected')
            if previous and row.open_time < previous.close_time:
                raise ValueError('Duplicate, overlapping or unordered bars rejected')
            previous = row
        with localcontext() as precision:
            precision.prec = 50
            engine, gaps, signals = _Engine(), 0, dict.fromkeys(RULES)
            for index, row in enumerate(checked):
                if index and row.open_time != checked[index - 1].close_time:
                    engine, gaps = _Engine(), gaps + 1
                signals = engine.push(row)
            ready = len(engine.bars) >= REQUIRED
            target = signals[rule] if ready else None
            evidence = engine.evidence(gaps, len(checked))
            evidence['market'] = {'instrument': instrument, 'source': source, 'timeframe': timeframe}
            return {'rule_version': VERSION, 'state': 'READY' if ready else 'WARMUP', 'samples': len(engine.bars), 'required': REQUIRED, 'target': target, 'available_at': checked[-1].close_time.isoformat() if checked else None, 'reason': f'NEW_{target}_SIGNAL' if target else 'NO_NEW_SIGNAL' if ready else 'GAP_RESET_WARMUP' if gaps else 'CLOSED_HISTORY_WARMUP', 'evidence': evidence}
    except (ValueError, TypeError, ArithmeticError) as error:
        return {**unavailable, 'reason': str(error)}
