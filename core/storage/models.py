from datetime import datetime
from decimal import Decimal
from sqlalchemy import Boolean, CheckConstraint, DateTime, ForeignKey, Index, Numeric, String, UniqueConstraint
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

class Base(DeclarativeBase):
    pass

class InstrumentRecord(Base):
    __tablename__ = "instruments"
    instrument_id: Mapped[str] = mapped_column(String(160), primary_key=True)
    exchange: Mapped[str] = mapped_column(String(32))
    native_symbol: Mapped[str] = mapped_column(String(64))
    base: Mapped[str] = mapped_column(String(32))
    quote: Mapped[str] = mapped_column(String(32))
    market_type: Mapped[str] = mapped_column(String(16))
    contract_type: Mapped[str | None] = mapped_column(String(32))
    settlement_currency: Mapped[str | None] = mapped_column(String(32))
    tick_size: Mapped[Decimal] = mapped_column(Numeric(38,18))
    quantity_step: Mapped[Decimal] = mapped_column(Numeric(38,18))
    min_quantity: Mapped[Decimal] = mapped_column(Numeric(38,18))
    min_notional: Mapped[Decimal] = mapped_column(Numeric(38,18))
    contract_size: Mapped[Decimal] = mapped_column(Numeric(38,18))
    __table_args__ = (
        UniqueConstraint("exchange", "native_symbol", "market_type", name="uq_instrument_native"),
        CheckConstraint("market_type IN ('SPOT','PERPETUAL')", name="ck_market_type"),
        CheckConstraint("tick_size > 0 AND quantity_step > 0 AND min_quantity >= 0 AND min_notional >= 0 AND contract_size > 0", name="ck_instrument_rules"),
        CheckConstraint("market_type <> 'PERPETUAL' OR (settlement_currency IS NOT NULL AND contract_type IS NOT NULL)", name="ck_contract"),
    )

class CandleRecord(Base):
    __tablename__ = "candles"
    instrument_id: Mapped[str] = mapped_column(ForeignKey("instruments.instrument_id"), primary_key=True)
    timeframe: Mapped[str] = mapped_column(String(4), primary_key=True)
    open_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), primary_key=True)
    close_time: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    open: Mapped[Decimal] = mapped_column(Numeric(38,18))
    high: Mapped[Decimal] = mapped_column(Numeric(38,18))
    low: Mapped[Decimal] = mapped_column(Numeric(38,18))
    close: Mapped[Decimal] = mapped_column(Numeric(38,18))
    volume: Mapped[Decimal] = mapped_column(Numeric(38,18))
    is_closed: Mapped[bool] = mapped_column(Boolean)
    source: Mapped[str] = mapped_column(String(64))
    __table_args__ = (
        CheckConstraint("low > 0 AND low <= open AND low <= close AND high >= open AND high >= close AND volume >= 0", name="ck_candle_values"),
        CheckConstraint("close_time > open_time", name="ck_candle_time"),
        CheckConstraint("extract(epoch from close_time-open_time) = CASE timeframe WHEN '1m' THEN 60 WHEN '5m' THEN 300 WHEN '15m' THEN 900 WHEN '1h' THEN 3600 WHEN '4h' THEN 14400 WHEN '1d' THEN 86400 END AND mod(extract(epoch from open_time), CASE timeframe WHEN '1m' THEN 60 WHEN '5m' THEN 300 WHEN '15m' THEN 900 WHEN '1h' THEN 3600 WHEN '4h' THEN 14400 WHEN '1d' THEN 86400 END) = 0", name="ck_candle_interval"),
        CheckConstraint("timeframe IN ('1m','5m','15m','1h','4h','1d')", name="ck_timeframe"),
    )

# TimescaleDB creates this descending time index for the hypertable.
Index("candles_open_time_idx", CandleRecord.open_time.desc())
