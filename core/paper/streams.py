"""Append-only closed-bar acceptance with transactional simulation and correction veto."""
from datetime import datetime, timezone, timedelta
from uuid import uuid4
from core.paper.accounts import record_control, state
from sqlalchemy import select, text, func
from sqlalchemy.orm import Session
from core.backtest.spot import digest, BacktestConfig
from core.models import Candle, Instrument
from core.replay.sessions import validate, Conflict
from core.paper.ledger import RiskLimits
from core.paper import intents
from core.paper.stream_ledger import VERSION, evaluate
from core.storage.models import PaperStreamRecord as Stream, PaperWorkerRecord as Worker, CandleRecord, InstrumentRecord

ACTIVE_LIMIT=20
class Capacity(Exception): pass


def now(): return datetime.now(timezone.utc)


def inputs(record):
    snapshot=record.snapshot
    if snapshot['version']!=VERSION or snapshot['sha256']!=digest({k:v for k,v in snapshot.items() if k!='sha256'}):
        raise ValueError('Paper stream snapshot integrity failed')
    seed=[Candle.model_validate(b) for b in snapshot['dataset']]
    instrument=Instrument.model_validate(snapshot['instrument']);validate(seed,instrument)
    observations=record.observations
    if len(seed)+len(observations)>1000: raise ValueError('Paper stream bar limit exceeded')
    bars=list(seed)
    previous_observed=None
    for row in observations:
        if set(row)!={'candle','observed_at','gate'} or row['gate'] not in ['ELIGIBLE','STALE_BAR','PAUSED','PRE_ACTIVATION']:
            raise ValueError('Invalid paper observation')
        observed=datetime.fromisoformat(row['observed_at'])
        bar=Candle.model_validate(row['candle'])
        if observed.tzinfo is None or observed<bar.close_time or (previous_observed is not None and observed<previous_observed):
            raise ValueError('Invalid paper observation time')
        activation=datetime.fromisoformat(snapshot['request']['as_of'])
        if (bar.close_time<=activation)!=(row['gate']=='PRE_ACTIVATION'):
            raise ValueError('Pre-activation bar cannot trade')
        if row['gate']=='ELIGIBLE' and observed-bar.close_time>timedelta(seconds=snapshot['request']['max_lag_seconds']):
            raise ValueError('Late execution cannot be eligible')
        previous_observed=observed;bars.append(bar)
    validate(bars,instrument)
    return seed,observations,instrument,BacktestConfig.model_validate(snapshot['request']['config']),RiskLimits.model_validate(snapshot['request']['risk'])


def computed(record):
    seed,observations,instrument,config,limits=inputs(record);request=record.snapshot['request']
    return evaluate(seed,observations,instrument,config,limits,strategy_id=request['strategy'],parameters=request['parameters'],snapshot_sha256=record.snapshot['sha256'],halt_at=record.halt_at)


def visible(record):
    ledger=computed(record)
    if ledger!=record.ledger:raise ValueError('Paper stream ledger integrity failed')
    snapshot=record.snapshot;request=snapshot['request'];seed=snapshot['dataset']
    clock=record.observations[-1]['candle']['close_time'] if record.observations else seed[-1]['close_time']
    return {'session_id':record.session_id,'revision':record.revision,'status':record.status,'clock':clock,
            'seed_bars':len(seed),'accepted_bars':len(record.observations),'bar_limit':1000,
            'manifest':{'version':VERSION,'snapshot_sha256':snapshot['sha256'],'request':request,'instrument':snapshot['instrument']},
            'seed':seed,'observations':record.observations,'feed':record.feed,**ledger,'mode':'REALTIME_CLOSED_BAR_PAPER','trading_enabled':False}


def create(engine,request,bars,instrument):
    validate(bars,instrument)
    if not 2<=len(bars)<=500:raise ValueError('Require 2..500 warmup bars')
    activation=datetime.fromisoformat(request['as_of'])
    if activation.tzinfo is None or bars[-1].close_time>activation:raise ValueError('Warmup must precede activation')
    snapshot={'version':VERSION,'request':request,'dataset':[bar.model_dump(mode='json') for bar in bars],'instrument':instrument.model_dump(mode='json')}
    snapshot['sha256']=digest(snapshot)
    with Session(engine) as session,session.begin():
        session.execute(text('SELECT pg_advisory_xact_lock(6660801)'))
        if session.scalar(select(func.count()).select_from(Stream).where(Stream.status.in_(['RUNNING','PAUSED'])))>=ACTIVE_LIMIT:raise Capacity()
        record=Stream(session_id=str(uuid4()),created_at=activation,updated_at=activation,snapshot=snapshot,observations=[],revision=0,status='RUNNING',halt_at=None,feed={'state':'WAITING','expected_open':bars[-1].close_time.isoformat(),'checked_at':None})
        record.intent_version=intents.VERSION
        record.ledger=computed(record);response=visible(record);session.add(record);return response


def read(engine,session_id):
    with Session(engine) as session:
        record=session.get(Stream,session_id)
        if record is None:raise KeyError(session_id)
        return visible(record)


def command(engine,session_id,expected_revision,action):
    if isinstance(expected_revision,bool) or not isinstance(expected_revision,int) or expected_revision<0 or action not in ['pause','resume','halt','stop']:
        raise ValueError('Require revision and pause/resume/halt/stop')
    with Session(engine) as session,session.begin():
        record=session.get(Stream,session_id,with_for_update=True)
        if record is None:raise KeyError(session_id)
        before_revision = record.revision
        before_state = state(record, 'streams')
        conflict = record.revision != expected_revision
        if not conflict:
            visible(record);intents.synchronize(session,record);before=(record.status,record.halt_at)
            if action=='stop':record.status='STOPPED'
            elif action=='halt' and record.status in ['RUNNING','PAUSED']:
                if record.halt_at is None:record.halt_at=len(record.observations)
            elif action=='pause' and record.status=='RUNNING':record.status='PAUSED'
            elif action=='resume' and record.status=='PAUSED':record.status='RUNNING'
            elif action=='resume' and record.status!='RUNNING':raise ValueError('Terminal account cannot resume; create a new account')
            if before!=(record.status,record.halt_at):
                record.ledger=computed(record);record.revision+=1;record.updated_at=now()
            intents.synchronize(session,record)
            response = visible(record)
        record_control(session, record, 'streams', action, 1, expected_revision, before_revision, before_state)
    if conflict: raise Conflict()
    return response


def advance(engine,session_id,*,observed_at=None):
    stamp=observed_at or now()
    if stamp.tzinfo is None:raise ValueError('Timezone required')
    with Session(engine) as session,session.begin():
        record=session.scalar(select(Stream).where(Stream.session_id==session_id).with_for_update(skip_locked=True))
        if record is None or record.status not in ['RUNNING','PAUSED']:return False
        # This verifies durable state before any mutation; failure rolls back the account.
        visible(record)
        intents.synchronize(session,record)
        seed,observations,instrument,_,_=inputs(record)
        accepted=[*seed,*[Candle.model_validate(row['candle']) for row in observations]]
        expected=accepted[-1].close_time
        # Share locks make this check and new-bar acceptance coherent with corrections.
        rules=session.get(InstrumentRecord,instrument.instrument_id,with_for_update={'read':True})
        current=list(session.scalars(select(CandleRecord).where(CandleRecord.instrument_id==instrument.instrument_id,CandleRecord.timeframe==accepted[0].timeframe,CandleRecord.open_time>=accepted[0].open_time,CandleRecord.open_time<=accepted[-1].open_time).order_by(CandleRecord.open_time).with_for_update(read=True)))
        failure=None
        if rules is None or Instrument.model_validate(rules,from_attributes=True)!=instrument:failure='RULES_CHANGED'
        elif len(current)!=len(accepted):failure='DATA_MISSING'
        elif any(Candle.model_validate(a,from_attributes=True)!=b for a,b in zip(current,accepted)):failure='DATA_REVISED'
        if failure:
            record.status='BLOCKED';record.feed={'state':failure,'expected_open':expected.isoformat(),'checked_at':stamp.isoformat()}
            record.revision+=1;record.updated_at=stamp;return False
        new=list(session.scalars(select(CandleRecord).where(CandleRecord.instrument_id==instrument.instrument_id,CandleRecord.timeframe==accepted[0].timeframe,CandleRecord.open_time>=expected,CandleRecord.close_time<=stamp,CandleRecord.is_closed.is_(True)).order_by(CandleRecord.open_time).limit(min(10,1000-len(accepted))).with_for_update(read=True)))
        added=[];gap=False
        for row in new:
            bar=Candle.model_validate(row,from_attributes=True)
            if bar.open_time!=expected:gap=True;break
            gate='PRE_ACTIVATION' if bar.close_time<=datetime.fromisoformat(record.snapshot['request']['as_of']) else 'PAUSED' if record.status=='PAUSED' else 'STALE_BAR' if stamp-bar.close_time>timedelta(seconds=record.snapshot['request']['max_lag_seconds']) else 'ELIGIBLE'
            added.append({'candle':bar.model_dump(mode='json'),'observed_at':stamp.isoformat(),'gate':gate});expected=bar.close_time
        if added:
            record.observations=[*observations,*added];record.ledger=computed(record);record.revision+=1;record.updated_at=stamp
            intents.synchronize(session,record,new=True)
        if len(accepted)+len(added)==1000:
            record.status='LIMIT_REACHED'
        overdue=stamp>expected+(accepted[-1].close_time-accepted[-1].open_time)+timedelta(seconds=record.snapshot['request']['max_lag_seconds'])
        state='GAP' if gap else 'STALE' if overdue and not new else 'CURRENT' if added else 'WAITING'
        record.feed={'state':state,'expected_open':expected.isoformat(),'checked_at':stamp.isoformat()}
        return bool(added)


def active(engine):
    with Session(engine) as session:return list(session.scalars(select(Stream.session_id).where(Stream.status.in_(['RUNNING','PAUSED'])).order_by(Stream.created_at).limit(ACTIVE_LIMIT)))


def heartbeat(engine,worker_id):
    with Session(engine) as session,session.begin():
        record=session.get(Worker,worker_id)
        if record:record.heartbeat_at=now()
        else:session.add(Worker(worker_id=worker_id,heartbeat_at=now()))


def healthy(engine):
    with Session(engine) as session:stamp=session.scalar(select(func.max(Worker.heartbeat_at)))
    return stamp is not None and stamp>now()-timedelta(seconds=10)
