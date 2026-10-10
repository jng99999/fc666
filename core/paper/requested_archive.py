"""Bounded financial-only archive transport; never authorizes retention or execution."""
from copy import deepcopy
import json
from core.paper import requested_inspection as inspection
from core.paper.fault_adapter import encoded, sha

VERSION = 'paper-requested-segmented-archive-v1'
SEGMENT_BYTES = 65536
MAX_BYTES = 70 * 1024 * 1024
KEYS = {'version', 'scope', 'export_sha256', 'byte_length', 'segments', 'sha256'}
OPERATIONAL_VERSION = 'paper-requested-segmented-operational-archive-v1'
OPERATIONAL_VERSION_V2 = 'paper-requested-segmented-operational-archive-v2'


def payload_contract(report):
    from core.paper import requested_operational_archive as operational
    if isinstance(report, dict) and report.get('version') in [operational.VERSION, operational.VERSION_V2]:
        operational.verify(report)
        return (OPERATIONAL_VERSION_V2, operational.SCOPE_V2) if report['version'] == operational.VERSION_V2 else (OPERATIONAL_VERSION, operational.SCOPE)
    inspection.verify(report)
    return VERSION, inspection.SCOPE


def pack(report):
    version, scope = payload_contract(report)
    raw = encoded(report)
    segments = []
    previous = report['sha256']
    for offset in range(0, len(raw), SEGMENT_BYTES):
        body = dict(ordinal=len(segments), offset=offset, previous_sha256=previous,
                    data=raw[offset:offset + SEGMENT_BYTES])
        segment = {**body, 'sha256': sha(body)}
        segments.append(segment)
        previous = segment['sha256']
    body = dict(version=version, scope=scope, export_sha256=report['sha256'],
                byte_length=len(raw), segments=segments)
    result = {**body, 'sha256': sha(body)}
    if len(encoded(result)) > MAX_BYTES:
        raise ValueError('Archive exceeds bounds')
    return result


def verify(value):
    from core.paper import requested_operational_archive as operational
    if not isinstance(value, dict) or set(value) != KEYS or (value['version'], value['scope']) not in [(VERSION, inspection.SCOPE), (OPERATIONAL_VERSION, operational.SCOPE), (OPERATIONAL_VERSION_V2, operational.SCOPE_V2)]:
        raise ValueError('Unsupported archive envelope')
    size = value['byte_length']
    if type(size) is not int or not 0 < size <= 32 * 1024 * 1024:
        raise ValueError('Invalid archive length')
    segments = value['segments']
    if not isinstance(segments, list) or len(segments) != (size + SEGMENT_BYTES - 1) // SEGMENT_BYTES:
        raise ValueError('Incomplete archive segments')
    if len(encoded(value)) > MAX_BYTES:
        raise ValueError('Archive exceeds bounds')
    previous = value['export_sha256']
    chunks = []
    for ordinal, segment in enumerate(segments):
        if not isinstance(segment, dict) or set(segment) != {'ordinal', 'offset', 'previous_sha256', 'data', 'sha256'}:
            raise ValueError('Invalid archive segment')
        data = segment['data']
        if (type(segment['ordinal']) is not int or segment['ordinal'] != ordinal or
            type(segment['offset']) is not int or segment['offset'] != ordinal * SEGMENT_BYTES or
            segment['previous_sha256'] != previous or type(data) is not str or not data.isascii() or
            len(data) != min(SEGMENT_BYTES, size - ordinal * SEGMENT_BYTES)):
            raise ValueError('Archive sequence or length differs')
        if segment['sha256'] != sha({k: v for k, v in segment.items() if k != 'sha256'}):
            raise ValueError('Archive segment hash differs')
        previous = segment['sha256']
        chunks.append(data)
    raw = ''.join(chunks)
    report = json.loads(raw)
    if raw != encoded(report) or report.get('sha256') != value['export_sha256']:
        raise ValueError('Archive canonical export differs')
    payload_contract(report)
    if encoded(value) != encoded(pack(report)):
        raise ValueError('Archive manifest differs')
    return deepcopy(report)


def capture(engine, account_id):
    return pack(inspection.capture(engine, account_id))
