"""Bounded replay of a retained successful job; consistency, not authentication."""
import json
from core.backtest.spot import BacktestConfig, VERSION, digest, simulate
from core.models import Candle, Instrument

MAX_BYTES=32*1024*1024


def verify(request,snapshot,result):
    if not all(isinstance(value,dict) for value in (request,snapshot,result)):
        raise ValueError('Invalid retained research envelope')
    if len(json.dumps([request,snapshot,result],allow_nan=False).encode())>MAX_BYTES:
        raise ValueError('Retained research exceeds verification bound')
    fields={'request_sha256','engine_version','strategy','dataset','instrument','sha256'}
    if set(snapshot)!=fields:raise ValueError('Invalid snapshot fields')
    if digest({k:v for k,v in snapshot.items() if k!='sha256'})!=snapshot['sha256'] or digest(request)!=snapshot['request_sha256']:
        raise ValueError('Altered retained input')
    dataset=snapshot['dataset']
    if not isinstance(dataset,list) or not 2<=len(dataset)<=5000:raise ValueError('Invalid source bounds')
    strategy=request.get('strategy','ema_long_flat_v1')
    if snapshot['strategy']!=strategy:raise ValueError('Strategy mismatch')
    kind=request.get('research_type');version=snapshot['engine_version']
    runner=simulate;config_type=BacktestConfig
    options={'strategy_id':strategy,'parameters':request.get('parameters')}
    if kind is None and version=='spot-next-open-v2':
        from core.backtest.legacy_v2 import simulate as runner, BacktestConfig as config_type
        if strategy!='ema_long_flat_v1':raise ValueError('Unsupported legacy strategy')
        options={}
    elif kind is None and version==VERSION:pass
    elif kind=='holdout' and version=='spot-holdout-v1':
        from core.backtest.holdout import simulate as runner
        options['train_bars']=request['train_bars']
    elif kind=='walk_forward' and version=='spot-walk-forward-v1':
        from core.backtest.walk_forward import simulate as runner
        options={'strategy_id':strategy,'grid':request['grid'],'train_bars':request['train_bars'],'test_bars':request['test_bars']}
    else:raise ValueError('Research type/engine mismatch')
    reproduced=runner([Candle.model_validate(value) for value in dataset],
                      Instrument.model_validate(snapshot['instrument']),
                      config_type.model_validate(request['config']),**options)
    if digest(reproduced)!=digest(result):raise ValueError('Retained result differs from replay')
    return {'run_id':reproduced['run_id'],'engine_version':version,'result_sha256':digest(result),
            'snapshot_sha256':snapshot['sha256'],'consistency_verified':True,
            'provenance_authenticated':False,'trading_enabled':False}
