"""Import true finalized public OHLCV; never generate market data."""
import argparse
from datetime import datetime,timezone
import json
import hashlib
from pathlib import Path
from sqlalchemy import create_engine
from apps.api.settings import Settings
from core.exchange.binance import BinancePublic, SYMBOLS, INTERVALS, MarketDataError
from core.market_data.storage import save_instruments, save_candles, gap_report

def run(bars,until_ms=None):
    adapter=BinancePublic()
    engine=create_engine(Settings().database_url.get_secret_value(),hide_parameters=True)
    try:
        now=adapter.server_time()
        cutoff=now if until_ms is None else until_ms
        if not 0<cutoff<=now: raise MarketDataError("Import cutoff must be positive and no later than exchange server time")
        instruments=adapter.instruments();save_instruments(engine,instruments)
        reports=[]
        for symbol in SYMBOLS:
            for interval,step in INTERVALS.items():
                end=(cutoff//step)*step;start=end-bars*step
                candles=adapter.candles(symbol,interval,start,end,now)
                added=save_candles(engine,candles)
                reports.append({"symbol":symbol,"timeframe":interval,"from_ms":start,"until_ms":end,"inserted":added,"dataset_sha256":hashlib.sha256("\n".join(c.model_dump_json() for c in candles).encode()).hexdigest(),**gap_report(candles,interval,start,end)})
        report={"source":"https://data-api.binance.vision", "verified_at":datetime.now(timezone.utc).isoformat(),"requested_until_ms":cutoff,"results":reports}
        output=Path(".runtime/import_report.json");output.parent.mkdir(exist_ok=True);output.write_text(json.dumps(report,indent=2))
        print(json.dumps(report,indent=2))
        if any(item["missing_count"] for item in reports): raise SystemExit("Historical import has gaps; report retained above")
    finally:
        adapter.close();engine.dispose()

if __name__=="__main__":
    parser=argparse.ArgumentParser();parser.add_argument("--bars",type=int,default=120);parser.add_argument("--until-ms",type=int)
    args=parser.parse_args()
    if not 1<=args.bars<=20000: parser.error("bars must be 1..20000")
    try: run(args.bars,args.until_ms)
    except MarketDataError as exc: raise SystemExit(str(exc))
