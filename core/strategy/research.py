"""Causal long/flat research rules. Provenance and adaptations: docs/research-strategies.md."""
from collections import deque
from decimal import Decimal, localcontext
from pydantic import BaseModel, ConfigDict, Field, model_validator
from core.strategy.contracts import StrategyContext, TargetSignal


class RsiParameters(BaseModel):
    model_config = ConfigDict(frozen=True, extra='forbid')
    period: int = Field(default=14, ge=2, le=500, strict=True)
    lower: int = Field(default=30, ge=1, le=99, strict=True)
    upper: int = Field(default=70, ge=1, le=99, strict=True)

    @model_validator(mode='after')
    def ordered(self):
        if self.lower >= self.upper:
            raise ValueError('lower must be smaller than upper')
        return self


class BollingerParameters(BaseModel):
    model_config = ConfigDict(frozen=True, extra='forbid')
    period: int = Field(default=20, ge=2, le=500, strict=True)
    deviations: int = Field(default=2, ge=1, le=5, strict=True)


class ChannelParameters(BaseModel):
    model_config = ConfigDict(frozen=True, extra='forbid')
    entry: int = Field(default=20, ge=2, le=500, strict=True)
    exit: int = Field(default=10, ge=2, le=500, strict=True)

    @model_validator(mode='after')
    def ordered(self):
        if self.exit > self.entry:
            raise ValueError('exit must not exceed entry')
        return self


class MacdParameters(BaseModel):
    model_config = ConfigDict(frozen=True, extra='forbid')
    fast: int = Field(default=12, ge=2, le=500, strict=True)
    slow: int = Field(default=26, ge=2, le=500, strict=True)
    signal: int = Field(default=9, ge=2, le=500, strict=True)

    @model_validator(mode='after')
    def ordered(self):
        if self.fast >= self.slow:
            raise ValueError('fast must be smaller than slow')
        return self


class RocParameters(BaseModel):
    model_config = ConfigDict(frozen=True, extra='forbid')
    period: int = Field(default=20, ge=2, le=500, strict=True)


class ClosedRule:
    def __init__(self, parameters):
        self.parameters = parameters
        self.target = 'FLAT'
        self.as_of = None

    def check(self, context):
        if context.as_of.tzinfo is None or context.as_of.utcoffset() is None:
            raise ValueError('Timezone-aware decision time required')
        if self.as_of is not None and context.as_of <= self.as_of:
            raise ValueError('Strictly increasing decision times required')
        if not context.close.is_finite() or context.close <= 0:
            raise ValueError('Finite positive close required')
        self.as_of = context.as_of

    def signal(self, context):
        return TargetSignal(self.target, context.as_of)


class RsiLongFlat(ClosedRule):
    def __init__(self, parameters):
        super().__init__(parameters)
        self.previous = None
        self.changes = 0
        self.gain = Decimal(0)
        self.loss = Decimal(0)

    def decide(self, context: StrategyContext):
        self.check(context)
        if self.previous is None:
            self.previous = context.close
            return None
        with localcontext() as precision:
            precision.prec = 60
            change = context.close - self.previous
            self.previous = context.close
            gain, loss = max(change, Decimal(0)), max(-change, Decimal(0))
            self.changes += 1
            period = self.parameters.period
            if self.changes <= period:
                self.gain += gain
                self.loss += loss
                if self.changes < period:
                    return None
                self.gain /= period
                self.loss /= period
            else:
                self.gain = (self.gain * (period - 1) + gain) / period
                self.loss = (self.loss * (period - 1) + loss) / period
            rsi = (Decimal(50) if self.gain == self.loss == 0 else
                   Decimal(100) if self.loss == 0 else
                   Decimal(100) * self.gain / (self.gain + self.loss))
            if rsi <= self.parameters.lower:
                self.target = 'LONG'
            elif rsi >= self.parameters.upper:
                self.target = 'FLAT'
        return self.signal(context)


class BollingerLongFlat(ClosedRule):
    def __init__(self, parameters):
        super().__init__(parameters)
        self.closes = deque(maxlen=parameters.period)

    def decide(self, context: StrategyContext):
        self.check(context)
        self.closes.append(context.close)
        if len(self.closes) < self.parameters.period:
            return None
        with localcontext() as precision:
            precision.prec = 60
            mean = sum(self.closes, Decimal(0)) / self.parameters.period
            variance = sum(((price - mean) ** 2 for price in self.closes), Decimal(0)) / self.parameters.period
            lower = mean - self.parameters.deviations * variance.sqrt()
            if variance > 0 and context.close < lower:
                self.target = 'LONG'
            elif context.close >= mean:
                self.target = 'FLAT'
        return self.signal(context)


class ChannelLongFlat(ClosedRule):
    def __init__(self, parameters):
        super().__init__(parameters)
        self.highs = deque(maxlen=parameters.entry)
        self.lows = deque(maxlen=parameters.exit)

    def decide(self, context: StrategyContext):
        if (context.high is None or context.low is None or
            not context.close.is_finite() or not context.high.is_finite() or not context.low.is_finite() or
            not 0 < context.low <= context.close <= context.high):
            raise ValueError('Matching finite positive high/low required')
        self.check(context)
        ready = len(self.highs) == self.parameters.entry
        # Thresholds exclude the decision bar: including it prevents a close breakout.
        if ready:
            if context.close > max(self.highs):
                self.target = 'LONG'
            elif context.close < min(self.lows):
                self.target = 'FLAT'
        self.highs.append(context.high)
        self.lows.append(context.low)
        return self.signal(context) if ready else None


class SeededEma:
    """SMA seed followed by alpha=2/(period+1), evaluated in the caller's Decimal context."""
    def __init__(self, period):
        self.period = period
        self.samples = 0
        self.total = Decimal(0)
        self.value = None

    def update(self, value):
        if self.value is None:
            self.samples += 1
            self.total += value
            if self.samples == self.period:
                self.value = self.total / self.period
        else:
            self.value += Decimal(2) / (self.period + 1) * (value - self.value)
        return self.value


class MacdLongFlat(ClosedRule):
    def __init__(self, parameters):
        super().__init__(parameters)
        self.fast = SeededEma(parameters.fast)
        self.slow = SeededEma(parameters.slow)
        self.signal_line = SeededEma(parameters.signal)

    def decide(self, context: StrategyContext):
        self.check(context)
        with localcontext() as precision:
            precision.prec = 60
            fast, slow = self.fast.update(context.close), self.slow.update(context.close)
            if fast is None or slow is None:
                return None
            macd = fast - slow
            signal = self.signal_line.update(macd)
            if signal is None:
                return None
            self.target = 'LONG' if macd > signal else 'FLAT'
        return self.signal(context)


class RocLongFlat(ClosedRule):
    def __init__(self, parameters):
        super().__init__(parameters)
        self.closes = deque(maxlen=parameters.period)

    def decide(self, context: StrategyContext):
        self.check(context)
        ready = len(self.closes) == self.parameters.period
        if ready:
            # Positive prices: sign of ROC equals sign of close-close[N], without division rounding.
            self.target = 'LONG' if context.close > self.closes[0] else 'FLAT'
        self.closes.append(context.close)
        return self.signal(context) if ready else None
