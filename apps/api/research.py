from datetime import datetime, timezone
from typing import Literal
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import select
from sqlalchemy.orm import Session
from core.models import Candle, Instrument
from core.storage.models import CandleRecord, InstrumentRecord
from core.exchange.binance import SYMBOLS
from core.backtest.spot import BacktestConfig, simulate

class RunRequest(BaseModel):
    model_config=ConfigDict(extra='forbid')
    symbol: Literal['BTCUSDT','ETHUSDT']='BTCUSDT'
    timeframe: Literal['1m','5m','15m','1h','4h','1d']='1h'
    limit: int=Field(default=120,ge=2,le=1000,strict=True)
    as_of: datetime | None=None
    config: BacktestConfig=Field(default_factory=BacktestConfig)

    @field_validator('as_of')
    @classmethod
    def cutoff(cls,value):
        if value is not None and (value.tzinfo is None or value.utcoffset() is None): raise ValueError('as_of must be timezone-aware')
        if value is not None and value>datetime.now(timezone.utc): raise ValueError('Future as_of unavailable')
        return value


def router_for(engine):
    router=APIRouter()
    @router.post('/api/v1/research/backtest')
    def backtest(request:RunRequest):
        cutoff=request.as_of or datetime.now(timezone.utc)
        with Session(engine) as session:
            record=session.get(InstrumentRecord,SYMBOLS[request.symbol])
            if record is None: raise HTTPException(409,'Instrument rules unavailable')
            instrument=Instrument.model_validate(record,from_attributes=True)
            query=select(CandleRecord).where(CandleRecord.instrument_id==instrument.instrument_id,CandleRecord.timeframe==request.timeframe,CandleRecord.is_closed.is_(True),CandleRecord.close_time<=cutoff).order_by(CandleRecord.open_time.desc()).limit(request.limit)
            bars=[Candle.model_validate(b,from_attributes=True) for b in reversed(list(session.scalars(query)))]
        try: return simulate(bars,instrument,request.config)
        except ValueError as error: raise HTTPException(409,str(error))
    return router
