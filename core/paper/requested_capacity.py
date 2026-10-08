"""Versioned structural event headroom; never predicts future venue facts."""
from copy import deepcopy
from core.paper import requested_execution as contract, requested_inspection as inspection
from core.paper.fault_adapter import encoded, sha

VERSION = 'paper-requested-capacity-v1'
REQUEST_LIMIT = 100
ACCOUNT_EVENT_LIMIT = 1000
MIN_NEW_EVENTS = 3  # SUBMIT, full FILL (or REJECT), SEAL.


def require_new(financial, event_limit):
    """Current admission only; never apply a new gate to historical proof replay."""
    if event_limit - financial['total_events'] < MIN_NEW_EVENTS:
        raise ValueError('Account source history capacity lacks three-event preparation headroom')


def require_information(financial,entry,event,projected,event_limit):
    """Current write policy only; do not retrofit historical replay or receipts."""
    repeated_ack=event['kind']=='ACK' and any(old['kind']=='ACK' for old in entry['events'])
    repeated_receipt=event['kind']=='RECEIPT' and entry['summary']['receipt_confirmed']
    if not (repeated_ack or repeated_receipt):return
    needed=1 if (contract.decimal(projected['unfilled_quantity'])==0 or projected['state'] in
                 ('REJECTED_UNCONFIRMED','CANCEL_ACKNOWLEDGED','PARTIAL_CANCEL_ACKNOWLEDGED')) else 2
    remaining=min(event_limit-financial['total_events']-1,contract.EVENT_LIMIT-len(entry['events'])-1)
    if remaining<needed:
        raise ValueError('Repeated information would consume minimum terminal event headroom')


def evaluate(journal_report):
    financial = inspection.verify(journal_report)
    remaining = ACCOUNT_EVENT_LIMIT - financial['total_events']
    requests_left = REQUEST_LIMIT - len(financial['requests'])
    active = next((entry for entry in financial['requests']
                   if entry['request']['request_id'] == financial['active_request_id']), None)
    headroom = None
    if active is not None:
        summary = active['summary']
        state = summary['state']
        full = contract.decimal(summary['unfilled_quantity']) == 0
        if not active['events']:
            needed = 3
        elif (full or state in
              ('REJECTED_UNCONFIRMED', 'CANCEL_ACKNOWLEDGED', 'PARTIAL_CANCEL_ACKNOWLEDGED')):
            needed = 1
        else:
            # Either fill the remaining quantity then SEAL, or reach a known
            # cancellation/rejection then SEAL. Future facts are not assured.
            needed = 2
        available = min(remaining, contract.EVENT_LIMIT - len(active['events']))
        headroom = {'request_id': active['request']['request_id'], 'state': state,
                    'remaining_event_slots': available, 'shortest_terminal_path_events': needed,
                    'shortest_terminal_path_fits': available >= needed,
                    'funding': deepcopy(summary['funding'])}
    fits = remaining >= MIN_NEW_EVENTS and requests_left > 0
    body = {'version': VERSION, 'journal': deepcopy(journal_report),
            'account_id': financial['opening']['account_id'], 'financial_revision': financial['revision'],
            'limits': {'account_events': ACCOUNT_EVENT_LIMIT, 'requests': REQUEST_LIMIT,
                       'per_request_events': contract.EVENT_LIMIT},
            'remaining_event_slots': remaining, 'remaining_request_slots': requests_left,
            'new_request_minimum_events': MIN_NEW_EVENTS,
            'new_request_capacity_fits': fits,
            'idle_account_capacity_fits': fits and active is None,
            'active_request': headroom, 'future_settlement_guaranteed': False,
            'submission_allowed': False, 'read_only': True}
    result = {**body, 'sha256': sha(body)}
    if len(encoded(result).encode()) > contract.MAX_BYTES:
        raise ValueError('Capacity export exceeds 32 MiB')
    return result


def capture(engine, account_id):
    from core.paper import requested_journal as journal
    return evaluate(inspection.seal(journal.read(engine, account_id, lock_timeout_ms=2000)))


def verify(report):
    if (not isinstance(report, dict) or report.get('version') != VERSION
            or len(encoded(report).encode()) > contract.MAX_BYTES):
        raise ValueError('Invalid capacity envelope/version/bounds')
    expected = evaluate(report.get('journal'))
    if encoded(report) != encoded(expected):
        raise ValueError('Capacity differs from complete evidence replay')
    return deepcopy(expected)
