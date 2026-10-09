"""Bounded rolling training-only parameter selection and cold out-of-sample runs."""
from copy import deepcopy
from decimal import Decimal
from core.backtest.spot import BacktestConfig,simulate as spot,digest
from core.strategy.registry import resolve
from core.models import Candle,Instrument

VERSION='spot-walk-forward-v1'
MAX_BYTES=32*1024*1024


def validate(bars,instrument,strategy_id,grid,train_bars,test_bars):
    if (type(train_bars) is not int or type(test_bars) is not int or train_bars<3 or test_bars<3
        or not isinstance(grid,list) or not 1<=len(grid)<=8 or not train_bars+test_bars<=len(bars)<=1000):
        raise ValueError('Bounded complete training/test windows and1..8 candidates required')
    parameters=[resolve(strategy_id).create(value)[1].model_dump(mode='json') for value in grid]
    if len({digest(value) for value in parameters})!=len(parameters):raise ValueError('Duplicate parameter candidate')
    lookback=max(p['period'] if strategy_id=='ema_long_flat_v1' else p['slow'] for p in parameters)
    if min(train_bars,test_bars)<lookback+1:raise ValueError('Every window needs lookback plus next-open bar')
    folds=(len(bars)-train_bars)//test_bars
    if not 1<=folds<=8:raise ValueError('Require1..8 complete out-of-sample folds')
    # Validate the entire source before any candidate runs, including trailing data.
    for i,bar in enumerate(bars):
        if not bar.is_closed or bar.instrument_id!=instrument.instrument_id or (i and (bar.timeframe!=bars[i-1].timeframe or bar.open_time!=bars[i-1].close_time)):
            raise ValueError('Matching closed contiguous source required')
    return parameters,folds


def simulate(bars,instrument,config,*,strategy_id,grid,train_bars,test_bars,checkpoint=None):
    parameters,folds=validate(bars,instrument,strategy_id,grid,train_bars,test_bars)
    total=folds*(len(parameters)*train_bars+test_bars);completed=0
    def progress(offset):
        return None if checkpoint is None else lambda done,count:checkpoint(offset+done,total)
    results=[]
    for index in range(folds):
        boundary=train_bars+index*test_bars;train=bars[boundary-train_bars:boundary];test=bars[boundary:boundary+test_bars]
        candidates=[]
        for p in parameters:
            selected_config=config.model_copy(update={'period':p['period']}) if strategy_id=='ema_long_flat_v1' else config
            run=spot(train,instrument,selected_config,strategy_id=strategy_id,parameters=p,checkpoint=progress(completed))
            completed+=train_bars
            candidates.append({'parameters':p,'config':selected_config.model_dump(mode='json'),'train':run})
        # Deterministic declared-grid-order tie break; no test result affects selection.
        selected=max(range(len(candidates)),key=lambda i:Decimal(candidates[i]['train']['metrics']['total_return']))
        chosen=candidates[selected]
        out=spot(test,instrument,BacktestConfig.model_validate(chosen['config']),strategy_id=strategy_id,parameters=chosen['parameters'],checkpoint=progress(completed))
        completed+=test_bars
        results.append({'train_start':train[0].open_time.isoformat(),'train_end':train[-1].close_time.isoformat(),
                        'test_start':test[0].open_time.isoformat(),'test_end':test[-1].close_time.isoformat(),
                        'candidates':candidates,'selected_index':selected,'test':out})
    manifest={'version':VERSION,'strategy':strategy_id,'grid':parameters,'config':config.model_dump(mode='json'),
              'instrument':instrument.model_dump(mode='json'),'train_bars':train_bars,'test_bars':test_bars,
              'selection':'maximum-training-total-return;grid-order-ties','data_sha256':digest([bar.model_dump(mode='json') for bar in bars]),
              'cold_test_windows':True,'aggregate_compounded_return':None,'trailing_unused_bars':len(bars)-train_bars-folds*test_bars}
    result={'run_id':digest(manifest),'manifest':manifest,'dataset':[bar.model_dump(mode='json') for bar in bars],'folds':results,'trading_enabled':False}
    import json
    if len(json.dumps(result).encode())>MAX_BYTES:raise ValueError('Walk-forward export exceeds32MiB')
    return result


def verify(report):
    if not isinstance(report,dict) or set(report)!={'run_id','manifest','dataset','folds','trading_enabled'}:raise ValueError('Invalid walk-forward envelope')
    import json
    if len(json.dumps(report).encode())>MAX_BYTES:raise ValueError('Walk-forward exceeds32MiB')
    m=report['manifest']
    if m['version']!=VERSION:raise ValueError('Unsupported walk-forward version')
    expected=simulate([Candle.model_validate(value) for value in report['dataset']],Instrument.model_validate(m['instrument']),
                      BacktestConfig.model_validate(m['config']),strategy_id=m['strategy'],grid=m['grid'],train_bars=m['train_bars'],test_bars=m['test_bars'])
    if expected!=report:raise ValueError('Walk-forward export differs from chronological replay')
    return deepcopy(expected)
