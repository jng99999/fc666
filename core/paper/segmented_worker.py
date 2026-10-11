"""Trusted internal local worker fences, not operator or venue authorization."""
from copy import deepcopy
from sqlalchemy import select, text
from core.paper import segmented_ledger as ledger
from core.paper.fault_adapter import encoded, sha
from core.paper.requested_journal import transaction
from core.storage.models import SegmentedPaperWorkerRecord as Record

VERSION = 'paper-segmented-local-worker-v1'
CLAIMS = 64
RECORDS = 576
RECORD_BYTES = 16384
SCOPE = {'scope': 'TRUSTED_LOCAL_WORKER_FENCING_ONLY', 'external_submission_allowed': False}


def clock(db):
    return db.scalar(text('SELECT (extract(epoch FROM clock_timestamp()) * 1000000)::bigint'))


def _identity(value):
    if not isinstance(value, str) or not 1 <= len(value) <= 128:
        raise ValueError('Bounded stable worker/command identity required')
    return value


def enrolled(db, identity):
    return db.scalar(select(Record.sequence).where(Record.ledger_id == identity).limit(1)) is not None


def audit(db, row, nodes):
    records = list(db.scalars(select(Record).where(Record.ledger_id == row.ledger_id).order_by(Record.sequence).limit(RECORDS + 1)))
    for index, record in enumerate(records):
        value = record.payload
        if (not isinstance(value, dict) or record.sequence != index or value.get('sequence') != record.sequence
                or record.kind != value.get('kind') or record.command_id != value.get('command_id')
                or record.payload_sha256 != value.get('sha256')):
            raise ValueError('Persisted worker metadata differs from evidence')
    _replay(row.ledger_id, row.worker_version, [node.payload for node in nodes], [record.payload for record in records])
    return records


def _replay(ledger_id, worker_version, nodes, records):
    # Financial nodes must already have passed the native financial replay.
    # Shared by database auditing and the complete offline operational archive.
    if not isinstance(records, list):
        raise ValueError('Exact worker record list required')
    if worker_version is None:
        if records: raise ValueError('Undeclared worker fencing evidence')
        return []
    if worker_version != VERSION or not records:
        raise ValueError('Missing or unsupported worker fencing evidence')
    if len(records) > RECORDS:
        raise ValueError('Worker record capacity exceeded')
    parent = None; previous_us = 0; head = 0; claim = None; identities = set()
    for index, value in enumerate(records):
        keys = {'version', 'ledger_id', 'sequence', 'parent_sha256', 'kind', 'command_id',
                'commit_index', 'node_sha256', 'recorded_us', 'payload', 'sha256'}
        if (not isinstance(value, dict) or set(value) != keys or value['version'] != VERSION
                or value['ledger_id'] != ledger_id or type(value['sequence']) is not int
                or value['sequence'] != index
                or value['parent_sha256'] != parent
                or value['sha256'] != sha({key: item for key, item in value.items() if key != 'sha256'})
                or len(ledger.json_text(value).encode()) > RECORD_BYTES
                or type(value['recorded_us']) is not int or not previous_us <= value['recorded_us'] <= 2**63-1
                or value['recorded_us'] <= 0 or type(value['commit_index']) is not int):
            raise ValueError('Worker record identity/lineage mismatch')
        _identity(value['command_id'])
        key = (value['kind'], value['command_id'])
        if key in identities: raise ValueError('Duplicate worker command')
        identities.add(key)
        payload = value['payload']; point = value['commit_index']; now = value['recorded_us']
        if index == 0:
            if value['kind'] != 'ENROLL' or encoded(payload) != encoded(SCOPE) or point != 0:
                raise ValueError('Worker enrollment must bind financial root')
        elif value['kind'] == 'CLAIM':
            if (not isinstance(payload, dict) or set(payload) != {'owner', 'token', 'ttl_seconds', 'expires_us'}
                    or type(payload['token']) is not int or payload['token'] != (claim['token'] + 1 if claim else 1)
                    or payload['token'] > CLAIMS or type(payload['ttl_seconds']) is not int
                    or not 1 <= payload['ttl_seconds'] <= 60 or type(payload['expires_us']) is not int
                    or payload['expires_us'] != now + payload['ttl_seconds'] * 1000000
                    or payload['expires_us'] > 2**63-1 or point != head
                    or (claim is not None and now < claim['expires_us'])):
                raise ValueError('Invalid worker lease/takeover')
            _identity(payload['owner']); claim = payload
        elif value['kind'] == 'ACCEPT':
            if (claim is None or not isinstance(payload, dict) or set(payload) != {'owner', 'token'}
                    or type(payload['token']) is not int or payload['token'] != claim['token']
                    or payload['owner'] != claim['owner'] or now >= claim['expires_us'] or point != head + 1
                    or point >= len(nodes) or nodes[point]['command']['command_id'] != value['command_id']):
                raise ValueError('Financial admission has stale/missing worker fence')
            head = point
        else:
            raise ValueError('Invalid worker transition')
        if not 0 <= point < len(nodes) or value['node_sha256'] != nodes[point]['sha256']:
            raise ValueError('Worker checkpoint differs from financial evidence')
        parent = value['sha256']; previous_us = now
    if head != len(nodes) - 1:
        raise ValueError('Bound ledger financial commit lacks worker admission')
    return deepcopy(records)


def _append(db, row, records, kind, identity, index, node_hash, payload, now):
    if len(records) >= RECORDS: raise ValueError('Worker record capacity exceeded')
    if records and now < records[-1].payload['recorded_us']: raise ValueError('Database clock regressed')
    body = dict(version=VERSION, ledger_id=row.ledger_id, sequence=len(records),
                parent_sha256=records[-1].payload_sha256 if records else None, kind=kind,
                command_id=_identity(identity), commit_index=index, node_sha256=node_hash,
                recorded_us=now, payload=deepcopy(payload))
    value = {**body, 'sha256': sha(body)}
    if len(ledger.json_text(value).encode()) > RECORD_BYTES: raise ValueError('Worker record byte capacity exceeded')
    db.add(Record(ledger_id=row.ledger_id, sequence=len(records), kind=kind, command_id=identity,
                  payload=value, payload_sha256=value['sha256']))
    db.flush()
    return deepcopy(value)


def enroll(engine, ledger_id, command_id):
    _identity(command_id)
    with transaction(engine) as db:
        row = ledger._lock(db, ledger_id); nodes = ledger._audit(db, row); records = audit(db, row, nodes)
        if records:
            if records[0].command_id != command_id: raise ValueError('Conflicting enrollment retry')
            return deepcopy(records[0].payload)
        if row.commit_index != 0: raise ValueError('Cannot enroll retrospectively after financial work')
        row.worker_version = VERSION
        db.flush()
        result = _append(db, row, records, 'ENROLL', command_id, 0, row.root_sha256, SCOPE, clock(db))
        ledger._audit(db, row)
        return result


def claim(engine, ledger_id, command_id, owner, *, expected_sha256, expected_token, ttl_seconds=30):
    _identity(command_id); _identity(owner)
    if type(expected_token) is not int or not 0 <= expected_token < CLAIMS or type(ttl_seconds) is not int or not 1 <= ttl_seconds <= 60:
        raise ValueError('Invalid worker token/duration')
    with transaction(engine) as db:
        row = ledger._lock(db, ledger_id); nodes = ledger._audit(db, row); records = audit(db, row, nodes)
        if not records: raise ValueError('Explicit local worker enrollment required')
        now = clock(db)
        if now < records[-1].payload['recorded_us']: raise ValueError('Database clock regressed')
        claims = [record for record in records if record.kind == 'CLAIM']
        last = claims[-1] if claims else None
        old = next((record for record in claims if record.command_id == command_id), None)
        if old:
            value = old.payload; payload = value['payload']
            if (old is not last or payload['owner'] != owner or payload['token'] != expected_token + 1
                    or payload['ttl_seconds'] != ttl_seconds or value['node_sha256'] != expected_sha256
                    or not value['recorded_us'] <= now < payload['expires_us']):
                raise ValueError('Conflicting/expired worker claim retry')
            return deepcopy(value)
        if expected_sha256 != row.head_sha256 or expected_token != (last.payload['payload']['token'] if last else 0):
            raise ValueError('Stale worker acquisition checkpoint')
        if last and now < last.payload['payload']['expires_us']: raise ValueError('Current worker lease still active')
        result = _append(db, row, records, 'CLAIM', command_id, row.commit_index, row.head_sha256,
                         dict(owner=owner, token=expected_token + 1, ttl_seconds=ttl_seconds,
                              expires_us=now + ttl_seconds * 1000000), now)
        ledger._audit(db, row)
        return result


def execute(engine, ledger_id, owner, token, command):
    _identity(owner)
    if type(token) is not int: raise ValueError('Strict worker fencing token required')
    command = ledger._command(command)
    with transaction(engine) as db:
        row = ledger._lock(db, ledger_id); nodes = ledger._audit(db, row); records = audit(db, row, nodes)
        claims = [record.payload for record in records if record.kind == 'CLAIM']
        if not claims: raise ValueError('Current worker lease required')
        active = claims[-1]; lease = active['payload']; now = clock(db)
        if now < records[-1].payload['recorded_us']: raise ValueError('Database clock regressed')
        if owner != lease['owner'] or token != lease['token'] or not active['recorded_us'] <= now < lease['expires_us']:
            raise ValueError('Expired or fenced worker')
        old = next((node for node in nodes if node.command_id == command['command_id']), None)
        result = ledger._execute_locked(db, row, nodes, command, final_audit=False)
        if old is None:
            accepted = clock(db)
            if not active['recorded_us'] <= accepted < lease['expires_us']: raise ValueError('Worker lease expired during settlement')
            _append(db, row, records, 'ACCEPT', command['command_id'], result['commit_index'], result['node_sha256'],
                    dict(owner=owner, token=token), accepted)
        ledger._audit(db, row)
        return result
