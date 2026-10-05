from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session
from core.storage.models import InstrumentRecord, CandleRecord
from core.exchange.binance import MarketDataError, INTERVALS

class DataConflict(MarketDataError):
    pass

def save_instruments(engine,instruments):
    with engine.begin() as conn:
        for instrument in instruments:
            data=instrument.model_dump()
            conn.execute(insert(InstrumentRecord).values(**data).on_conflict_do_update(
                index_elements=["instrument_id"],set_={k:v for k,v in data.items() if k!="instrument_id"}))

def save_candles(engine,candles):
    added=0
    with Session(engine) as session:
        for candle in candles:
            if not candle.is_closed: raise MarketDataError("Only finalized candles may be stored")
            key=(candle.instrument_id,candle.timeframe,candle.open_time)
            existing=session.get(CandleRecord,key)
            if existing is not None:
                if any(getattr(existing,name)!=getattr(candle,name) for name in ("open","high","low","close","volume","close_time","source")):
                    raise DataConflict("Finalized candle differs; explicit revision workflow required")
                continue
            # Concurrent identical imports must not race duplicate primary keys.
            data=candle.model_dump()
            statement=insert(CandleRecord).values(**data).on_conflict_do_nothing().returning(CandleRecord.open_time)
            if session.execute(statement).scalar() is not None: added+=1
            else:
                existing=session.get(CandleRecord,key,populate_existing=True)
                if any(getattr(existing,name)!=getattr(candle,name) for name in ("open","high","low","close","volume","close_time","source")):
                    raise DataConflict("Concurrent finalized candle differs")
        session.commit()
    return added

def gap_report(candles,interval,start,end):
    step=INTERVALS[interval]
    present={int(c.open_time.timestamp()*1000) for c in candles}
    first=((start+step-1)//step)*step
    expected=list(range(first,end,step))
    missing=[t for t in expected if t not in present]
    return {"expected":len(expected),"received":len(present),"missing_count":len(missing),"missing_open_times_ms":missing[:100],"missing_list_truncated":len(missing)>100}

def reconcile_confirmed_rest(engine,candidates,confirmation,*,observed_at):
    """Explicit revision path only: two matching REST observations, closed >=60s.

    Ordinary save_candles still rejects conflicts. Historical exports and queued
    job snapshots keep their original values; this transaction audits before/after.
    """
    from datetime import timedelta
    from uuid import uuid4
    from core.models import Candle
    from core.storage.models import CandleRevisionRecord
    if observed_at.tzinfo is None:raise ValueError('UTC-aware observation required')
    if len(candidates)!=len(confirmation) or any(a!=b for a,b in zip(candidates,confirmation)):
        raise DataConflict('REST observations do not confirm the same finalized dataset')
    revised=0
    with Session(engine) as session,session.begin():
        for candle in candidates:
            if not candle.is_closed:raise DataConflict('REST correction requires closed candles')
            key=(candle.instrument_id,candle.timeframe,candle.open_time)
            existing=session.get(CandleRecord,key,with_for_update=True)
            if existing is None:continue # Missing bars are appended by the normal importer afterwards.
            before=Candle.model_validate(existing,from_attributes=True)
            if before==candle:continue
            if candle.close_time>observed_at-timedelta(seconds=60):raise DataConflict('REST correction too recent; wait for finalized confirmation')
            session.add(CandleRevisionRecord(revision_id=str(uuid4()),instrument_id=candle.instrument_id,timeframe=candle.timeframe,open_time=candle.open_time,recorded_at=observed_at,reason='Two matching official finalized REST observations supersede prior stored bar',previous=before.model_dump(mode='json'),revised=candle.model_dump(mode='json')))
            for field in ('open','high','low','close','volume','close_time','source','is_closed'):setattr(existing,field,getattr(candle,field))
            revised+=1
    return revised
