"""Offline verification of an exported run; no network/database or trading keys."""
import argparse
import json
import os,stat
from pathlib import Path
from core.backtest.spot import BacktestConfig,simulate,digest
from core.models import Candle,Instrument
from core.market_data.quality import unique_object

MAX_BYTES=32*1024*1024

def load(path):
    descriptor=os.open(path,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK)
    with os.fdopen(descriptor,'rb') as file:
        if not stat.S_ISREG(os.fstat(file.fileno()).st_mode):raise ValueError('Regular export file required')
        raw=file.read(MAX_BYTES+1)
    if len(raw)>MAX_BYTES:raise ValueError('Export exceeds 32 MiB')
    def invalid_constant(value):raise ValueError('Non-finite JSON rejected')
    return json.loads(raw,object_pairs_hook=unique_object,parse_constant=invalid_constant)

def reproduce(data):
    if not isinstance(data,dict) or not isinstance(data.get('manifest'),dict):raise ValueError('Invalid export envelope')
    if len(json.dumps(data,allow_nan=False).encode())>MAX_BYTES:raise ValueError('Export exceeds 32 MiB')
    if not isinstance(data.get('dataset'),list) or not 2<=len(data['dataset'])<=5000:raise ValueError('Invalid export dataset bounds')
    version=data['manifest']['engine_version']
    if version=='spot-holdout-v1':
        from core.backtest.holdout import simulate as runner
        result=runner([Candle.model_validate(bar) for bar in data['dataset']],Instrument.model_validate(data['manifest']['instrument']),BacktestConfig.model_validate(data['manifest']['config']),strategy_id=data['manifest']['strategy'],parameters=data['manifest']['parameters'],train_bars=data['manifest']['train_bars'])
        if digest(result)!=digest(data):raise ValueError('Export differs from reproduced result')
        return result['run_id']
    if version=='spot-next-open-v1':
        from core.backtest.legacy_v1 import simulate as runner, BacktestConfig as config_type
    elif version=='spot-next-open-v2':
        from core.backtest.legacy_v2 import simulate as runner, BacktestConfig as config_type
    elif version=='spot-next-open-v3':runner=simulate;config_type=BacktestConfig
    else:raise ValueError('Unsupported engine version')
    result=runner([Candle.model_validate(b) for b in data['dataset']],Instrument.model_validate(data['manifest']['instrument']),config_type.model_validate(data['manifest']['config']),**({'strategy_id':data['manifest']['strategy'],'parameters':data['manifest']['parameters']} if version=='spot-next-open-v3' else {}))
    if digest(result)!=digest(data): raise ValueError('Export differs from reproduced result')
    return result['run_id']

if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('file',type=Path);args=parser.parse_args()
    try:run_id=reproduce(load(args.file))
    except (OSError,ValueError,KeyError,TypeError):parser.exit(1,'Backtest export verification failed\n')
    print('Verified offline:',run_id)
