from datetime import datetime, timezone
from typing import Literal
from fastapi import APIRouter, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from sqlalchemy import select
from uuid import UUID
from core.storage.models import ResearchJobRecord as Job
from core.research import jobs, batches, matrices
from core.research.limits import MAX_BARS
from sqlalchemy.orm import Session
from core.models import Candle, Instrument
from core.storage.models import CandleRecord, InstrumentRecord
from core.exchange.public import SYMBOLS, INTERVALS
from core.backtest.spot import BacktestConfig, simulate
from core.strategy.registry import DEFINITIONS, resolve

class RunRequest(BaseModel):
    model_config=ConfigDict(extra='forbid')
    strategy: str='ema_long_flat_v1'
    parameters: dict | None=None
    symbol: Literal[tuple(SYMBOLS)]='BTCUSDT'
    timeframe: Literal['1m','5m','15m','1h','4h','1d']='1h'
    limit: int=Field(default=120,ge=2,le=MAX_BARS,strict=True)
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
    train_bars: int=Field(default=60,ge=2,le=MAX_BARS-2,strict=True)


class Variant(BaseModel):
    model_config=ConfigDict(extra='forbid')
    strategy: str
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


class MatrixRequest(BaseModel):
    model_config=ConfigDict(extra='forbid')
    request_id: UUID
    symbols: list[Literal[tuple(SYMBOLS)]]=Field(min_length=1,max_length=len(SYMBOLS))
    timeframes: list[Literal['1m','5m','15m','1h','4h','1d']]=Field(min_length=1,max_length=6)
    variants: list[Variant]=Field(min_length=1,max_length=8)
    start_at: datetime
    as_of: datetime
    config: BacktestConfig=Field(default_factory=BacktestConfig)

    @model_validator(mode='after')
    def bounded(self):
        from core.backtest.spot import digest
        if any(value.tzinfo is None or value.utcoffset() is None for value in (self.start_at,self.as_of)):
            raise ValueError('Timezone-aware window required')
        if not self.start_at<self.as_of<=datetime.now(timezone.utc):raise ValueError('Past ordered window required')
        if self.start_at.microsecond or self.as_of.microsecond:raise ValueError('Exact candle boundaries required')
        if len(set(self.symbols))!=len(self.symbols) or len(set(self.timeframes))!=len(self.timeframes):raise ValueError('Duplicate market selection')
        if len({digest(v.model_dump(mode='json')) for v in self.variants})!=len(self.variants):raise ValueError('Duplicate strategy variants')
        if len(self.symbols)*len(self.timeframes)*len(self.variants)>20:raise ValueError('Matrix exceeds 20 tasks')
        total=0
        for timeframe in self.timeframes:
            step=INTERVALS[timeframe]
            start,end=int(self.start_at.timestamp()*1000),int(self.as_of.timestamp()*1000)
            if start%step or end%step:raise ValueError('Window must align to every selected candle boundary')
            count=(end-start)//step
            if not 2<=count<=MAX_BARS:raise ValueError('Every market window requires 2..5000 bars')
            from core.strategy.registry import lookback
            if any(count<lookback(v.strategy,v.parameters)+1 for v in self.variants):raise ValueError('Window needs strategy lookback plus next-open bar')
            total+=count*len(self.symbols)*len(self.variants)
        if total>20000:raise ValueError('Matrix exceeds 20000 total bars; reduce scope')
        return self


def prepare_matrix(engine,request):
    from core.backtest.spot import digest
    scope=request.model_dump(mode='json');request_hash=digest(scope);matrix_id=str(request.request_id)
    try:
        existing=matrices.read(engine,matrix_id)
        if existing['request_sha256']!=request_hash:raise ValueError('Matrix request ID already used with different inputs')
        return existing
    except KeyError:pass
    inputs=[]
    with Session(engine) as session:
        session.connection(execution_options={'isolation_level':'REPEATABLE READ'})
        for symbol in request.symbols:
            record=session.get(InstrumentRecord,SYMBOLS[symbol])
            if record is None:raise ValueError(f'{symbol}: instrument unavailable')
            instrument=Instrument.model_validate(record,from_attributes=True)
            for timeframe in request.timeframes:
                expected=int((request.as_of-request.start_at).total_seconds()*1000)//INTERVALS[timeframe]
                query=select(CandleRecord).where(CandleRecord.instrument_id==instrument.instrument_id,CandleRecord.timeframe==timeframe,
                    CandleRecord.is_closed.is_(True),CandleRecord.open_time>=request.start_at,CandleRecord.close_time<=request.as_of).order_by(CandleRecord.open_time).limit(MAX_BARS+1)
                bars=[Candle.model_validate(row,from_attributes=True) for row in session.scalars(query)]
                if len(bars)!=expected or bars[0].open_time!=request.start_at or bars[-1].close_time!=request.as_of:
                    raise ValueError(f'{symbol} {timeframe}: complete history required ({len(bars)}/{expected}); window was not shortened')
                dataset_hash=digest([bar.model_dump(mode='json') for bar in bars])
                for variant in request.variants:
                    config=request.config.model_dump(mode='json')
                    if variant.strategy=='ema_long_flat_v1':config['period']=variant.parameters['period']
                    frozen=RunRequest(symbol=symbol,timeframe=timeframe,limit=expected,as_of=request.as_of,
                        strategy=variant.strategy,parameters=variant.parameters,config=config).model_dump(mode='json')
                    inputs.append(({**frozen,'matrix_id':matrix_id,'matrix_slot':len(inputs),'matrix_scope':scope,
                        'matrix_request_sha256':request_hash,'window_start':request.start_at.isoformat(),'dataset_sha256':dataset_hash},bars,instrument))
    jobs.enqueue_prepared(engine,inputs)
    return matrices.read(engine,matrix_id)


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

class WalkForwardRequest(RunRequest):
    grid:list[dict]=Field(min_length=1,max_length=8)
    train_bars:int=Field(ge=3,le=MAX_BARS,strict=True)
    test_bars:int=Field(ge=3,le=MAX_BARS,strict=True)


def router_for(engine):
    router=APIRouter()
    @router.post('/api/v1/research/matrices',status_code=202)
    def submit_matrix(request:MatrixRequest):
        try:return prepare_matrix(engine,request)
        except jobs.QueueFull:raise HTTPException(429,'Research queue full; matrix was not submitted')
        except ValueError as error:raise HTTPException(409,str(error))

    @router.get('/api/v1/research/matrices/{matrix_id}')
    def get_matrix(matrix_id:UUID):
        try:return matrices.read(engine,str(matrix_id))
        except KeyError:raise HTTPException(404,'Matrix not found')

    @router.post('/api/v1/research/matrices/{matrix_id}/cancel')
    def cancel_matrix(matrix_id:UUID):
        try:return matrices.cancel(engine,str(matrix_id))
        except KeyError:raise HTTPException(404,'Matrix not found')
    @router.post('/api/v1/research/backtest')
    def backtest(request:RunRequest):
        bars,instrument,_=prepare(engine,request)
        try: return simulate(bars,instrument,request.config,strategy_id=request.strategy,parameters=request.parameters)
        except ValueError as error: raise HTTPException(409,str(error))
    @router.post('/api/v1/research/walk-forward')
    def walk_forward(request:WalkForwardRequest):
        from core.backtest.walk_forward import simulate as walk
        bars,instrument,_=prepare(engine,request)
        try:result=walk(bars,instrument,request.config,strategy_id=request.strategy,grid=request.grid,train_bars=request.train_bars,test_bars=request.test_bars)
        except (ValueError,ArithmeticError):raise HTTPException(409,'Walk-forward input cannot be verified')
        from fastapi.responses import JSONResponse
        return JSONResponse(result,headers={'Cache-Control':'no-store'})

    @router.post('/api/v1/research/walk-forwards',status_code=202)
    def submit_walk_forward(request:WalkForwardRequest):
        from core.backtest.walk_forward import validate
        bars,instrument,frozen=prepare(engine,request)
        try:
            validate(bars,instrument,request.strategy,request.grid,request.train_bars,request.test_bars)
            return jobs.enqueue(engine,{**frozen,'research_type':'walk_forward'},bars,instrument)
        except jobs.QueueFull:raise HTTPException(429,'Research queue full (20 active tasks)')
        except (ValueError,ArithmeticError):raise HTTPException(409,'Walk-forward windows or candidates unavailable')

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

    @router.post('/api/v1/research/jobs/{job_id}/verify')
    def verify_job(job_id:UUID):
        from core.research.verification import verify
        with Session(engine) as session:
            job=session.get(Job,str(job_id))
            if job is None:raise HTTPException(404,'Task not found')
            if job.status!='SUCCEEDED':raise HTTPException(409,'Task has no successful result')
            request,snapshot,result=job.request,job.snapshot,job.result
        # Release the database session before bounded CPU replay. No state is written.
        try:verified=verify(request,snapshot,result)
        except (ValueError,KeyError,TypeError):raise HTTPException(409,'Retained research verification failed')
        return JSONResponse(verified,headers={'Cache-Control':'no-store'})
    return router
