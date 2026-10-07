"""Account-scoped projected-fill reservations, atomic with original Paper effects."""
from datetime import datetime, timezone
from decimal import Decimal, localcontext
from types import SimpleNamespace
import json
from sqlalchemy import select, func
from sqlalchemy.orm import Session
from core.backtest.spot import digest
from core.paper import authorization, streams, intents
from core.storage.models import (PaperStreamRecord as Stream, PaperPreparationRecord as Preparation,
    PaperFundingReservationRecord as Reservation, PaperFundingOutcomeRecord as Outcome, PaperLifecycleCoverageRecord as Coverage)

VERSION='paper-projected-funding-v1'
WINDOW=20
MAX_BYTES=32*1024*1024


def plan(record, prepared, approved):
    from core.paper import preparation
    payload=preparation.validate(prepared)
    if preparation.base(record)!=payload['base']:
        raise ValueError('Funding base mismatch')
    before=streams.visible(record)
    candidate=SimpleNamespace(snapshot=record.snapshot,observations=[*record.observations,*payload['observations']],halt_at=record.halt_at)
    projected=streams.computed(candidate)
    auths={row.order_id:row for row in approved}
    orders=projected['orders'][len(before['orders']):]
    if {row['order_id'] for row in orders}!=set(auths):
        raise ValueError('Funding authorization coverage mismatch')
    with localcontext() as context:
        context.prec=60
        cash=Decimal(before['account']['cash']);quantity=Decimal(before['account']['quantity'])
        initial_cash,initial_quantity=cash,quantity
        min_cash,min_quantity=cash,quantity
        flows=[]
        for order in orders:
            approved_order=auths[order['order_id']]
            fill=approved_order.payload['projected_fill']
            start_cash,start_quantity=cash,quantity
            needed_cash=needed_quantity=Decimal(0)
            if fill is not None:
                size,price,fee=(Decimal(fill[key]) for key in ['quantity','price','fee'])
                if not all(v.is_finite() for v in [size,price,fee]) or size<=0 or price<=0 or fee<0:
                    raise ValueError('Invalid projected funding values')
                if order['side']=='BUY':
                    needed_cash=size*price+fee
                    if needed_cash>cash:raise ValueError('Projected buy exceeds account funds')
                    cash-=needed_cash;quantity+=size
                elif order['side']=='SELL':
                    needed_quantity=size
                    if size>quantity:raise ValueError('Projected sell exceeds account inventory')
                    cash+=size*price-fee;quantity-=size
                else:raise ValueError('Invalid funding side')
                if cash<0 or quantity<0 or cash!=Decimal(fill['cash_after']) or quantity!=Decimal(fill['quantity_after']):
                    raise ValueError('Funding differs from original ledger receipts')
            min_cash,min_quantity=min(min_cash,cash),min(min_quantity,quantity)
            flows.append({'authorization_id':approved_order.authorization_id,'authorization_sha256':approved_order.payload_sha256,
                'order_id':order['order_id'],'side':order['side'],'cash_before':str(start_cash),'quantity_before':str(start_quantity),
                'required_cash':str(needed_cash),'required_quantity':str(needed_quantity),'cash_after':str(cash),'quantity_after':str(quantity)})
        if cash!=Decimal(projected['account']['cash']) or quantity!=Decimal(projected['account']['quantity']):
            raise ValueError('Funding final balance mismatch')
        return {'version':VERSION,'scope':'ACCOUNT_SEQUENTIAL_PROJECTED_FILLS','session_id':record.session_id,
            'preparation_id':prepared.preparation_id,'base':payload['base'],'initial_cash':str(initial_cash),'initial_quantity':str(initial_quantity),
            'reserved_cash':str(initial_cash-min_cash),'reserved_quantity':str(initial_quantity-min_quantity),
            'flows':flows,'projected_cash_after':str(cash),'projected_quantity_after':str(quantity),
            'projected_ledger_sha256':digest(projected),'external_submission_allowed':False}


def outcome(reservation, status, reason):
    value=reservation.payload
    consumed=status=='CONSUMED'
    return {'version':VERSION,'preparation_id':reservation.preparation_id,'reservation_sha256':reservation.payload_sha256,
        'status':status,'reason':reason,'release_cash':value['reserved_cash'],'release_quantity':value['reserved_quantity'],
        'applied_ledger_sha256':value['projected_ledger_sha256'] if consumed else None,
        'cash_after':value['projected_cash_after'] if consumed else None,
        'quantity_after':value['projected_quantity_after'] if consumed else None,
        'economic_effects_applied':consumed and any(Decimal(flow['required_cash'])>0 or Decimal(flow['required_quantity'])>0 for flow in value['flows'])}


def check(db, record, prepared):
    reservation=db.get(Reservation,prepared.preparation_id)
    final=db.get(Outcome,prepared.preparation_id)
    if prepared.funding_version is None:
        if reservation is not None or final is not None:raise ValueError('Undeclared funding evidence')
        return None
    if prepared.funding_version!=VERSION or reservation is None:
        raise ValueError('Missing or unsupported funding reservation')
    frozen=authorization.historical_base(record,prepared)
    approved=authorization.check(db,frozen,prepared)
    expected=plan(frozen,prepared,approved)
    if reservation.payload!=expected or reservation.payload_sha256!=digest(expected):
        raise ValueError('Funding reservation integrity failed')
    from core.paper import lifecycle
    creation=db.get(Coverage,prepared.preparation_id)
    if creation is None or creation.version!=lifecycle.VERSION or creation.recorded_at.tzinfo is None:
        raise ValueError('Funding lacks original lifecycle creation coverage')
    clock=reservation.recorded_at
    if clock.tzinfo is None or clock<prepared.created_at or clock>datetime.now(timezone.utc) or clock<creation.recorded_at or creation.recorded_at<prepared.created_at or any(clock<row.recorded_at for row in approved):
        raise ValueError('Invalid funding reservation clock')
    if prepared.finished_at is not None and clock>prepared.finished_at:raise ValueError('Reservation follows settlement')
    if prepared.status=='PREPARED':
        if final is not None:raise ValueError('Pending reservation has an outcome')
    else:
        expected_final=outcome(reservation,prepared.status,prepared.reason)
        if final is None or final.payload!=expected_final or final.payload_sha256!=digest(expected_final):
            raise ValueError('Funding settlement integrity failed')
        if final.recorded_at.tzinfo is None or final.recorded_at>datetime.now(timezone.utc) or not clock<=final.recorded_at<=prepared.finished_at:
            raise ValueError('Invalid funding settlement clock')
    return reservation


def persist(db, record, prepared):
    if prepared.funding_version!=VERSION or db.get(Reservation,prepared.preparation_id) is not None:
        raise ValueError('Funding reservation must be created once')
    value=plan(record,prepared,authorization.check(db,record,prepared))
    db.add(Reservation(preparation_id=prepared.preparation_id,recorded_at=datetime.now(timezone.utc),payload=value,payload_sha256=digest(value)))
    db.flush()


def finish(db, record, prepared, status, reason):
    if prepared.status!='PREPARED':raise ValueError('Funding settlement requires a pending preparation')
    reservation=check(db,record,prepared)
    if reservation is None:return
    if status not in ['CONSUMED','CANCELLED'] or (status=='CONSUMED' and reason is not None):
        raise ValueError('Invalid funding settlement')
    if status=='CONSUMED':
        actual=streams.visible(record)
        if digest(record.ledger)!=reservation.payload['projected_ledger_sha256'] or actual['account']['cash']!=reservation.payload['projected_cash_after'] or actual['account']['quantity']!=reservation.payload['projected_quantity_after']:
            raise ValueError('Actual account effects differ from reserved projection')
    value=outcome(reservation,status,reason)
    db.add(Outcome(preparation_id=prepared.preparation_id,recorded_at=datetime.now(timezone.utc),payload=value,payload_sha256=digest(value)))
    db.flush()


def capture(engine,id):
    with engine.connect().execution_options(isolation_level='REPEATABLE READ') as conn,conn.begin(),Session(bind=conn) as db:
        record=db.get(Stream,id)
        if record is None:raise intents.MissingAccount(id)
        streams.visible(record)
        total=db.scalar(select(func.count()).select_from(Preparation).where(Preparation.session_id==id))
        if total>1000:raise ValueError('Funding history exceeds capacity')
        rows=list(db.scalars(select(Preparation).where(Preparation.session_id==id).order_by(Preparation.created_at.desc(),Preparation.preparation_id.desc()).limit(WINDOW)))
        pending_id=db.scalar(select(Preparation.preparation_id).where(Preparation.session_id==id,Preparation.status=='PREPARED'))
        if pending_id is not None and pending_id not in {row.preparation_id for row in rows}:
            raise ValueError('Pending reservation outside inspection window')
        batches=[];active_cash=active_quantity=Decimal(0);pending_known=True
        for row in reversed(rows):
            from core.paper import preparation, lifecycle
            preparation.validate(row)
            reservation=check(db,record,row)
            if reservation is None:
                if row.status=='PREPARED':pending_known=False
                batches.append({'preparation_id':row.preparation_id,'status':row.status,'coverage':'LEGACY_UNAVAILABLE','reservation':None,'outcome':None})
                continue
            if row.status=='CONSUMED':
                lifecycle.check_coverage(db,record,row)
                payload=row.payload
                for obs in payload['observations']:
                    if obs not in record.observations:raise ValueError('Funded observations missing from account')
                ids={flow['order_id'] for flow in reservation.payload['flows']}
                for approved in authorization.rows(db,row.preparation_id):
                    if approved.order_id not in ids or approved.payload['proposed_order'] not in record.ledger['orders'] or (approved.payload['projected_fill'] is not None and approved.payload['projected_fill'] not in record.ledger['fills']):
                        raise ValueError('Funded receipt missing from account')
            if row.status=='PREPARED':
                if preparation.base(record)!=row.payload['base']:raise ValueError('Pending funding base changed')
                active_cash=Decimal(reservation.payload['reserved_cash']);active_quantity=Decimal(reservation.payload['reserved_quantity'])
            final=db.get(Outcome,row.preparation_id)
            batches.append({'preparation_id':row.preparation_id,'status':row.status,'coverage':'VERIFIED','reservation':reservation.payload,'outcome':final.payload if final else None})
        with localcontext() as context:
            context.prec=60
            available_cash=Decimal(record.ledger['account']['cash'])-active_cash
            available_quantity=Decimal(record.ledger['account']['quantity'])-active_quantity
            if available_cash<0 or available_quantity<0:raise ValueError('Active reservations exceed account')
            report={'version':VERSION,'session_id':id,'revision':record.revision,'total_batches':total,'window_limit':WINDOW,'has_older':total>WINDOW,
                'reserved_cash':str(active_cash) if pending_known else None,'reserved_quantity':str(active_quantity) if pending_known else None,'available_cash':str(available_cash) if pending_known else None,'available_quantity':str(available_quantity) if pending_known else None,
                'batches':batches,'mode':'READ_ONLY_PROJECTED_PAPER_FUNDING','trading_enabled':False,'external_submission_supported':False}
        if len(json.dumps(report,separators=(',',':')).encode())>MAX_BYTES:raise ValueError('Funding response exceeds 32 MiB')
        return report
