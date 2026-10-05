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
