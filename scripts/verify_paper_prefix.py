"""Offline consistency check of reached historical paper ledger; no external I/O."""
import json
import sys
from datetime import datetime
from pathlib import Path
from uuid import UUID
from core.models import Candle, Instrument
from core.backtest.spot import BacktestConfig
from core.paper.ledger import VERSION, RiskLimits, evaluate
from core.exchange.binance import SYMBOLS

LEDGER_KEYS = {'account','risk','orders','fills','equity','signals','pending'}


def verify(data):
    required = {'session_id','revision','cursor','total','status','clock','manifest','candles','trading_enabled','mode'} | LEDGER_KEYS
    if not isinstance(data, dict) or set(data) != required:
        raise ValueError('Unsupported paper prefix fields')
    UUID(data['session_id'])
    for key in ['cursor','total','revision']:
        if isinstance(data[key],bool) or not isinstance(data[key],int) or data[key]<0:
            raise ValueError('Invalid paper cursor, total or revision')
    cursor,total=data['cursor'],data['total']
    if not 0<=cursor<=total or not 2<=total<=1000 or len(data['candles'])!=cursor:
        raise ValueError('Invalid reached paper prefix length')
    if data['status']!=('ENDED' if cursor==total else 'READY') or data['trading_enabled'] is not False or data['mode']!='HISTORICAL_PAPER':
        raise ValueError('Invalid paper state')
    manifest=data['manifest']
    if manifest['version']!=VERSION or manifest['strategy_version']!=1:
        raise ValueError('Unsupported paper engine or strategy version')
    sha=manifest['snapshot_sha256']
    if not isinstance(sha,str) or len(sha)!=64 or any(c not in '0123456789abcdef' for c in sha):
        raise ValueError('Invalid paper snapshot hash')
    times=[datetime.fromisoformat(value) for value in [data['clock'],manifest['start'],manifest['end']]]
    if any(value.tzinfo is None or value.utcoffset() is None for value in times):
        raise ValueError('Timezone required')
    clock,start,end=times
    if not start<=clock<=end or (cursor==total and clock!=end):
        raise ValueError('Invalid paper clock')
    instrument=Instrument.model_validate(manifest['instrument'])
    if instrument.market_type!='SPOT' or instrument.instrument_id!=SYMBOLS.get(manifest['symbol']):
        raise ValueError('Only matching Binance Spot supported')
    bars=[Candle.model_validate(value) for value in data['candles']]
    for index,bar in enumerate(bars):
        if not bar.is_closed or bar.instrument_id!=instrument.instrument_id or bar.timeframe!=manifest['timeframe'] or bar.close_time>clock:
            raise ValueError('Invalid reached paper candle')
        if index and bar.open_time!=bars[index-1].close_time:
            raise ValueError('Noncontiguous paper prefix')
    if bars and (bars[0].open_time!=start or bars[-1].close_time!=clock) or not bars and clock!=start:
        raise ValueError('Paper clock differs from reached prefix')
    config=BacktestConfig.model_validate(manifest['config'])
    limits=RiskLimits.model_validate(manifest['risk'])
    expected=evaluate(bars,instrument,config,limits,strategy_id=manifest['strategy'],parameters=manifest['parameters'],snapshot_sha256=sha,halt_at=data['risk']['manual_halt_at'],ended=cursor==total)
    if expected!={key:data[key] for key in LEDGER_KEYS}:
        raise ValueError('Paper prefix ledger differs from causal reconstruction')
    return {'bars':cursor,'orders':len(expected['orders']),'fills':len(expected['fills'])}


def main():
    if len(sys.argv)!=2:raise SystemExit('Usage: python -m scripts.verify_paper_prefix prefix.json')
    path=Path(sys.argv[1])
    if path.stat().st_size>4*1024*1024:raise SystemExit('Paper prefix exceeds 4MiB')
    try:result=verify(json.loads(path.read_text()))
    except (ValueError,KeyError,TypeError) as error:raise SystemExit(f'Paper verification failed: {error}')
    print(f'Verified historical paper prefix: {result}')


if __name__=='__main__':main()
