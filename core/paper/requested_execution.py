"""Versioned explicit-quantity Paper contract; deterministic, no persistence/I/O."""
from copy import deepcopy
from datetime import datetime
from decimal import Decimal, localcontext, ROUND_CEILING, ROUND_HALF_EVEN
from core.paper.fault_adapter import encoded, sha

VERSION='paper-requested-execution-v1'
QUANTUM=Decimal('1e-36')
MONEY_LIMIT=Decimal('1e40')
ASSET_LIMIT=Decimal('1e18')
EVENT_LIMIT=1000
MAX_BYTES=32*1024*1024
BASE_KEYS={'cash','quantity','cost_basis','realized_pnl','fees'}
RULE_KEYS={'tick_size','quantity_step','min_quantity','min_notional'}


def decimal(value, *, asset=False, signed=False, positive=False):
    if not isinstance(value,str) or len(value)>128:
        raise ValueError('Bounded decimal string required')
    result=Decimal(value)
    scale=18 if asset else 36
    limit=ASSET_LIMIT if asset else MONEY_LIMIT
    if (not result.is_finite() or len(result.as_tuple().digits)>80
        or abs(result.as_tuple().exponent)>80 or result.as_tuple().exponent < -scale
        or abs(result)>limit or (not signed and result<0) or (positive and result<=0)):
        raise ValueError('Decimal outside versioned financial bounds')
    return result


def clock(value):
    if not isinstance(value,str) or len(value)>64:
        raise ValueError('Timezone-aware clock required')
    result=datetime.fromisoformat(value)
    if result.tzinfo is None:raise ValueError('Timezone-aware clock required')
    return result


def base_values(base):
    if not isinstance(base,dict) or set(base)!=BASE_KEYS:
        raise ValueError('Invalid frozen account base')
    result={key:decimal(base[key],asset=key=='quantity',signed=key=='realized_pnl') for key in BASE_KEYS}
    if result['quantity']==0 and result['cost_basis']!=0:
        raise ValueError('Empty inventory cannot retain cost basis')
    return result


def reserve(side, quantity, price, fee_rate):
    if side=='SELL':return Decimal(0),quantity
    notional=quantity*price
    fee=(notional*fee_rate).quantize(QUANTUM,rounding=ROUND_CEILING)
    return notional+fee,Decimal(0)


def request(account_id, client_request_id, side, requested_quantity, limit_price, max_fee_rate, base, rules, created_at):
    """Freeze an explicit request and full reservation; no inferred old demand."""
    if any(not isinstance(value,str) or not value or len(value)>128 for value in [account_id,client_request_id]) or side not in ['BUY','SELL']:
        raise ValueError('Invalid request identity/side')
    clock(created_at)
    if not isinstance(rules,dict) or set(rules)!=RULE_KEYS:
        raise ValueError('Exact frozen market rules required')
    with localcontext() as context:
        context.prec=260
        account=base_values(base)
        size=decimal(requested_quantity,asset=True,positive=True)
        price=decimal(limit_price,asset=True,positive=True)
        rate=decimal(max_fee_rate,asset=True)
        if rate>1:raise ValueError('Fee rate exceeds one')
        tick=decimal(rules['tick_size'],asset=True,positive=True)
        step=decimal(rules['quantity_step'],asset=True,positive=True)
        minimum=decimal(rules['min_quantity'],asset=True,positive=True)
        min_notional=decimal(rules['min_notional'])
        if price%tick or size%step or size<minimum or size*price<min_notional:
            raise ValueError('Request violates frozen market rules')
        cash_hold,inventory_hold=reserve(side,size,price,rate)
        if cash_hold>account['cash'] or inventory_hold>account['quantity'] or (side=='BUY' and account['quantity']+size>ASSET_LIMIT):
            raise ValueError('Full requested quantity is not funded')
    value={'version':VERSION,'account_id':account_id,'client_request_id':client_request_id,'side':side,
           'requested_quantity':requested_quantity,'limit_price':limit_price,'max_fee_rate':max_fee_rate,
           'base':deepcopy(base),'rules':deepcopy(rules),'created_at':created_at,
           'mode':'LOCAL_PAPER_EXPLICIT_REQUEST_CONTRACT','external_submission_allowed':False}
    return {**value,'request_id':sha(value)}


def validate(value):
    keys={'version','account_id','client_request_id','side','requested_quantity','limit_price','max_fee_rate','base','rules','created_at','mode','external_submission_allowed','request_id'}
    if not isinstance(value,dict) or set(value)!=keys:
        raise ValueError('Invalid request envelope')
    expected=request(*(value[key] for key in ['account_id','client_request_id','side','requested_quantity','limit_price','max_fee_rate','base','rules','created_at']))
    if encoded(value)!=encoded(expected):raise ValueError('Request integrity/version mismatch')
    return expected


def reduce(value, events):
    value=validate(value)
    if not isinstance(events,list) or len(events)>EVENT_LIMIT or len(encoded(events).encode())>MAX_BYTES:
        raise ValueError('Invalid bounded transcript')
    by_id={};by_sequence={}
    for event in events:
        if not isinstance(event,dict) or set(event)!={'event_id','request_id','sequence','received_at','kind','payload'}:
            raise ValueError('Invalid event envelope')
        identity,sequence=event['event_id'],event['sequence']
        if not isinstance(identity,str) or not identity or len(identity)>128 or event['request_id']!=value['request_id'] or type(sequence) is not int or not 0<=sequence<EVENT_LIMIT:
            raise ValueError('Invalid event identity/sequence')
        if identity in by_id:
            if encoded(by_id[identity])!=encoded(event):raise ValueError('Conflicting event redelivery')
            continue
        if sequence in by_sequence:raise ValueError('Conflicting source sequence')
        by_id[identity]=event;by_sequence[sequence]=event
    if sorted(by_sequence)!=list(range(len(by_sequence))):raise ValueError('Incomplete source prefix')
    with localcontext() as context:
        context.prec=260
        account=base_values(value['base'])
        wanted=decimal(value['requested_quantity'],asset=True)
        limit=decimal(value['limit_price'],asset=True)
        rate=decimal(value['max_fee_rate'],asset=True)
        step=decimal(value['rules']['quantity_step'],asset=True)
        tick=decimal(value['rules']['tick_size'],asset=True)
        side=value['side']
        quantity=fees=notional=Decimal(0)
        fills={};receipt=None
        submitted=acknowledged=unknown=cancel_requested=cancelled=rejected=sealed=False
        previous=clock(value['created_at'])
        for sequence in sorted(by_sequence):
            event=by_sequence[sequence]
            stamp=clock(event['received_at'])
            if stamp<previous:raise ValueError('Receipt clocks regress before request/source order')
            previous=stamp
            kind,payload=event['kind'],event['payload']
            if sealed:raise ValueError('New event after local source seal requires separate reconciliation')
            if not isinstance(payload,dict):raise ValueError('Invalid event payload')
            if kind=='SUBMIT':
                if payload or submitted or sequence!=0:raise ValueError('Invalid submission')
                submitted=True
            elif not submitted:raise ValueError('Submission must precede updates')
            elif kind in ['ACK','UNKNOWN_SUBMISSION','CANCEL_REQUEST','CANCEL_ACK','REJECT']:
                if payload:raise ValueError('Unexpected control payload')
                if kind=='ACK':
                    if rejected:raise ValueError('Acknowledgement after rejection')
                    acknowledged=True;unknown=False
                elif kind=='UNKNOWN_SUBMISSION':
                    if acknowledged or quantity or rejected or cancelled:raise ValueError('Submission uncertainty cannot erase known facts')
                    unknown=True
                elif kind=='CANCEL_REQUEST':
                    if cancel_requested or rejected:raise ValueError('Conflicting cancellation request')
                    cancel_requested=True
                elif kind=='CANCEL_ACK':
                    if not cancel_requested or cancelled or rejected:raise ValueError('Cancellation acknowledgement lacks pending request')
                    cancelled=True
                else:
                    if quantity or cancelled or rejected:raise ValueError('Rejection conflicts with economic facts')
                    rejected=True;unknown=False
            elif kind=='FILL':
                if rejected or set(payload)!={'fill_id','quantity','price','fee','executed_at'}:
                    raise ValueError('Invalid fill or fill after rejection')
                identity=payload['fill_id']
                if not isinstance(identity,str) or not identity or len(identity)>128:
                    raise ValueError('Invalid fill identity')
                if identity in fills:
                    if encoded(fills[identity])!=encoded(payload):raise ValueError('Conflicting fill identity')
                    continue
                execution=clock(payload['executed_at'])
                if not clock(value['created_at'])<=execution<=stamp:
                    raise ValueError('Fill execution clock outside request/receipt bounds')
                size=decimal(payload['quantity'],asset=True,positive=True)
                price=decimal(payload['price'],asset=True,positive=True)
                fee=decimal(payload['fee'])
                amount=size*price
                if size%step or price%tick or quantity+size>wanted or fee>amount*rate:
                    raise ValueError('Fill violates quantity/grid/fee cap')
                if (side=='BUY' and price>limit) or (side=='SELL' and price<limit):
                    raise ValueError('Fill violates frozen protective limit')
                if side=='BUY':
                    debit=amount+fee
                    if debit>account['cash']:raise ValueError('Fill exceeds account cash')
                    account['cash']-=debit;account['quantity']+=size;account['cost_basis']+=debit
                else:
                    if size>account['quantity']:raise ValueError('Fill exceeds inventory')
                    basis=(account['cost_basis'] if size==account['quantity'] else
                           (account['cost_basis']*size/account['quantity']).quantize(QUANTUM,rounding=ROUND_HALF_EVEN))
                    account['cash']+=amount-fee;account['quantity']-=size;account['cost_basis']-=basis
                    account['realized_pnl']+=amount-fee-basis
                account['fees']+=fee
                base_values({key:str(account[key]) for key in BASE_KEYS})
                quantity+=size;fees+=fee;notional+=amount;fills[identity]=deepcopy(payload);unknown=False
            elif kind in ['RECEIPT','SEAL']:
                expected={'quantity','fees','notional'} | ({'last_sequence'} if kind=='SEAL' else set())
                if set(payload)!=expected:raise ValueError('Invalid cumulative receipt/seal')
                values=tuple(decimal(payload[key]) for key in ['quantity','fees','notional'])
                if values!=(quantity,fees,notional):raise ValueError('Cumulative receipt differs from unique fill ledger')
                if kind=='SEAL':
                    if type(payload['last_sequence']) is not int or payload['last_sequence']!=sequence-1 or not (quantity==wanted or cancelled or rejected):
                        raise ValueError('Local seal lacks complete source/terminal evidence')
                    sealed=True
                receipt=values
            else:raise ValueError('Unknown event kind')
        remaining=wanted-quantity
        cash_hold,inventory_hold=(Decimal(0),Decimal(0)) if sealed else reserve(side,remaining,limit,rate)
        if cash_hold>account['cash'] or inventory_hold>account['quantity']:
            raise ValueError('Remaining request is underfunded')
        state=('FILLED' if quantity==wanted else 'REJECTED' if rejected and sealed else 'REJECTED_UNCONFIRMED' if rejected else
               'PARTIAL_CANCELLED' if cancelled and sealed and quantity else 'CANCELLED' if cancelled and sealed else
               'PARTIAL_CANCEL_ACKNOWLEDGED' if cancelled and quantity else 'CANCEL_ACKNOWLEDGED' if cancelled else
               'CANCEL_PENDING' if cancel_requested else 'PARTIALLY_FILLED' if quantity else
               'ACKNOWLEDGED' if acknowledged else 'UNKNOWN_SUBMISSION' if unknown else 'SUBMITTED' if submitted else 'CREATED')
        return {'version':VERSION,'request_id':value['request_id'],'requested_quantity':str(wanted),'state':state,
                'filled_quantity':str(quantity),'unfilled_quantity':str(remaining),'active_unfilled_quantity':str(Decimal(0) if sealed else remaining),
                'cancelled_quantity':str(remaining) if sealed and cancelled and quantity<wanted else '0' if sealed else None,
                'cumulative_fees':str(fees),'cumulative_notional':str(notional),'unique_fills':len(fills),'source_events':len(by_sequence),
                'receipt_confirmed':receipt is not None and receipt==(quantity,fees,notional),'local_source_sealed':sealed,
                'account':{key:str(account[key]) for key in sorted(BASE_KEYS)},
                'funding':{'reserved_cash':str(cash_hold),'reserved_quantity':str(inventory_hold),
                           'available_cash':str(account['cash']-cash_hold),'available_quantity':str(account['quantity']-inventory_hold)},
                'execution_enabled':False,'external_reconciliation_supported':False}


def capture(value, events):
    summary=reduce(value,events)
    result={'version':VERSION,'request':deepcopy(value),'events':deepcopy(events),'summary':summary}
    report={**result,'sha256':sha(result)}
    if len(encoded(report).encode())>MAX_BYTES:raise ValueError('Export exceeds 32 MiB')
    return report


def verify(report):
    if not isinstance(report,dict) or set(report)!={'version','request','events','summary','sha256'} or report['version']!=VERSION:
        raise ValueError('Unknown export envelope/version')
    if len(encoded(report).encode())>MAX_BYTES or encoded(report)!=encoded(capture(report['request'],report['events'])):
        raise ValueError('Requested execution export differs from deterministic replay')
    return report['summary']
