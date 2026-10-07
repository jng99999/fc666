from copy import deepcopy
import pytest
from core.paper.reconciliation import reconcile


def event(seq, kind, payload=None):
    return {'event_id':f'e{seq}', 'order_id':'order', 'sequence':seq, 'kind':kind, 'payload':payload or {}}


def fill(seq, quantity='1', price='10', fee='0.1', identity='fill'):
    return event(seq, 'FILL', {'fill_id':identity,'quantity':quantity,'price':price,'fee':fee})


def receipt(seq, quantity='1', fees='0.1', notional='10'):
    return event(seq, 'RECEIPT', {'quantity':quantity,'fees':fees,'notional':notional})


def run(events, requested='2'):
    return reconcile('order', requested, events)


def test_cancel_late_fill_and_receipt_reconcile_without_execution():
    events = [event(0,'SUBMIT'),event(1,'ACK'),event(2,'CANCEL_REQUEST'),event(3,'CANCEL_ACK'),fill(4),receipt(5)]
    result = run(events)
    assert result['state'] == 'PARTIAL_CANCELLED'
    assert result['receipt_confirmed'] and result['unique_fills'] == 1
    assert not result['execution_enabled'] and not result['external_reconciliation_supported']
    assert run(list(reversed(events))+events) == result


def test_new_late_fill_invalidates_previous_receipt():
    events = [event(0,'SUBMIT'),event(1,'CANCEL_REQUEST'),event(2,'CANCEL_ACK'),receipt(3,'0','0','0'),fill(4)]
    assert not run(events)['receipt_confirmed']
    assert run(events+[receipt(5)])['receipt_confirmed']


def test_full_fill_wins_cancel_ack_without_losing_costs():
    events = [event(0,'SUBMIT'),fill(1),event(2,'CANCEL_REQUEST'),event(3,'CANCEL_ACK'),fill(4,'1','12','0.2','second'),receipt(5,'2','0.3','22')]
    result = run(events)
    assert result['state'] == 'FILLED'
    assert (result['quantity'],result['fees'],result['notional']) == ('2','0.3','22')


def test_same_fill_different_delivery_id_is_deduplicated():
    result = run([event(0,'SUBMIT'),fill(1),fill(2),receipt(3)])
    assert result['quantity'] == '1' and result['unique_fills'] == 1


def test_unknown_submission_stays_closed_until_ack_or_fill():
    events = [event(0,'SUBMIT'),event(1,'UNKNOWN_SUBMISSION')]
    assert run(events)['state'] == 'UNKNOWN_SUBMISSION'
    assert run(events+[event(2,'ACK')])['state'] == 'ACKNOWLEDGED'
    assert run(events+[fill(2)])['state'] == 'PARTIALLY_FILLED'
    assert not run(events)['receipt_confirmed']


@pytest.mark.parametrize('quantity', ['0','-1','NaN','Infinity','1e1000'])
def test_invalid_fill_quantity(quantity):
    with pytest.raises(ValueError): run([event(0,'SUBMIT'),fill(1,quantity)])


@pytest.mark.parametrize('events', [
    [fill(0)], [event(0,'SUBMIT'),event(2,'ACK')],
    [event(0,'SUBMIT'),event(1,'CANCEL_ACK')],
    [event(0,'SUBMIT'),fill(1,'3')],
    [event(0,'SUBMIT'),fill(1),receipt(2,'1','0.2','10')],
    [event(0,'SUBMIT'),fill(1),fill(2,'1','11')],
    [event(0,'SUBMIT'),event(1,'UNKNOWN_KIND')],
    [event(0,'SUBMIT'),event(1,'ACK'),event(2,'UNKNOWN_SUBMISSION')],
])
def test_conflicting_or_incomplete_transcripts_rejected(events):
    with pytest.raises(ValueError): run(events)


def test_identity_conflicts_and_input_immutability():
    events = [event(0,'SUBMIT'),fill(1)]
    original = deepcopy(events)
    run(events)
    assert events == original
    bad = deepcopy(events[1]); bad['payload']['price'] = '20'
    with pytest.raises(ValueError): run(events+[bad])
    bad = event(1,'ACK')
    bad['event_id'] = 'other'
    with pytest.raises(ValueError): run(events+[bad])


def test_bounded_transcript():
    with pytest.raises(ValueError): run([event(0,'SUBMIT')]*1001)
