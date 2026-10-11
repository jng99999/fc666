"""Complete financial and declared local worker evidence; never trading authority."""
from copy import deepcopy
from core.paper import segmented_ledger as ledger, segmented_worker as worker
from core.paper import segmented_ledger_archive as financial
from core.paper.fault_adapter import encoded, sha
from core.paper.requested_journal import transaction

VERSION = 'paper-segmented-local-operational-archive-v1'
SCOPE = 'FINANCIAL_AND_LOCAL_WORKER_FENCES'
MAX_BYTES = financial.MAX_BYTES + worker.RECORDS * worker.RECORD_BYTES + 16384


def verify(value):
    keys = {'version', 'scope', 'ledger_id', 'financial', 'worker_version', 'worker_records',
            'worker_root_sha256', 'worker_head_sha256', 'local_worker_evidence_included',
            'operator_worker_binding_supported', 'external_submission_allowed', 'sha256'}
    if (not isinstance(value, dict) or set(value) != keys or value['version'] != VERSION
            or value['scope'] != SCOPE or value['worker_version'] != worker.VERSION
            or value['local_worker_evidence_included'] is not True
            or value['operator_worker_binding_supported'] is not False
            or value['external_submission_allowed'] is not False):
        raise ValueError('Invalid complete operational archive contract')
    identity = ledger._id(value['ledger_id'])
    records = value['worker_records']
    if not isinstance(records, list) or not 1 <= len(records) <= worker.RECORDS:
        raise ValueError('Missing or excessive worker evidence')
    if len(encoded(value).encode()) > MAX_BYTES:
        raise ValueError('Operational archive byte capacity exceeded')
    if sum(len(ledger.json_text(record).encode()) for record in records) > worker.RECORDS * worker.RECORD_BYTES:
        raise ValueError('Retained worker evidence byte capacity exceeded')
    if value['sha256'] != sha({key: item for key, item in value.items() if key != 'sha256'}):
        raise ValueError('Operational archive hash mismatch')
    checked = financial.verify(value['financial'])
    if checked['ledger_id'] != identity:
        raise ValueError('Mixed financial and worker ledger identities')
    worker._replay(identity, worker.VERSION, checked['commits'], records)
    if (value['worker_root_sha256'] != records[0]['sha256']
            or value['worker_head_sha256'] != records[-1]['sha256']):
        raise ValueError('Worker archive endpoints mismatch')
    return deepcopy(value)


def capture(engine, ledger_id):
    with transaction(engine) as db:
        row = ledger._lock(db, ledger_id)
        nodes = ledger._audit(db, row)
        if row.worker_version != worker.VERSION:
            raise ValueError('Complete operational scope requires explicit worker enrollment')
        records = worker.audit(db, row, nodes)
        body = {'version': VERSION, 'scope': SCOPE, 'ledger_id': row.ledger_id,
                'financial': financial._pack(row, nodes), 'worker_version': row.worker_version,
                'worker_records': [deepcopy(record.payload) for record in records],
                'worker_root_sha256': records[0].payload_sha256,
                'worker_head_sha256': records[-1].payload_sha256,
                'local_worker_evidence_included': True,
                'operator_worker_binding_supported': False, 'external_submission_allowed': False}
        return verify({**body, 'sha256': sha(body)})
