from datetime import datetime, timezone
from typing import Literal
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from sqlalchemy import select
from uuid import UUID
from core.storage.models import ResearchJobRecord as Job
from core.research import jobs, batches
from sqlalchemy.orm import Session
from core.models import Candle, Instrument
from core.storage.models import CandleRecord, InstrumentRecord
from core.exchange.binance import SYMBOLS
from core.backtest.spot import BacktestConfig, simulate
from core.strategy.registry import DEFINITIONS, resolve

class RunRequest(BaseModel):
    model_config=ConfigDict(extra='forbid')
    strategy: Literal['ema_long_flat_v1','sma_long_flat_v1']='ema_long_flat_v1'
    parameters: dict | None=None
    symbol: Literal['BTCUSDT','ETHUSDT']='BTCUSDT'
    timeframe: Literal['1m','5m','15m','1h','4h','1d']='1h'
    limit: int=Field(default=120,ge=2,le=1000,strict=True)
    as_of: datetime | None=None
    config: BacktestConfig=Field(default_factory=BacktestConfig)

    @model_validator(mode='after')
    def strategy_parameters(self):
        _,validated=resolve(self.strategy).create(self.parameters if self.parameters is not None else ({'period':self.config.period} if self.strategy=='ema_long_flat_v1' else {}))
        if self.strategy=='ema_long_flat_v1' and validated.period!=self.config.period:raise ValueError('EMA period must match config.period')
        self.parameters=validated.model_dump(mode='json')
        return self

    @field_validator('as_of')
    @classmethod
    def cutoff(cls,value):
        if value is not None and (value.tzinfo is None or value.utcoffset() is None): raise ValueError('as_of must be timezone-aware')
        if value is not None and value>datetime.now(timezone.utc): raise ValueError('Future as_of unavailable')
        return value


class HoldoutRequest(BaseModel):
    model_config=ConfigDict(extra='forbid')
    base: RunRequest
    train_bars: int=Field(default=60,ge=2,le=998,strict=True)


class Variant(BaseModel):
    model_config=ConfigDict(extra='forbid')
    strategy: Literal['ema_long_flat_v1','sma_long_flat_v1']
    parameters: dict

    @model_validator(mode='after')
    def normalized(self):
        _,validated=resolve(self.strategy).create(self.parameters)
        self.parameters=validated.model_dump(mode='json')
        return self


class BatchRequest(BaseModel):
    model_config=ConfigDict(extra='forbid')
    base: RunRequest
    variants: list[Variant]=Field(min_length=2,max_length=8)

    @model_validator(mode='after')
    def unique(self):
        from core.backtest.spot import digest
        signatures=[digest(variant.model_dump(mode='json')) for variant in self.variants]
        if len(set(signatures))!=len(signatures):raise ValueError('Duplicate variants unavailable')
        return self


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
        try: return simulate(bars,instrument,request.config,strategy_id=request.strategy,parameters=request.parameters)
        except ValueError as error: raise HTTPException(409,str(error))
    @router.get('/api/v1/research/status')
    def worker_status():
        try:ready=jobs.healthy(engine)
        except Exception:ready=False
        return {'status':'healthy' if ready else 'unavailable','trading_enabled':False}

    @router.get('/api/v1/research/strategies')
    def strategies():
        return [{**definition.describe(),'config_schema':BacktestConfig.model_json_schema()}
                for definition in DEFINITIONS.values() if definition.backtest_available]

    @router.post('/api/v1/research/jobs',status_code=202)
    def submit(request:RunRequest):
        bars,instrument,frozen=prepare(engine,request)
        try:return jobs.enqueue(engine,frozen,bars,instrument)
        except jobs.QueueFull:raise HTTPException(429,'Research queue full (20 active tasks)')
        except ValueError as error:raise HTTPException(409,str(error))

    @router.post('/api/v1/research/holdouts',status_code=202)
    def submit_holdout(request:HoldoutRequest):
        from core.backtest.holdout import validate_split
        bars,instrument,frozen=prepare(engine,request.base)
        try:
            validate_split(bars,request.train_bars,request.base.strategy,request.base.parameters,request.base.config)
            return jobs.enqueue(engine,{**frozen,'research_type':'holdout','train_bars':request.train_bars},bars,instrument)
        except jobs.QueueFull:raise HTTPException(429,'Research queue full (20 active tasks)')
        except ValueError as error:raise HTTPException(409,str(error))

    @router.post('/api/v1/research/batches',status_code=202)
    def submit_batch(request:BatchRequest):
        bars,instrument,base=prepare(engine,request.base)
        requests=[]
        for variant in request.variants:
            config=dict(base['config'])
            if variant.strategy=='ema_long_flat_v1':config['period']=variant.parameters['period']
            frozen=RunRequest.model_validate({**base,**variant.model_dump(mode='json'),'config':config}).model_dump(mode='json')
            requests.append(frozen)
        try:return batches.enqueue(engine,requests,bars,instrument)
        except jobs.QueueFull:raise HTTPException(429,'Research queue full; batch was not submitted')
        except ValueError as error:raise HTTPException(409,str(error))

    @router.get('/api/v1/research/batches/{batch_id}')
    def get_batch(batch_id:UUID):
        try:return batches.read(engine,str(batch_id))
        except KeyError:raise HTTPException(404,'Batch not found')

    @router.post('/api/v1/research/batches/{batch_id}/cancel')
    def cancel_batch(batch_id:UUID):
        try:return batches.cancel(engine,str(batch_id))
        except KeyError:raise HTTPException(404,'Batch not found')

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
