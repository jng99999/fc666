"""Closed-bar replay: only the reached prefix can leave this module."""
from datetime import datetime,timezone
from uuid import uuid4
from sqlalchemy.orm import Session
from core.storage.models import ReplaySessionRecord as Replay
from core.models import Candle,Instrument
from core.indicators.engine import calculate
from core.backtest.spot import digest
from core.replay.decisions import normalized,events,RULE

VERSION='closed-bar-replay-v2'
LEGACY_VERSION='closed-bar-replay-v1'
class Conflict(Exception):pass


def validate(bars,instrument):
    if instrument.market_type!='SPOT':raise ValueError('Only Spot replay is implemented')
    if not 2<=len(bars)<=1000:raise ValueError('Require 2..1000 finalized bars')
    for index,bar in enumerate(bars):
        if not bar.is_closed or bar.instrument_id!=instrument.instrument_id:raise ValueError('Finalized matching instrument required')
        if index and (bar.timeframe!=bars[index-1].timeframe or bar.open_time!=bars[index-1].close_time):raise ValueError('Contiguous ordered series required')


def visible(record):
    snapshot=record.snapshot
    if digest({key:value for key,value in snapshot.items() if key!='sha256'})!=snapshot['sha256']:
        raise ValueError('Replay snapshot integrity failed')
    if snapshot['version'] not in [VERSION,LEGACY_VERSION]:raise ValueError('Unsupported replay version')
    bars=[Candle.model_validate(bar) for bar in snapshot['dataset']]
    instrument=Instrument.model_validate(snapshot['instrument']);validate(bars,instrument)
    if not 0<=record.cursor<=len(bars):raise ValueError('Invalid replay cursor')
    reached=bars[:record.cursor]
    clock=reached[-1].close_time if reached else bars[0].open_time
    # Rebuild strictly from the reached prefix; reset therefore cannot retain future pivots.
    indicators=calculate(reached,as_of=clock,period=snapshot['request']['period'])
    response={'session_id':record.session_id,'revision':record.revision,'cursor':record.cursor,'total':len(bars),
            'status':'ENDED' if record.cursor==len(bars) else 'READY','clock':clock,
            'manifest':{'version':snapshot['version'],'symbol':snapshot['request']['symbol'],
                        'timeframe':snapshot['request']['timeframe'],'period':snapshot['request']['period'],
                        'as_of':snapshot['request']['as_of'],'snapshot_sha256':snapshot['sha256'],
                        'start':bars[0].open_time,'end':bars[-1].close_time},
            'candles':[bar.model_dump(mode='json') for bar in reached],
            'indicators':indicators,'trading_enabled':False}
    if snapshot['version']==VERSION:
        request=snapshot['request'];strategy_id=request.get('strategy')
        definition,parameters=normalized(strategy_id,request.get('parameters'),request['period'])
        response['manifest'].update({'strategy':strategy_id,'strategy_version':None if definition is None else definition.version,'parameters':parameters,'decision_rule':RULE})
        response['decisions']=events(reached,indicators,strategy_id=strategy_id,parameters=parameters,period=request['period'],snapshot_sha256=snapshot['sha256'])
    return response


def create(engine,request,bars,instrument):
    validate(bars,instrument)
    _,parameters=normalized(request.get('strategy'),request.get('parameters'),request['period'])
    request={**request,'strategy':request.get('strategy'),'parameters':parameters}
    snapshot={'version':VERSION,'request':request,'dataset':[bar.model_dump(mode='json') for bar in bars],
              'instrument':instrument.model_dump(mode='json')}
    snapshot['sha256']=digest(snapshot);stamp=datetime.now(timezone.utc)
    with Session(engine) as session,session.begin():
        record=Replay(session_id=str(uuid4()),created_at=stamp,updated_at=stamp,snapshot=snapshot,cursor=0,revision=0)
        response=visible(record);session.add(record);return response


def read(engine,session_id):
    with Session(engine) as session:
        record=session.get(Replay,session_id)
        if record is None:raise KeyError(session_id)
        return visible(record)


def command(engine,session_id,expected_revision,action,count=1):
    if isinstance(expected_revision,bool) or not isinstance(expected_revision,int) or expected_revision<0:
        raise ValueError('Nonnegative integer revision required')
    if action not in ['step','reset'] or isinstance(count,bool) or not isinstance(count,int) or not 1<=count<=10:
        raise ValueError('Require step/reset with 1..10 bars')
    if action=='reset' and count!=1:raise ValueError('Reset count must be one')
    with Session(engine) as session,session.begin():
        record=session.get(Replay,session_id,with_for_update=True)
        if record is None:raise KeyError(session_id)
        if record.revision!=expected_revision:raise Conflict()
        # Verify before mutating; any invalid snapshot rolls the transaction back.
        response=visible(record)
        target=0 if action=='reset' else min(record.cursor+count,response['total'])
        if target!=record.cursor:
            record.cursor=target;record.revision+=1;record.updated_at=datetime.now(timezone.utc)
        return visible(record)
