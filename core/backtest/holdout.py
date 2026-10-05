"""Fixed-parameter chronological comparison; no fitting, carry-over or trading."""
from core.backtest.spot import simulate as spot_simulate, digest
from core.strategy.registry import resolve

VERSION='spot-holdout-v1'


def validate_split(bars,train_bars,strategy_id,parameters,config):
    if isinstance(train_bars,bool) or not isinstance(train_bars,int):raise ValueError('Integer training size required')
    if not 2<=train_bars<=len(bars)-2:raise ValueError('Require at least two bars in each segment')
    _,validated=resolve(strategy_id).create(parameters)
    lookback=validated.period if strategy_id=='ema_long_flat_v1' else validated.slow
    if min(train_bars,len(bars)-train_bars)<lookback+1:
        raise ValueError('Each segment needs strategy lookback plus one bar for next-open execution')
    if strategy_id=='ema_long_flat_v1' and validated.period!=config.period:raise ValueError('EMA period must match config.period')
    if not 4<=len(bars)<=1000:raise ValueError('Require 4..1000 bars')
    for index,bar in enumerate(bars):
        if not bar.is_closed:raise ValueError('Finalized bars required')
        if index and (bar.timeframe!=bars[index-1].timeframe or bar.open_time!=bars[index-1].close_time):
            raise ValueError('Contiguous ordered series required')
    return validated


def simulate(bars,instrument,config,*,strategy_id,parameters,train_bars,checkpoint=None):
    validated=validate_split(bars,train_bars,strategy_id,parameters,config)
    total=len(bars)
    def progress(offset):
        return None if checkpoint is None else lambda done,count:checkpoint(offset+done,total)
    # Two fresh engines: no position, cash, pending signal, indicator or regime state crosses.
    train=spot_simulate(bars[:train_bars],instrument,config,strategy_id=strategy_id,
                        parameters=validated.model_dump(mode='json'),checkpoint=progress(0))
    test=spot_simulate(bars[train_bars:],instrument,config,strategy_id=strategy_id,
                       parameters=validated.model_dump(mode='json'),checkpoint=progress(train_bars))
    manifest={'engine_version':VERSION,'segment_engine_version':train['manifest']['engine_version'],
              'strategy':strategy_id,'parameters':validated.model_dump(mode='json'),
              'config':config.model_dump(mode='json'),'instrument':instrument.model_dump(mode='json'),
              'data_sha256':digest([bar.model_dump(mode='json') for bar in bars]),
              'train_bars':train_bars,'test_bars':total-train_bars,'split_at':bars[train_bars].open_time.isoformat(),
              'train_run_id':train['run_id'],'test_run_id':test['run_id'],
              'policy':'cold-start-independent-v1',
              'assumptions':['Fixed submitted parameters in both segments; no fitting or automatic selection',
                             'Separate initial cash, empty inventory and fresh indicator/strategy state per segment',
                             'No external warmup or pending signal crosses the split; warmup stays flat within each segment',
                             'No aggregate return or concatenated equity for these independently funded segments',
                             'Repeated inspection/tuning can contaminate the holdout; no statistical validation claim']}
    return {'run_id':digest(manifest),'manifest':manifest,'dataset':[bar.model_dump(mode='json') for bar in bars],
            'train':train,'test':test,'trading_enabled':False}
