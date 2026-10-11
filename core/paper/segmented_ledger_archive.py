"""Complete local ledger evidence; offline consistency never grants authority."""
from copy import deepcopy
from core.paper import segmented_ledger as ledger
from core.paper.fault_adapter import encoded, sha
from core.paper.requested_journal import transaction

VERSION = 'paper-segmented-local-ledger-archive-v1'
MAX_BYTES = ledger.STORAGE_BYTES + 16384


def verify(value):
    keys = {'version', 'ledger_id', 'root_sha256', 'head_sha256', 'commits',
            'operator_worker_binding_supported', 'external_submission_allowed', 'sha256'}
    if (not isinstance(value, dict) or set(value) != keys or value['version'] != VERSION
            or value['operator_worker_binding_supported'] is not False
            or value['external_submission_allowed'] is not False):
        raise ValueError('Invalid archive contract')
    identity = ledger._id(value['ledger_id'])
    nodes = value['commits']
    if not isinstance(nodes, list) or not 1 <= len(nodes) <= ledger.COMMITS:
        raise ValueError('Invalid retained commit count')
    if len(encoded(value).encode()) > MAX_BYTES:
        raise ValueError('Archive byte capacity exceeded')
    if sum(len(ledger.json_text(node).encode()) for node in nodes) > ledger.STORAGE_BYTES:
        raise ValueError('Retained commit byte capacity exceeded')
    if value['sha256'] != sha({key: item for key, item in value.items() if key != 'sha256'}):
        raise ValueError('Archive hash mismatch')
    parent = None
    before = None
    commands = set()
    for index, node in enumerate(nodes):
        node_keys = {'version', 'ledger_id', 'commit_index', 'parent_sha256', 'command', 'report', 'sha256'}
        if (not isinstance(node, dict) or set(node) != node_keys or node['version'] != ledger.VERSION
                or node['ledger_id'] != identity or type(node['commit_index']) is not int
                or node['commit_index'] != index or node['parent_sha256'] != parent
                or len(encoded(node).encode()) > ledger.NODE_BYTES
                or node['sha256'] != sha({key: item for key, item in node.items() if key != 'sha256'})):
            raise ValueError('Commit identity/hash/lineage mismatch')
        if index == 0:
            command = node['command']
            if not isinstance(command, dict) or set(command) != {'kind', 'opening'} or command['kind'] != 'CREATE':
                raise ValueError('Invalid root command')
            report = ledger.accounts.replay(command['opening'], [])
            if report['opening']['account_id'] != identity:
                raise ValueError('Root identity mismatch')
        else:
            command = ledger._command(node['command'])
            if command['command_id'] in commands:
                raise ValueError('Repeated command identity')
            commands.add(command['command_id'])
            report = ledger._transition(before, command, parent)
        if encoded(report) != encoded(node['report']):
            raise ValueError('Financial settlement differs from replay')
        before, parent = report, node['sha256']
    if value['root_sha256'] != nodes[0]['sha256'] or value['head_sha256'] != parent:
        raise ValueError('Archive endpoints mismatch')
    return deepcopy(value)


def _pack(row, nodes):
    body = {'version': VERSION, 'ledger_id': row.ledger_id, 'root_sha256': row.root_sha256,
            'head_sha256': row.head_sha256, 'commits': [deepcopy(node.payload) for node in nodes],
            'operator_worker_binding_supported': False, 'external_submission_allowed': False}
    return {**body, 'sha256': sha(body)}


def capture(engine, ledger_id):
    with transaction(engine) as db:
        row = ledger._lock(db, ledger_id)
        nodes = ledger._audit(db, row)
        return verify(_pack(row, nodes))
