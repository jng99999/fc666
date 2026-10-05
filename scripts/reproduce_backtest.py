"""Offline verification of an exported run; no network/database or trading keys."""
import argparse
import json
from pathlib import Path
from core.backtest.spot import BacktestConfig,simulate
from core.models import Candle,Instrument

def reproduce(data):
    if data['manifest']['engine_version']!='spot-next-open-v1': raise ValueError('Unsupported engine version')
    result=simulate([Candle.model_validate(b) for b in data['dataset']],Instrument.model_validate(data['manifest']['instrument']),BacktestConfig.model_validate(data['manifest']['config']))
    if result!=data: raise ValueError('Export differs from reproduced result')
    return result['run_id']

if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('file',type=Path);args=parser.parse_args()
    if args.file.stat().st_size>4*1024*1024:parser.error('Export exceeds 4 MiB')
    print('Verified offline:',reproduce(json.loads(args.file.read_text())))
