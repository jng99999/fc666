"""Immutable rule authorization for projected Paper orders, before acceptance."""
from datetime import datetime,timezone
from types import SimpleNamespace
import json
from sqlalchemy import select,func
from sqlalchemy.orm import Session
from core.backtest.spot import digest
from core.paper import streams,intents
from core.storage.models import PaperStreamRecord as Stream,PaperPreparationRecord as Preparation,PaperAuthorizationRecord as Authorization

VERSION='paper-simulated-authorization-v1'
MAX_BYTES=32*1024*1024
WINDOW=20


def gate(record):
    streams.visible(record)
    running=record.status=='RUNNING'
    return {'account_status':record.status,'buy_allowed':running and not record.ledger['risk']['entry_halted'],
        'sell_allowed':running,'entry_halted':record.ledger['risk']['entry_halted'],
        'scope':'LOCAL_PAPER_RULES','external_submission_allowed':False}


def plan(record,prepared):
    from core.paper import preparation
    payload=preparation.validate(prepared)
    if preparation.base(record)!=payload['base']:raise ValueError('Authorization base mismatch')
    before=streams.visible(record);account_gate=gate(record)
    candidate=SimpleNamespace(snapshot=record.snapshot,observations=[*record.observations,*payload['observations']],halt_at=record.halt_at)
    projected=streams.computed(candidate)
    if projected['orders'][:len(before['orders'])]!=before['orders'] or projected['fills'][:len(before['fills'])]!=before['fills']:raise ValueError('Projected prefix changed')
    fills={fill['order_id']:fill for fill in projected['fills'][len(before['fills']):]}
    result=[]
    for order in projected['orders'][len(before['orders']):]:
        estimate=fills.get(order['order_id']);allowed=order['status']!='REJECTED'
        if allowed and not account_gate['buy_allowed' if order['side']=='BUY' else 'sell_allowed']:raise ValueError('Projected execution violates account gate')
        if allowed!=(estimate is not None):raise ValueError('Projected authorization receipt mismatch')
        value={'version':VERSION,'session_id':record.session_id,'preparation_id':prepared.preparation_id,
            'snapshot_sha256':record.snapshot['sha256'],'engine_version':record.snapshot['version'],
            'account_gate':account_gate,'decision':'ALLOW_SIMULATION' if allowed else 'DENY_SIMULATION',
            'reason':None if allowed else order['reason'],'proposed_order':order,'projected_fill':estimate,
            'projection_only':True,'external_submission_allowed':False}
        result.append({'authorization_id':digest({'version':VERSION,'preparation_id':prepared.preparation_id,'order_id':order['order_id']}),
            'order_id':order['order_id'],'payload':value,'payload_sha256':digest(value)})
    if len({r['order_id'] for r in result})!=len(result) or len(fills)!=sum(r['payload']['decision']=='ALLOW_SIMULATION' for r in result):raise ValueError('Ambiguous authorization projection')
    return result


def rows(db,pid):
    return list(db.scalars(select(Authorization).where(Authorization.preparation_id==pid).order_by(Authorization.order_id)))


def persist(db,record,prepared):
    if prepared.authorization_version!=VERSION or rows(db,prepared.preparation_id):raise ValueError('Authorization must be persisted once with new preparation')
    for value in plan(record,prepared):
        db.add(Authorization(preparation_id=prepared.preparation_id,recorded_at=datetime.now(timezone.utc),**value))
    db.flush()


def check(db,record,prepared):
    if prepared.authorization_version!=VERSION:raise ValueError('Preparation lacks supported authorization')
    expected={r['order_id']:r for r in plan(record,prepared)};stored=rows(db,prepared.preparation_id)
    if len(stored)!=len(expected) or {r.order_id for r in stored}!=set(expected):raise ValueError('Authorization coverage mismatch')
    for row in stored:
        if any(getattr(row,key)!=value for key,value in expected[row.order_id].items()):raise ValueError('Authorization differs from frozen projection')
        if row.recorded_at.tzinfo is None or row.recorded_at<prepared.created_at or row.recorded_at>datetime.now(timezone.utc):raise ValueError('Invalid authorization evidence clock')
        if prepared.finished_at is not None and row.recorded_at>prepared.finished_at:raise ValueError('Authorization was recorded after preparation finished')
    return stored


def historical_base(record,prepared):
    from core.paper import preparation
    payload=preparation.validate(prepared)
    first=datetime.fromisoformat(payload['observations'][0]['candle']['open_time'])
    prefix=[row for row in record.observations if datetime.fromisoformat(row['candle']['open_time'])<first]
    frozen=SimpleNamespace(session_id=record.session_id,snapshot=record.snapshot,observations=prefix,
        revision=payload['base']['revision'],status=payload['base']['status'],halt_at=payload['base']['halt_at'],
        feed=record.feed)
    frozen.ledger=streams.computed(frozen)
    if preparation.base(frozen)!=payload['base']:raise ValueError('Historical authorization base differs')
    return frozen


def capture(engine,id):
    from core.paper import preparation
    with engine.connect().execution_options(isolation_level='REPEATABLE READ') as conn,conn.begin(),Session(bind=conn) as db:
        record=db.get(Stream,id)
        if record is None:raise intents.MissingAccount(id)
        current_gate=gate(record)
        total=db.scalar(select(func.count()).select_from(Preparation).where(Preparation.session_id==id))
        if total>preparation.LIMIT:raise ValueError('Authorization history exceeds preparation capacity')
        batches=list(reversed(list(db.scalars(select(Preparation).where(Preparation.session_id==id).order_by(Preparation.created_at.desc(),Preparation.preparation_id.desc()).limit(WINDOW)))))
        result=[]
        for prepared in batches:
            preparation.validate(prepared)
            if prepared.authorization_version is None:
                if rows(db,prepared.preparation_id):raise ValueError('Legacy preparation has undeclared authorization')
                result.append({'preparation_id':prepared.preparation_id,'status':prepared.status,'coverage':'LEGACY_UNAVAILABLE','authorizations':[]})
            else:
                frozen=historical_base(record,prepared)
                stored=check(db,frozen,prepared)
                if prepared.status=='CONSUMED':
                    start=len(frozen.observations)
                    observations=prepared.payload['observations']
                    if record.observations[start:start+len(observations)]!=observations:raise ValueError('Consumed authorization lacks accepted observations')
                    actual_orders={r['order_id']:r for r in record.ledger['orders']}
                    actual_fills={r['order_id']:r for r in record.ledger['fills']}
                    if any(actual_orders.get(r.order_id)!=r.payload['proposed_order'] or actual_fills.get(r.order_id)!=r.payload['projected_fill'] for r in stored):raise ValueError('Consumed authorization differs from accepted receipts')
                result.append({'preparation_id':prepared.preparation_id,'status':prepared.status,'coverage':'VERIFIED',
                    'authorizations':[{'authorization_id':r.authorization_id,'recorded_at':r.recorded_at.isoformat(),'payload':r.payload,'payload_sha256':r.payload_sha256} for r in stored]})
        report={'version':VERSION,'session_id':id,'revision':record.revision,'account_gate':current_gate,'batches':result,
            'total_batches':total,'window_limit':WINDOW,'has_older':total>len(batches),
            'mode':'READ_ONLY_PAPER_AUTHORIZATION','trading_enabled':False,'external_submission_supported':False,'automatic_replay':False}
        if len(json.dumps(report,separators=(',',':')).encode())>MAX_BYTES:raise ValueError('Authorization response exceeds 32 MiB')
        return report
