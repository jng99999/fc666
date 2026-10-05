"""Deterministic target decisions from reached closes; no execution or orders."""
from core.backtest.spot import digest
from core.strategy.contracts import StrategyContext
from core.strategy.registry import resolve

RULE='target-on-close-v1'


def normalized(strategy_id,parameters,period):
    if strategy_id is None:
        if parameters is not None:raise ValueError('Parameters require a replay strategy')
        return None,None
    definition=resolve(strategy_id)
    _,validated=definition.create(parameters if parameters is not None else ({'period':period} if strategy_id=='ema_long_flat_v1' else {}))
    if strategy_id=='ema_long_flat_v1' and validated.period!=period:
        raise ValueError('Replay EMA period must match indicator period')
    return definition,validated.model_dump(mode='json')


def events(bars,indicators,*,strategy_id,parameters,period,snapshot_sha256):
    definition,parameters=normalized(strategy_id,parameters,period)
    if definition is None:return []
    if len(bars)!=len(indicators):raise ValueError('Indicator prefix must match reached bars')
    strategy,_=definition.create(parameters)
    result=[]
    for bar,row in zip(bars,indicators):
        if row['available_at']!=bar.close_time.isoformat():raise ValueError('Decision indicator availability mismatch')
        signal=strategy.decide(StrategyContext(bar.close_time,bar.close,row['values']['ema']))
        if signal is None:continue
        if signal.available_at!=bar.close_time:raise ValueError('Only finalized-close decisions supported')
        payload={'schema_version':1,'type':'TARGET_DECISION','rule':RULE,'strategy':strategy_id,
                 'strategy_version':definition.version,'parameters':parameters,
                 'bar_open_time':bar.open_time.isoformat(),'available_at':signal.available_at.isoformat(),
                 'target':signal.target,'execution':'NOT_IMPLEMENTED'}
        result.append({'event_id':digest({'snapshot_sha256':snapshot_sha256,'decision':payload}),**payload})
    return result
