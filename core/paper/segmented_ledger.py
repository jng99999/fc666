"""Independent PostgreSQL local financial ledger; no operator/worker/venue authority."""
from copy import deepcopy
import re
from sqlalchemy import select,text,func
from core.storage.models import SegmentedPaperLedgerRecord as Ledger, SegmentedPaperCommitRecord as Commit
from core.paper import requested_segmented_account as accounts, requested_segmented_execution as segments
from core.paper.fault_adapter import encoded,sha
from core.paper.requested_journal import transaction

VERSION='paper-segmented-local-ledger-v1'
COMMITS=512
STORAGE_BYTES=64*1024*1024
NODE_BYTES=34*1024*1024
LEDGERS=100


def _id(value):
    if not isinstance(value,str) or not value.startswith('segmented:') or not 11<=len(value)<=128:
        raise ValueError('Independent segmented ledger identity required')
    return value


def _command(value):
    keys={'command_id','kind','expected_sha256','expected_revision','payload'}
    if (not isinstance(value,dict) or set(value)!=keys or
        not isinstance(value['command_id'],str) or not 1<=len(value['command_id'])<=128 or
        value['kind'] not in ('PREPARE','APPEND','VOID') or
        not isinstance(value['expected_sha256'],str) or re.fullmatch('[0-9a-f]{64}',value['expected_sha256']) is None or
        type(value['expected_revision']) is not int or value['expected_revision']<0 or not isinstance(value['payload'],dict)):
        raise ValueError('Exact versioned financial command required')
    if len(encoded(value).encode())>segments.SEGMENT_BYTES+8192:raise ValueError('Command byte capacity exceeded')
    return deepcopy(value)


def _transition(before,command,parent):
    command=_command(command)
    if command['expected_sha256']!=parent or command['expected_revision']!=before['revision']:
        raise ValueError('Stale local financial checkpoint')
    reports=[entry['report'] for entry in before['requests']]
    voids={entry['report']['request']['request_id']:entry['void'] for entry in before['requests'] if entry['void'] is not None}
    payload=command['payload']
    if command['kind']=='PREPARE':
        reports.append(segments.replay(payload,[]))
    elif command['kind']=='APPEND':
        if set(payload)!={'request_id','batch'} or not reports or before['active_request_id']!=payload['request_id']:
            raise ValueError('Only active request accepts a new segment')
        changed=segments.append(reports[-1],payload['batch'])
        if encoded(changed)==encoded(reports[-1]):raise ValueError('Retry requires the original command identity')
        reports[-1]=changed
    else:
        identity=payload.get('request_id')
        if not reports or identity!=before['active_request_id'] or identity in voids:
            raise ValueError('Only current unsubmitted request can be withdrawn')
        voids[identity]=payload
    result=accounts.replay(before['opening'],reports,finalizations=voids)
    if result['revision']<=before['revision']:raise ValueError('Financial command must advance revision')
    return result


def _node(ledger_id,index,parent,command,report):
    body={'version':VERSION,'ledger_id':ledger_id,'commit_index':index,'parent_sha256':parent,'command':deepcopy(command),'report':report}
    result={**body,'sha256':sha(body)}
    if len(encoded(result).encode())>NODE_BYTES:raise ValueError('Commit byte capacity exceeded')
    return result


def _lock(db,ledger_id):
    db.execute(text("SELECT set_config('lock_timeout','2000ms',true)"))
    row=db.scalar(select(Ledger).where(Ledger.ledger_id==_id(ledger_id)).with_for_update())
    if row is None:raise ValueError('Unknown independent local ledger')
    return row


def _audit(db,row):
    count,size=db.execute(text('SELECT count(*),coalesce(sum(octet_length(payload::text)),0) FROM segmented_paper_commits WHERE ledger_id=:id'),{'id':row.ledger_id}).one()
    if not 1<=count<=COMMITS or size>STORAGE_BYTES:raise ValueError('Retained commit storage capacity exceeded')
    nodes=list(db.scalars(select(Commit).where(Commit.ledger_id==row.ledger_id).order_by(Commit.commit_index).limit(COMMITS+1)))
    parent=None;before=None;commands=set()
    for index,node in enumerate(nodes):
        value=node.payload
        keys={'version','ledger_id','commit_index','parent_sha256','command','report','sha256'}
        if (not isinstance(value,dict) or set(value)!=keys or value['version']!=VERSION or
            value['ledger_id']!=row.ledger_id or type(value['commit_index']) is not int or value['commit_index']!=index or node.commit_index!=index or
            value['parent_sha256']!=parent or value['sha256']!=node.payload_sha256 or
            value['sha256']!=sha({key:item for key,item in value.items() if key!='sha256'}) or
            len(encoded(value).encode())>NODE_BYTES):raise ValueError('Commit identity/hash/lineage mismatch')
        if index==0:
            command=value['command']
            if not isinstance(command,dict) or set(command)!={'kind','opening'} or command['kind']!='CREATE' or node.command_id is not None:
                raise ValueError('Invalid root financial command')
            report=accounts.replay(command['opening'],[])
            if report['opening']['account_id']!=row.ledger_id or row.root_sha256!=value['sha256']:
                raise ValueError('Root financial identity mismatch')
        else:
            command=_command(value['command'])
            if node.command_id!=command['command_id'] or node.command_id in commands:raise ValueError('Command identity conflict')
            commands.add(node.command_id)
            report=_transition(before,command,parent)
        if encoded(report)!=encoded(value['report']):raise ValueError('Persisted financial settlement differs from replay')
        before=report;parent=value['sha256']
    if (row.commit_index!=len(nodes)-1 or row.head_sha256!=parent or row.financial_revision!=before['revision'] or
        encoded(row.current)!=encoded(before['account'])):raise ValueError('Financial head differs from immutable commits')
    from core.paper import segmented_worker
    segmented_worker.audit(db, row, nodes)
    return nodes


def _view(node):
    return {'version':VERSION,'ledger_id':node['ledger_id'],'commit_index':node['commit_index'],
            'node_sha256':node['sha256'],'journal':deepcopy(node['report']),
            'local_financial_persistence_supported':True,'operator_worker_binding_supported':False,
            'external_submission_allowed':False}


def create(engine,opening):
    ledger_id=_id(opening.get('account_id') if isinstance(opening,dict) else None)
    report=accounts.replay(opening,[])
    root=_node(ledger_id,0,None,{'kind':'CREATE','opening':opening},report)
    with transaction(engine) as db:
        db.execute(text("SELECT set_config('lock_timeout','2000ms',true)"))
        db.execute(text("SELECT pg_advisory_xact_lock(hashtext('fc666-segmented-ledger-constructor-v1'))"))
        row=db.scalar(select(Ledger).where(Ledger.ledger_id==ledger_id).with_for_update())
        if row is not None:
            nodes=_audit(db,row)
            if encoded(nodes[0].payload)!=encoded(root):raise ValueError('Conflicting ledger creation retry')
            return _view(nodes[-1].payload)
        if db.scalar(select(func.count()).select_from(Ledger))>=LEDGERS:raise ValueError('Independent ledger count capacity exceeded')
        row=Ledger(ledger_id=ledger_id,root_sha256=root['sha256'],head_sha256=root['sha256'],commit_index=0,financial_revision=0,current=report['account'])
        db.add(row);db.flush()
        db.add(Commit(ledger_id=ledger_id,commit_index=0,command_id=None,payload=root,payload_sha256=root['sha256']));db.flush()
        _audit(db,row)
        return _view(root)


def read(engine,ledger_id):
    with transaction(engine) as db:
        row=_lock(db,ledger_id)
        return _view(_audit(db,row)[-1].payload)


def execute(engine,ledger_id,command):
    command=_command(command)
    with transaction(engine) as db:
        row=_lock(db,ledger_id);nodes=_audit(db,row)
        from core.paper import segmented_worker
        if segmented_worker.enrolled(db,ledger_id):
            raise ValueError('Bound ledger requires current worker lease')
        return _execute_locked(db,row,nodes,command)


def _execute_locked(db,row,nodes,command,*,final_audit=True):
    ledger_id=row.ledger_id
    old=next((node for node in nodes if node.command_id==command['command_id']),None)
    if old is not None:
        if encoded(old.payload['command'])!=encoded(command):raise ValueError('Conflicting command retry')
        return _view(old.payload)
    if len(nodes)>=COMMITS:raise ValueError('Commit count capacity exceeded')
    head=nodes[-1].payload
    report=_transition(head['report'],command,head['sha256'])
    node=_node(ledger_id,len(nodes),head['sha256'],command,report)
    # Measure the same JSON rendering the database stores; caller hashes do not
    # authorize or override budgets. This query sends no new financial rows.
    size=db.scalar(text('SELECT coalesce(sum(octet_length(payload::text)),0) FROM segmented_paper_commits WHERE ledger_id=:id'),{'id':ledger_id})
    added=db.scalar(text('SELECT octet_length(CAST(:payload AS json)::text)'),{'payload':json_text(node)})
    if size+added>STORAGE_BYTES:raise ValueError('Retained commit storage capacity exceeded')
    db.add(Commit(ledger_id=ledger_id,commit_index=len(nodes),command_id=command['command_id'],payload=node,payload_sha256=node['sha256']))
    row.head_sha256=node['sha256'];row.commit_index=len(nodes);row.financial_revision=report['revision'];row.current=deepcopy(report['account'])
    db.flush()
    if final_audit:_audit(db,row)
    return _view(node)

def json_text(value):
    # Match SQLAlchemy's default JSON serialization (including whitespace/ASCII).
    import json
    return json.dumps(value)
