"""Offline verification of the complete accepted realtime paper stream."""
import json
import sys
from pathlib import Path
from types import SimpleNamespace
from uuid import UUID
from datetime import datetime
from core.backtest.spot import digest
from core.paper.streams import computed, VERSION

LEDGER_KEYS={'account','risk','orders','fills','equity','signals','pending'}


def verify(data):
    required={'session_id','revision','status','clock','seed_bars','accepted_bars','bar_limit','manifest','seed','observations','feed','mode','trading_enabled'}|LEDGER_KEYS
    if not isinstance(data,dict) or set(data)!=required:raise ValueError('Unsupported realtime paper fields')
    UUID(data['session_id'])
    if data['mode']!='REALTIME_CLOSED_BAR_PAPER' or data['trading_enabled'] is not False or data['bar_limit']!=1000:raise ValueError('Invalid paper mode')
    if data['status'] not in ['RUNNING','PAUSED','STOPPED','BLOCKED','LIMIT_REACHED']:raise ValueError('Invalid paper status')
    if isinstance(data['revision'],bool) or not isinstance(data['revision'],int) or data['revision']<0:raise ValueError('Invalid paper revision')
    if data['seed_bars']!=len(data['seed']) or data['accepted_bars']!=len(data['observations']):raise ValueError('Invalid paper bar count')
    manifest=data['manifest']
    if manifest['version']!=VERSION:raise ValueError('Unsupported stream version')
    snapshot={'version':VERSION,'request':manifest['request'],'dataset':data['seed'],'instrument':manifest['instrument']}
    if digest(snapshot)!=manifest['snapshot_sha256']:raise ValueError('Initial paper snapshot hash differs')
    snapshot['sha256']=manifest['snapshot_sha256']
    record=SimpleNamespace(snapshot=snapshot,observations=data['observations'],halt_at=data['risk']['manual_halt_at'])
    expected=computed(record)
    if expected!={key:data[key] for key in LEDGER_KEYS}:raise ValueError('Realtime paper ledger differs from reconstruction')
    clock=data['observations'][-1]['candle']['close_time'] if data['observations'] else data['seed'][-1]['close_time']
    if datetime.fromisoformat(clock)!=datetime.fromisoformat(data['clock']):raise ValueError('Paper clock differs')
    return {'warmup':len(data['seed']),'accepted':len(data['observations']),'fills':len(expected['fills'])}


def main():
    if len(sys.argv)!=2:raise SystemExit('Usage: python -m scripts.verify_paper_stream stream.json')
    path=Path(sys.argv[1])
    if path.stat().st_size>4*1024*1024:raise SystemExit('Paper stream exceeds 4MiB')
    try:result=verify(json.loads(path.read_text()))
    except (ValueError,KeyError,TypeError) as error:raise SystemExit(f'Realtime paper verification failed: {error}')
    print(f'Verified realtime paper stream: {result}')


if __name__=='__main__':main()
