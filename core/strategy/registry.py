"""Built-in research definitions. Availability is separate from registration."""
from collections import deque
from dataclasses import dataclass
from decimal import Decimal, localcontext
from types import MappingProxyType

from pydantic import BaseModel, ConfigDict, Field, model_validator

from core.strategy.contracts import EmaLongFlat, StrategyContext, TargetSignal


class EmaParameters(BaseModel):
    model_config = ConfigDict(frozen=True, extra='forbid')
    period: int = Field(default=20, ge=2, le=500, strict=True)


class SmaParameters(BaseModel):
    model_config = ConfigDict(frozen=True, extra='forbid')
    fast: int = Field(default=10, ge=2, le=500, strict=True)
    slow: int = Field(default=20, ge=2, le=500, strict=True)

    @model_validator(mode='after')
    def ordered(self):
        if self.fast >= self.slow:
            raise ValueError('fast must be smaller than slow')
        return self


class SmaLongFlat:
    """Finalized-close fast SMA above slow SMA targets LONG, equality targets FLAT.

    Caller supplies chronological closed-bar contexts, as with the EMA contract.
    Decisions become available at that close; this class never executes orders.
    """
    def __init__(self, parameters: SmaParameters):
        self.parameters = parameters
        self._closes = deque(maxlen=parameters.slow)
        self._as_of = None

    def decide(self, context: StrategyContext) -> TargetSignal | None:
        if context.as_of.tzinfo is None or context.as_of.utcoffset() is None:
            raise ValueError('Timezone-aware decision time required')
        if self._as_of is not None and context.as_of <= self._as_of:
            raise ValueError('Strictly increasing decision times required')
        if not context.close.is_finite() or context.close <= 0:
            raise ValueError('Finite positive close required')
        self._as_of = context.as_of
        self._closes.append(context.close)
        if len(self._closes) < self.parameters.slow:
            return None
        # Compare cross-multiplied sums: no rounded division at the boundary.
        with localcontext() as precision:
            precision.prec = 60
            fast_sum = sum(list(self._closes)[-self.parameters.fast:], Decimal(0))
            slow_sum = sum(self._closes, Decimal(0))
            target = 'LONG' if fast_sum * self.parameters.slow > slow_sum * self.parameters.fast else 'FLAT'
        return TargetSignal(target, context.as_of)


@dataclass(frozen=True)
class Definition:
    strategy: str
    version: int
    description: str
    parameters: type[BaseModel]
    factory: object
    backtest_available: bool

    def create(self, parameters: dict):
        validated = self.parameters.model_validate(parameters)
        # EMA receives its indicator through StrategyContext, preserving v1 semantics.
        return (self.factory() if self.strategy == 'ema_long_flat_v1' else self.factory(validated)), validated

    def describe(self):
        return {'strategy': self.strategy, 'version': self.version,
                'description': self.description,
                'parameter_schema': self.parameters.model_json_schema(),
                'backtest_available': self.backtest_available}


DEFINITIONS = MappingProxyType({
    'ema_long_flat_v1': Definition('ema_long_flat_v1', 1,
        'Finalized close above SMA-seeded EMA -> LONG; otherwise FLAT; warmup -> no signal',
        EmaParameters, EmaLongFlat, True),
    'sma_long_flat_v1': Definition('sma_long_flat_v1', 1,
        'Finalized fast SMA above slow SMA -> LONG; otherwise FLAT; slow-window warmup -> no signal',
        SmaParameters, SmaLongFlat, False),
})


def resolve(strategy: str) -> Definition:
    try:
        return DEFINITIONS[strategy]
    except KeyError:
        raise ValueError('Unknown built-in strategy version') from None
