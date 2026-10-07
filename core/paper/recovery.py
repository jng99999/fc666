"""Read-only recovery inspection; never repairs or replays simulated execution."""
import json
from datetime import datetime,timezone
from types import SimpleNamespace
from uuid import UUID
from sqlalchemy import select,func
from sqlalchemy.orm import Session
from core.backtest.spot import digest
from core.models import Candle,Instrument
from core.paper import streams
from core.storage.models import PaperStreamRecord,PaperWorkerRecord,CandleRecord,InstrumentRecord

VERSION='paper-stream-recovery-v1'
MAX_BYTES=32*1024*1024

class MissingAccount(KeyError):pass


def stamp(value):
    result=datetime.fromisoformat(value)
    if result.tzinfo is None:raise ValueError('Timezone required')
    return result.astimezone(timezone.utc)


def evaluate(inputs):
    if set(inputs)!={'version','as_of','record','instrument','accepted','worker_heartbeat'} or inputs['version']!=VERSION:raise ValueError('Invalid recovery inputs')
    cutoff=stamp(inputs['as_of']);raw=inputs['record']
    if set(raw)!={'session_id','snapshot','observations','ledger','revision','status','halt_at','feed'}:raise ValueError('Invalid account fields')
    if str(UUID(raw['session_id']))!=raw['session_id'] or isinstance(raw['revision'],bool) or not isinstance(raw['revision'],int) or raw['revision']<0:raise ValueError('Invalid account identity or revision')
    if raw['status'] not in ['RUNNING','PAUSED','STOPPED','BLOCKED','LIMIT_REACHED']:raise ValueError('Invalid account status')
    if stamp(raw['snapshot']['request']['as_of'])>cutoff or any(stamp(row['observed_at'])>cutoff for row in raw['observations']):raise ValueError('Future account inputs')
    visible=streams.visible(SimpleNamespace(**raw))
    expected=[*raw['snapshot']['dataset'],*[row['candle'] for row in raw['observations']]]
    if any(Candle.model_validate(bar).close_time>cutoff for bar in [*expected,*inputs['accepted']]):raise ValueError('Future source bars')
    if raw['feed'].get('checked_at') is not None and stamp(raw['feed']['checked_at'])>cutoff:raise ValueError('Future feed clock')
    instrument=Instrument.model_validate(raw['snapshot']['instrument'])
    rules_match=inputs['instrument'] is not None and Instrument.model_validate(inputs['instrument'])==instrument
    data_match=[Candle.model_validate(bar) for bar in inputs['accepted']]==[Candle.model_validate(bar) for bar in expected]
    heartbeat=stamp(inputs['worker_heartbeat']) if inputs['worker_heartbeat'] else None
    if heartbeat is not None and heartbeat>cutoff:raise ValueError('Future heartbeat')
    healthy=heartbeat is not None and (cutoff-heartbeat).total_seconds()<=10
    order_ids=[order['order_id'] for order in visible['orders']];fill_ids=[fill['order_id'] for fill in visible['fills']]
    if len(set(order_ids))!=len(order_ids) or len(set(fill_ids))!=len(fill_ids) or not set(fill_ids)<=set(order_ids):raise ValueError('Ambiguous simulated receipts')
    fills={fill['order_id']:fill for fill in visible['fills']}
    receipts=[]
    for order in visible['orders']:
        fill=fills.get(order['order_id'])
        if (order['status']=='REJECTED')!=(fill is None):raise ValueError('Order/fill outcome mismatch')
        if fill is not None and (any(fill[key]!=order[key] for key in ['order_id','side','decision_at','execution_at','observed_at','simulated']) or fill['quantity']!=order['quantity']):
            raise ValueError('Order/fill receipt mismatch')
        receipts.append({'order':order,'fill':fill})
    state='NEEDS_REVIEW' if not rules_match or not data_match else 'BLOCKED_RETAINED' if raw['status']=='BLOCKED' else 'TERMINAL_RETAINED' if raw['status'] in ['STOPPED','LIMIT_REACHED'] else 'WAITING_FOR_BAR' if healthy else 'WORKER_UNCONFIRMED'
    result={'version':VERSION,'session_id':raw['session_id'],'as_of':inputs['as_of'],'revision':raw['revision'],
        'account_status':raw['status'],'feed':raw['feed'],'recovery_state':state,'ledger_clock':visible['clock'],
        'checks':{'ledger_reproduced':True,'unique_simulated_receipts':True,'instrument_rules_match':rules_match,'accepted_source_match':data_match,'worker_healthy_at_read':healthy},
        'account':visible['account'],'receipts':receipts,'pending_decision':visible['pending'],
        'automatic_replay':False,'trading_enabled':False,'mode':'READ_ONLY_PAPER_RECOVERY',
        'inputs_sha256':digest(inputs),'inputs':inputs}
    if len(json.dumps(result,ensure_ascii=False,separators=(',',':')).encode())>MAX_BYTES:raise ValueError('Recovery export exceeds 32 MiB')
    return result


def capture(engine,id):
    with engine.connect().execution_options(isolation_level='REPEATABLE READ') as connection,connection.begin(),Session(bind=connection) as db:
        record=db.get(PaperStreamRecord,id)
        if record is None:raise MissingAccount(id)
        cutoff=datetime.now(timezone.utc)
        streams.visible(record)
        raw={key:getattr(record,key) for key in ['session_id','snapshot','observations','ledger','revision','status','halt_at','feed']}
        expected=[*record.snapshot['dataset'],*[row['candle'] for row in record.observations]]
        instrument=Instrument.model_validate(record.snapshot['instrument']);current=db.get(InstrumentRecord,instrument.instrument_id)
        rows=list(db.scalars(select(CandleRecord).where(CandleRecord.instrument_id==instrument.instrument_id,CandleRecord.timeframe==expected[0]['timeframe'],CandleRecord.open_time>=stamp(expected[0]['open_time']),CandleRecord.open_time<=stamp(expected[-1]['open_time']),CandleRecord.close_time<=cutoff).order_by(CandleRecord.open_time)))
        heartbeat=db.scalar(select(func.max(PaperWorkerRecord.heartbeat_at)).where(PaperWorkerRecord.heartbeat_at<=cutoff))
        return evaluate({'version':VERSION,'as_of':cutoff.isoformat(),'record':raw,
            'instrument':Instrument.model_validate(current,from_attributes=True).model_dump(mode='json') if current else None,
            'accepted':[Candle.model_validate(row,from_attributes=True).model_dump(mode='json') for row in rows],
            'worker_heartbeat':heartbeat.isoformat() if heartbeat else None})


def verify(value):
    if value!=evaluate(value['inputs']):raise ValueError('Recovery inspection differs from reproduced inputs')
    return {'state':value['recovery_state'],'orders':len(value['receipts']),'fills':sum(receipt['fill'] is not None for receipt in value['receipts'])}
