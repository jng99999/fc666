"""Scoped public-history extension; existing candles are never revised or overwritten."""
import argparse
import json
from datetime import datetime, timezone
from sqlalchemy import create_engine,select
from sqlalchemy.orm import Session
from apps.api.settings import Settings
from core.exchange.public import public_adapter,SYMBOLS,INTERVALS
from core.models import Candle
from core.storage.models import CandleRecord,InstrumentRecord
from core.market_data.storage import save_candles,gap_report
from core.backtest.spot import digest

def run(symbols,timeframes,start_at,end_at,write=False):
    if any(value.tzinfo is None or value.utcoffset() is None for value in (start_at,end_at)):
        raise ValueError('Aware UTC window required')
    start,end=int(start_at.timestamp()*1000),int(end_at.timestamp()*1000)
    adapter=public_adapter();engine=create_engine(Settings().database_url.get_secret_value(),hide_parameters=True)
    report=[]
    try:
        now=adapter.server_time()
        if not 0<start<end<=now:raise ValueError('Past ordered window required')
        for symbol in symbols:
            for timeframe in timeframes:
                step=INTERVALS[timeframe]
                if start%step or end%step or not 2<=(end-start)//step<=5000:raise ValueError('Aligned 2..5000 bars required')
                bars=adapter.candles(symbol,timeframe,start,end,now)
                gaps=gap_report(bars,timeframe,start,end)
                if gaps['missing_count']:raise ValueError(f'{symbol} {timeframe}: incomplete public history: {gaps}')
                with Session(engine) as session:
                    if session.get(InstrumentRecord,SYMBOLS[symbol]) is None:raise ValueError('Instrument must already exist')
                    previous={row.open_time:Candle.model_validate(row,from_attributes=True) for row in session.scalars(select(CandleRecord).where(
                        CandleRecord.instrument_id==SYMBOLS[symbol],CandleRecord.timeframe==timeframe,CandleRecord.open_time>=start_at,CandleRecord.open_time<end_at))}
                conflicts=[bar.open_time.isoformat() for bar in bars if bar.open_time in previous and previous[bar.open_time]!=bar]
                if conflicts:raise ValueError(f'Existing history differs; no revision allowed: {symbol} {timeframe} {conflicts[:5]}')
                missing=[bar for bar in bars if bar.open_time not in previous]
                added=save_candles(engine,missing) if write else 0
                item={'symbol':symbol,'timeframe':timeframe,'start':start_at.isoformat(),'end':end_at.isoformat(),
                      'bars':len(bars),'missing_before':len(missing),'inserted':added,'revised':0,'dataset_sha256':digest([bar.model_dump(mode='json') for bar in bars]),**gaps}
                report.append(item);print(json.dumps(item),flush=True)
        return report
    finally:adapter.close();engine.dispose()

if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--symbols',nargs='+',choices=list(SYMBOLS),required=True)
    parser.add_argument('--timeframes',nargs='+',choices=list(INTERVALS),required=True)
    parser.add_argument('--start',type=datetime.fromisoformat,required=True);parser.add_argument('--end',type=datetime.fromisoformat,required=True)
    parser.add_argument('--write',action='store_true');args=parser.parse_args()
    run(args.symbols,args.timeframes,args.start,args.end,args.write)
