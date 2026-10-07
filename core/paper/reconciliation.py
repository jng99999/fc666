"""Isolated Paper-only cumulative reconciliation; no exchange or ledger writes."""
from decimal import Decimal, localcontext

VERSION = 'paper-fault-reconciliation-v1'
LIMIT = 1000


def number(value, positive=False):
    if not isinstance(value, str):
        raise ValueError('Decimal inputs must be strings')
    result = Decimal(value)
    if (not result.is_finite() or result < 0 or (positive and result == 0)
            or len(result.as_tuple().digits) > 40 or abs(result.as_tuple().exponent) > 40):
        raise ValueError('Invalid Decimal input')
    return result


def reconcile(order_id, requested_quantity, events):
    """Fold a complete bounded fixture transcript, preserving late economic facts.

    Transport order is irrelevant. Sequence is source order, not arrival time.
    Cancellation is not a watermark proving all fills have arrived. A cumulative
    receipt confirms reconciliation only when exact quantity/fee/notional match.
    This contract intentionally has no adapter, persistence or execution gate.
    """
    if not isinstance(order_id, str) or not order_id or not isinstance(events, list) or len(events) > LIMIT:
        raise ValueError('Invalid or oversized transcript')
    requested = number(requested_quantity, positive=True)
    by_id, by_sequence = {}, {}
    for event in events:
        if not isinstance(event, dict) or set(event) != {'event_id','order_id','sequence','kind','payload'}:
            raise ValueError('Invalid event envelope')
        identity, sequence = event['event_id'], event['sequence']
        if not isinstance(identity, str) or not identity or event['order_id'] != order_id:
            raise ValueError('Invalid event identity')
        if type(sequence) is not int or sequence < 0:
            raise ValueError('Invalid source sequence')
        if identity in by_id:
            if by_id[identity] != event:
                raise ValueError('Conflicting duplicate event')
            continue
        if sequence in by_sequence:
            raise ValueError('Conflicting source sequence')
        by_id[identity] = event
        by_sequence[sequence] = event
    if sorted(by_sequence) != list(range(len(by_sequence))):
        raise ValueError('Incomplete source sequence')
    with localcontext() as context:
        context.prec = 260
        quantity = fees = notional = Decimal(0)
        fills = {}
        submitted = acknowledged = cancel_requested = cancelled = unknown = False
        receipt = None
        for sequence in sorted(by_sequence):
            event = by_sequence[sequence]
            kind, payload = event['kind'], event['payload']
            if not isinstance(payload, dict):
                raise ValueError('Invalid payload')
            if kind == 'SUBMIT':
                if payload or submitted or sequence != 0:
                    raise ValueError('Invalid submission')
                submitted = True
            elif not submitted:
                raise ValueError('Submission must precede updates')
            elif kind in {'UNKNOWN_SUBMISSION','ACK','CANCEL_REQUEST','CANCEL_ACK'}:
                if payload:
                    raise ValueError('Unexpected control payload')
                if kind == 'UNKNOWN_SUBMISSION':
                    if acknowledged or cancelled:
                        raise ValueError('Unknown submission after acknowledgement')
                    unknown = True
                elif kind == 'ACK':
                    acknowledged, unknown = True, False
                elif kind == 'CANCEL_REQUEST':
                    if cancel_requested or cancelled:
                        raise ValueError('Repeated cancellation requires same event identity')
                    cancel_requested = True
                else:
                    if not cancel_requested or cancelled:
                        raise ValueError('Cancellation acknowledgement without request')
                    cancelled = True
            elif kind == 'FILL':
                if set(payload) != {'fill_id','quantity','price','fee'} or not isinstance(payload['fill_id'], str) or not payload['fill_id']:
                    raise ValueError('Invalid fill')
                fill_id = payload['fill_id']
                if fill_id in fills:
                    if fills[fill_id] != payload:
                        raise ValueError('Conflicting fill identity')
                    continue
                q, p, f = number(payload['quantity'], True), number(payload['price'], True), number(payload['fee'])
                quantity, fees, notional = quantity + q, fees + f, notional + q*p
                if quantity > requested:
                    raise ValueError('Overfilled order')
                fills[fill_id] = payload
                unknown = False
            elif kind == 'RECEIPT':
                if set(payload) != {'quantity','fees','notional'}:
                    raise ValueError('Invalid cumulative receipt')
                values = tuple(number(payload[key]) for key in ('quantity','fees','notional'))
                if values != (quantity, fees, notional):
                    raise ValueError('Cumulative receipt differs from fill ledger')
                receipt = values
            else:
                raise ValueError('Unknown event kind')
        confirmed = receipt is not None and receipt == (quantity, fees, notional)
        state = ('FILLED' if quantity == requested else 'PARTIAL_CANCELLED' if cancelled and quantity else
                 'CANCELLED' if cancelled else 'CANCEL_PENDING' if cancel_requested else
                 'PARTIALLY_FILLED' if quantity else 'ACKNOWLEDGED' if acknowledged else
                 'UNKNOWN_SUBMISSION' if unknown else 'SUBMITTED' if submitted else 'NOT_SUBMITTED')
        return {'version':VERSION, 'order_id':order_id, 'state':state,
                'quantity':str(quantity), 'fees':str(fees), 'notional':str(notional),
                'unique_fills':len(fills), 'receipt_confirmed':confirmed,
                'execution_enabled':False, 'external_reconciliation_supported':False}
