"""Offline verification of an exported run; no network/database or trading keys."""
import argparse
import json
from pathlib import Path
from core.backtest.spot import BacktestConfig,simulate
from core.models import Candle,Instrument

def reproduce(data):
    version=data['manifest']['engine_version']
    if version=='spot-holdout-v1':
        from core.backtest.holdout import simulate as runner
        result=runner([Candle.model_validate(bar) for bar in data['dataset']],Instrument.model_validate(data['manifest']['instrument']),BacktestConfig.model_validate(data['manifest']['config']),strategy_id=data['manifest']['strategy'],parameters=data['manifest']['parameters'],train_bars=data['manifest']['train_bars'])
        if result!=data:raise ValueError('Export differs from reproduced result')
        return result['run_id']
    if version=='spot-next-open-v1':
        from core.backtest.legacy_v1 import simulate as runner, BacktestConfig as config_type
    elif version=='spot-next-open-v2':
        from core.backtest.legacy_v2 import simulate as runner, BacktestConfig as config_type
    elif version=='spot-next-open-v3':runner=simulate;config_type=BacktestConfig
    else:raise ValueError('Unsupported engine version')
    result=runner([Candle.model_validate(b) for b in data['dataset']],Instrument.model_validate(data['manifest']['instrument']),config_type.model_validate(data['manifest']['config']),**({'strategy_id':data['manifest']['strategy'],'parameters':data['manifest']['parameters']} if version=='spot-next-open-v3' else {}))
    if result!=data: raise ValueError('Export differs from reproduced result')
    return result['run_id']

if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('file',type=Path);args=parser.parse_args()
    if args.file.stat().st_size>4*1024*1024:parser.error('Export exceeds 4 MiB')
    print('Verified offline:',reproduce(json.loads(args.file.read_text())))
