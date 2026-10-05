from datetime import datetime, timezone, timedelta
from decimal import Decimal
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

class Instrument(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    instrument_id: str = Field(min_length=1)
    exchange: str = Field(min_length=1)
    native_symbol: str = Field(min_length=1)
    base: str = Field(min_length=1)
    quote: str = Field(min_length=1)
    market_type: Literal["SPOT", "PERPETUAL"]
    settlement_currency: str | None = None
    tick_size: Decimal = Field(gt=0, max_digits=38, decimal_places=18)
    quantity_step: Decimal = Field(gt=0, max_digits=38, decimal_places=18)
    min_quantity: Decimal = Field(ge=0, max_digits=38, decimal_places=18)
    min_notional: Decimal = Field(ge=0, max_digits=38, decimal_places=18)
    contract_size: Decimal = Field(default=Decimal("1"), gt=0, max_digits=38, decimal_places=18)
    contract_type: str | None = None

    @field_validator("tick_size", "quantity_step", "min_quantity", "min_notional", "contract_size", mode="before")
    @classmethod
    def exact_numbers(cls, value):
        if isinstance(value, (float, bool)):
            raise ValueError("Use Decimal, integer or decimal string, not binary floats")
        return value

    @model_validator(mode="after")
    def validate_contract(self):
        if self.market_type == "PERPETUAL" and (not self.settlement_currency or not self.contract_type):
            raise ValueError("Perpetual instruments require settlement currency and contract type")
        return self

class Candle(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    instrument_id: str = Field(min_length=1)
    timeframe: Literal["1m", "5m", "15m", "1h", "4h", "1d"]
    open_time: datetime
    close_time: datetime
    open: Decimal = Field(gt=0, max_digits=38, decimal_places=18)
    high: Decimal = Field(gt=0, max_digits=38, decimal_places=18)
    low: Decimal = Field(gt=0, max_digits=38, decimal_places=18)
    close: Decimal = Field(gt=0, max_digits=38, decimal_places=18)
    volume: Decimal = Field(ge=0, max_digits=38, decimal_places=18)
    is_closed: bool
    source: str = Field(min_length=1)

    @field_validator("open", "high", "low", "close", "volume", mode="before")
    @classmethod
    def exact_numbers(cls, value):
        if isinstance(value, (float, bool)):
            raise ValueError("Use Decimal, integer or decimal string, not binary floats")
        return value

    @field_validator("open_time", "close_time")
    @classmethod
    def utc_time(cls, value):
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("Timezone-aware timestamps required")
        return value.astimezone(timezone.utc)

    @model_validator(mode="after")
    def validate_bar(self):
        if self.close_time <= self.open_time:
            raise ValueError("close_time must follow open_time")
        seconds={"1m":60,"5m":300,"15m":900,"1h":3600,"4h":14400,"1d":86400}[self.timeframe]
        if self.close_time-self.open_time != timedelta(seconds=seconds) or self.open_time.microsecond or int(self.open_time.timestamp()) % seconds:
            raise ValueError("Candle must use aligned UTC interval and exclusive close_time")
        if not self.low <= min(self.open, self.close) <= max(self.open, self.close) <= self.high:
            raise ValueError("Invalid OHLC bounds")
        return self
