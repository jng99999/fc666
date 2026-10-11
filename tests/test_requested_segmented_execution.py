from copy import deepcopy
from decimal import Decimal
import pytest
import subprocess
import sys
from core.paper import requested_segmented_execution as segmented, requested_execution as frozen
from core.paper.fault_adapter import sha, encoded
from tests.test_requested_execution import request, event, fill, cumulative


def long_history():
    req = request()
    events = [event(req,0,'SUBMIT'), fill(req,1)]
    events += [event(req,i,'ACK') for i in range(2,1001)]
    events += [fill(req,1001,size='3',fee='2.7',identity='remaining',executed_seconds=1001),
               cumulative(req,1002,'4','3.6','360',seal=True)]
    return req, [events[i:i+128] for i in range(0,len(events),128)]


def test_more_than_frozen_limit_replays_without_resetting_hold_or_economics():
    req, batches = long_history()
    with pytest.raises(ValueError):
        frozen.reduce(req, [e for b in batches for e in b])
    report = segmented.replay(req, batches)
    assert segmented.verify(report) == report
    assert report['total_events'] == 1003
    assert report['segments'][0]['checkpoint']['funding']['reserved_cash'].startswith('303')
    assert Decimal(report['summary']['account']['cash']) == Decimal('636.4')
    assert Decimal(report['summary']['account']['quantity']) == 4
    assert report['summary']['unique_fills'] == 2
    assert report['summary']['local_source_sealed']
    assert report['ledger_application_supported'] is False


def test_append_preserves_segments_and_exact_retry_after_seal():
    req = request()
    first = segmented.replay(req, [[event(req,0,'SUBMIT'), fill(req,1)]])
    second = [fill(req,2,size='3',fee='2.7',identity='remaining',executed_seconds=2), cumulative(req,3,'4','3.6','360',seal=True)]
    final = segmented.append(first, second)
    assert final['segments'][0] == first['segments'][0]
    assert segmented.append(final, second) == final
    with pytest.raises(ValueError):
        segmented.append(final, [event(req,4,'ACK')])


def test_fill_deduplication_and_conflicts_cross_segment_boundaries():
    req = request()
    first = [event(req,0,'SUBMIT'), fill(req,1)]
    repeat = fill(req,2)
    result = segmented.replay(req, [first,[repeat]])
    assert result['summary']['unique_fills'] == 1
    assert Decimal(result['summary']['account']['cash']) == Decimal('909.1')
    conflict = deepcopy(repeat); conflict['payload']['quantity'] = '2'
    with pytest.raises(ValueError, match='fill identity'):
        segmented.replay(req, [first,[conflict]])


@pytest.mark.parametrize('mutation', [
    lambda v:v['segments'].pop(0), lambda v:v['segments'].reverse(),
    lambda v:v['segments'][0]['checkpoint']['account'].update(cash='999'),
    lambda v:v['segments'][1].update(previous_sha256='0'*64),
    lambda v:v.update(ledger_application_supported=True),
])
def test_resealed_segment_corruption_or_authority_rejected(mutation):
    req = request()
    value = segmented.replay(req, [[event(req,0,'SUBMIT')],[fill(req,1)]])
    mutation(value)
    for s in value['segments']:
        s['sha256'] = sha({k:v for k,v in s.items() if k != 'sha256'})
    value['sha256'] = sha({k:v for k,v in value.items() if k != 'sha256'})
    with pytest.raises(ValueError):
        segmented.verify(value)


def test_unknown_and_cancellation_holds_survive_boundaries():
    req = request()
    value = segmented.replay(req, [[event(req,0,'SUBMIT'),event(req,1,'UNKNOWN_SUBMISSION')],
                                   [event(req,2,'CANCEL_REQUEST'),event(req,3,'CANCEL_ACK')]])
    assert value['segments'][0]['checkpoint']['state'] == 'UNKNOWN_SUBMISSION'
    assert Decimal(value['summary']['funding']['reserved_cash']) == 404
    final = segmented.append(value, [fill(req,4),cumulative(req,5,'1','.9','90',seal=True)])
    assert final['summary']['state'] == 'PARTIAL_CANCELLED'
    assert Decimal(final['summary']['funding']['reserved_cash']) == 0


def test_count_and_byte_capacity_refuse_without_rewriting_prior_evidence(monkeypatch):
    req = request()
    prior = segmented.replay(req, [[event(req,0,'SUBMIT')]])
    original = deepcopy(prior)
    with monkeypatch.context() as limited:
        limited.setattr(segmented,'SEGMENT_BYTES',1)
        with pytest.raises(ValueError, match='payload'):
            segmented.replay(req, [[event(req,0,'SUBMIT')]])
    with monkeypatch.context() as limited:
        limited.setattr(segmented,'MAX_SEGMENTS',1)
        bounded = segmented.replay(req, [[event(req,0,'SUBMIT')]])
        with pytest.raises(ValueError, match='count'):
            segmented.append(bounded, [event(req,1,'ACK')])
    with monkeypatch.context() as limited:
        limited.setattr(segmented,'TOTAL_EVENTS',1)
        with pytest.raises(ValueError, match='event capacity'):
            segmented.replay(req, [[event(req,0,'SUBMIT')],[event(req,1,'ACK')]])
    with monkeypatch.context() as limited:
        limited.setattr(segmented,'MAX_BYTES',1)
        with pytest.raises(ValueError, match='byte capacity'):
            segmented.replay(req, [[event(req,0,'SUBMIT')]])
    assert prior == original


def test_global_event_identity_and_sequence_are_strict():
    req = request()
    first = event(req,0,'SUBMIT')
    second = event(req,1,'ACK'); second['event_id'] = first['event_id']
    with pytest.raises(ValueError, match='event identity'):
        segmented.replay(req, [[first],[second]])
    for sequence in [True,2]:
        second = event(req,1,'ACK'); second['sequence'] = sequence
        with pytest.raises(ValueError, match='sequence'):
            segmented.replay(req, [[first],[second]])


def test_real_segment_and_global_capacity_boundaries():
    req = request()
    events = [event(req,0,'SUBMIT')] + [event(req,i,'ACK') for i in range(1,8192)]
    batches = [events[i:i+128] for i in range(0,len(events),128)]
    value = segmented.replay(req,batches)
    assert value['total_events'] == 8192
    assert Decimal(value['summary']['funding']['reserved_cash']) == 404
    with pytest.raises(ValueError, match='event capacity'):
        segmented.replay(req,[*batches,[event(req,8192,'ACK')]])
    with pytest.raises(ValueError, match='payload'):
        segmented.replay(req,[events[:129]])
    with pytest.raises(ValueError, match='count'):
        segmented.replay(req,[[events[0]]] * 257)


def test_offline_cli_replays_long_export_and_rejects_duplicate_json_keys(tmp_path):
    req, batches = long_history()
    path = tmp_path / 'segmented.json'
    path.write_text(encoded(segmented.replay(req,batches)))
    args = [sys.executable,'-m','scripts.verify_requested_segmented',str(path)]
    result = subprocess.run(args,capture_output=True)
    assert result.returncode == 0 and b'1003' in result.stdout
    path.write_text('{"version":"one","version":"two"}')
    assert subprocess.run(args,capture_output=True).returncode == 1
