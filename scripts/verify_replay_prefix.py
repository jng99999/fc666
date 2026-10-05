"""Offline consistency verification of reached data/indicators/target decisions.

Full snapshot SHA is an opaque binding: unreached data is deliberately absent.
"""
import argparse,json,re
from datetime import datetime
from pathlib import Path
from uuid import UUID
from core.exchange.binance import SYMBOLS
from core.models import Candle
from core.indicators.engine import calculate
from core.replay.decisions import events,normalized,RULE


def verify(data):
    if set(data)!={'session_id','revision','cursor','total','status','clock','manifest','candles','indicators','decisions','trading_enabled'}:raise ValueError('Unexpected prefix export fields')
    UUID(data['session_id'])
    if isinstance(data['revision'],bool) or not isinstance(data['revision'],int) or data['revision']<0:raise ValueError('Invalid replay revision')
    manifest=data['manifest']
    if manifest['symbol'] not in SYMBOLS:raise ValueError('Unknown replay symbol')
    if manifest['version']!='closed-bar-replay-v2' or manifest['decision_rule']!=RULE:
        raise ValueError('Unsupported replay decision version')
    if data['trading_enabled'] is not False:raise ValueError('Only read-only replay supported')
    if not re.fullmatch('[0-9a-f]{64}',manifest['snapshot_sha256']):raise ValueError('Snapshot binding required')
    bars=[Candle.model_validate(bar) for bar in data['candles']]
    cursor=data['cursor'];total=data['total']
    if isinstance(cursor,bool) or not isinstance(cursor,int) or isinstance(total,bool) or not isinstance(total,int) or not 0<=cursor<=total<=1000 or total<2 or cursor!=len(bars):
        raise ValueError('Invalid reached prefix length')
    if data['status']!=('ENDED' if cursor==total else 'READY'):raise ValueError('Invalid replay status')
    clock=datetime.fromisoformat(data['clock']);start=datetime.fromisoformat(manifest['start']);end=datetime.fromisoformat(manifest['end'])
    if clock.tzinfo is None or start.tzinfo is None or end.tzinfo is None or not start<=clock<=end:
        raise ValueError('Invalid replay clock')
    if cursor==total and clock!=end:raise ValueError('Ended replay must reach snapshot end')
    for index,bar in enumerate(bars):
        if not bar.is_closed or bar.close_time>clock or bar.timeframe!=manifest['timeframe'] or bar.instrument_id!=SYMBOLS[manifest['symbol']]:raise ValueError('Unreached or incompatible candle')
        if index and (bar.instrument_id!=bars[index-1].instrument_id or bar.open_time!=bars[index-1].close_time):raise ValueError('Contiguous matching prefix required')
    if (bars and (bars[0].open_time!=start or bars[-1].close_time!=clock)) or (not bars and clock!=start):raise ValueError('Clock must equal reached prefix boundary')
    definition,parameters=normalized(manifest['strategy'],manifest['parameters'],manifest['period'])
    if parameters!=manifest['parameters'] or manifest['strategy_version']!=(None if definition is None else definition.version):raise ValueError('Invalid strategy binding')
    indicators=calculate(bars,as_of=clock,period=manifest['period'])
    decisions=events(bars,indicators,strategy_id=manifest['strategy'],parameters=parameters,period=manifest['period'],snapshot_sha256=manifest['snapshot_sha256'])
    if indicators!=data['indicators'] or decisions!=data['decisions']:raise ValueError('Reached indicators or decisions differ from reproduction')
    return {'bars':cursor,'decisions':len(decisions)}


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('file',type=Path);args=parser.parse_args()
    if args.file.stat().st_size>4*1024*1024:parser.error('Prefix export exceeds 4 MiB')
    print('Verified reached prefix:',verify(json.loads(args.file.read_text())))
