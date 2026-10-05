from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Literal, Protocol

@dataclass(frozen=True)
class StrategyContext:
    as_of: datetime
    close: Decimal
    ema: float | None

@dataclass(frozen=True)
class TargetSignal:
    target: Literal['LONG', 'FLAT']
    available_at: datetime

class Strategy(Protocol):
    def decide(self, context: StrategyContext) -> TargetSignal | None: ...

class EmaLongFlat:
    """Research baseline, not a profitability claim or trading recommendation."""
    def decide(self, context):
        if context.ema is None: return None
        return TargetSignal('LONG' if context.close > Decimal(str(context.ema)) else 'FLAT', context.as_of)
