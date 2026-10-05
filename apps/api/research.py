from datetime import datetime, timezone
from typing import Literal
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import select
from uuid import UUID
from core.storage.models import ResearchJobRecord as Job
from core.research import jobs
from sqlalchemy.orm import Session
from core.models import Candle, Instrument
from core.storage.models import CandleRecord, InstrumentRecord
from core.exchange.binance import SYMBOLS
from core.backtest.spot import BacktestConfig, simulate

class RunRequest(BaseModel):
    model_config=ConfigDict(extra='forbid')
    strategy: Literal['ema_long_flat_v1']='ema_long_flat_v1'
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


def prepare(engine,request):
    cutoff=request.as_of or datetime.now(timezone.utc)
    with Session(engine) as session:
        record=session.get(InstrumentRecord,SYMBOLS[request.symbol])
        if record is None: raise HTTPException(409,'Instrument rules unavailable')
        instrument=Instrument.model_validate(record,from_attributes=True)
        query=select(CandleRecord).where(CandleRecord.instrument_id==instrument.instrument_id,CandleRecord.timeframe==request.timeframe,CandleRecord.is_closed.is_(True),CandleRecord.close_time<=cutoff).order_by(CandleRecord.open_time.desc()).limit(request.limit)
        bars=[Candle.model_validate(b,from_attributes=True) for b in reversed(list(session.scalars(query)))]
    frozen=request.model_dump(mode='json');frozen['as_of']=cutoff.isoformat()
    return bars,instrument,frozen

def router_for(engine):
    router=APIRouter()
    @router.post('/api/v1/research/backtest')
    def backtest(request:RunRequest):
        bars,instrument,_=prepare(engine,request)
        try: return simulate(bars,instrument,request.config)
        except ValueError as error: raise HTTPException(409,str(error))
    @router.get('/api/v1/research/status')
    def worker_status():
        try:ready=jobs.healthy(engine)
        except Exception:ready=False
        return {'status':'healthy' if ready else 'unavailable','trading_enabled':False}

    @router.get('/api/v1/research/strategies')
    def strategies():
        return [{'strategy':'ema_long_flat_v1','version':1,'description':'Finalized close above SMA-seeded EMA -> LONG; otherwise FLAT; warmup -> no signal','config_schema':BacktestConfig.model_json_schema()}]

    @router.post('/api/v1/research/jobs',status_code=202)
    def submit(request:RunRequest):
        bars,instrument,frozen=prepare(engine,request)
        try:return jobs.enqueue(engine,frozen,bars,instrument)
        except jobs.QueueFull:raise HTTPException(429,'Research queue full (20 active tasks)')
        except ValueError as error:raise HTTPException(409,str(error))

    @router.get('/api/v1/research/jobs')
    def list_jobs():
        with Session(engine) as session:return [jobs.view(job) for job in session.scalars(select(Job).order_by(Job.created_at.desc()).limit(20))]

    @router.get('/api/v1/research/jobs/{job_id}')
    def get_job(job_id:UUID):
        with Session(engine) as session:
            job=session.get(Job,str(job_id))
            if job is None:raise HTTPException(404,'Task not found')
            return jobs.view(job)

    @router.get('/api/v1/research/jobs/{job_id}/result')
    def get_result(job_id:UUID):
        with Session(engine) as session:
            job=session.get(Job,str(job_id))
            if job is None:raise HTTPException(404,'Task not found')
            if job.status!='SUCCEEDED':raise HTTPException(409,'Task has no successful result')
            return job.result

    @router.post('/api/v1/research/jobs/{job_id}/cancel')
    def cancel_job(job_id:UUID):
        try:return jobs.cancel(engine,str(job_id))
        except KeyError:raise HTTPException(404,'Task not found')
        except ValueError as error:raise HTTPException(409,str(error))
    return router
