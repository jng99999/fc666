"""Versioned bounded segmented replay prerequisite; no ledger or remote execution."""
from copy import deepcopy
from core.paper import requested_execution as settlement
from core.paper.fault_adapter import encoded, sha

VERSION = 'paper-requested-segmented-replay-v1'
SEGMENT_EVENTS = 128
SEGMENT_BYTES = 128 * 1024
MAX_SEGMENTS = 256
TOTAL_EVENTS = 8192
MAX_BYTES = 32 * 1024 * 1024


def replay(request, batches):
    request = settlement.validate(request)
    if not isinstance(batches, list) or len(batches) > MAX_SEGMENTS:
        raise ValueError('Segment count exceeds capacity')
    if len(encoded(batches)) > MAX_BYTES:
        raise ValueError('Segmented input exceeds byte capacity')
    events = []; identities = set(); segments = []
    header = dict(version=VERSION, request=request, limits=dict(segment_events=SEGMENT_EVENTS,
                  segment_bytes=SEGMENT_BYTES, segments=MAX_SEGMENTS, total_events=TOTAL_EVENTS,
                  total_bytes=MAX_BYTES))
    previous = sha(header)
    checkpoint = settlement._reduce(request, [], event_limit=TOTAL_EVENTS)
    for ordinal, batch in enumerate(batches):
        if not isinstance(batch, list) or not 1 <= len(batch) <= SEGMENT_EVENTS or len(encoded(batch)) > SEGMENT_BYTES:
            raise ValueError('Invalid bounded segment payload')
        if len(events) + len(batch) > TOTAL_EVENTS:
            raise ValueError('Segmented event capacity exceeded')
        start = len(events)
        for event in batch:
            if not isinstance(event, dict) or type(event.get('sequence')) is not int or event['sequence'] != len(events):
                raise ValueError('Noncontiguous global source sequence')
            identity = event.get('event_id')
            if type(identity) is not str or identity in identities:
                raise ValueError('Duplicate global event identity')
            identities.add(identity); events.append(deepcopy(event))
        checkpoint = settlement._reduce(request, events, event_limit=TOTAL_EVENTS)
        body = dict(ordinal=ordinal, start_sequence=start, end_sequence=len(events)-1,
                    previous_sha256=previous, events=deepcopy(batch), checkpoint=checkpoint)
        segment = {**body, 'sha256':sha(body)}
        if len(encoded(segment)) > SEGMENT_BYTES:
            raise ValueError('Segment with checkpoint exceeds byte capacity')
        segments.append(segment); previous = segment['sha256']
    body = {**header, 'segments':segments, 'summary':checkpoint, 'total_events':len(events),
            'tail_sha256':previous, 'read_only':True, 'ledger_application_supported':False,
            'external_submission_allowed':False}
    result = {**body, 'sha256':sha(body)}
    if len(encoded(result)) > MAX_BYTES:
        raise ValueError('Segmented export exceeds byte capacity')
    return result


def verify(value):
    keys = {'version','request','limits','segments','summary','total_events','tail_sha256','read_only','ledger_application_supported','external_submission_allowed','sha256'}
    if not isinstance(value, dict) or set(value) != keys or value['version'] != VERSION or len(encoded(value)) > MAX_BYTES:
        raise ValueError('Invalid segmented replay export')
    if not isinstance(value['segments'], list) or len(value['segments']) > MAX_SEGMENTS:
        raise ValueError('Segment count exceeds capacity')
    if any(not isinstance(s, dict) or 'events' not in s for s in value['segments']):
        raise ValueError('Missing segmented payload')
    expected = replay(value['request'], [s['events'] for s in value['segments']])
    if encoded(value) != encoded(expected):
        raise ValueError('Segmented checkpoints differ from complete replay')
    return deepcopy(expected)


def append(value, batch):
    """Append an immutable batch; exact final-batch retry preserves the artifact."""
    current = verify(value)
    if current['segments'] and encoded(batch) == encoded(current['segments'][-1]['events']):
        return current
    result = replay(current['request'], [s['events'] for s in current['segments']] + [batch])
    if encoded(result['segments'][:-1]) != encoded(current['segments']):
        raise ValueError('Append rewrote historical segment evidence')
    return result
