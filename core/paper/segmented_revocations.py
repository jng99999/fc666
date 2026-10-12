"""Permanent token-scoped denies for segmented HTTP operator admission only."""
from contextlib import contextmanager
from copy import deepcopy
import hashlib
import re
from sqlalchemy import select,text
from core.paper.requested_journal import transaction
from core.paper.fault_adapter import sha
from core.paper.segmented_ledger import _id
from core.storage.models import SegmentedOperatorRevocationRecord as Record, SegmentedOperatorRevocationHead as Head

VERSION='paper-segmented-operator-revocation-v1'
LIMIT=1024
LOCK='fc666-segmented-operator-revocation-v1'
ACTIONS={'*','READ_SEGMENTED_EVIDENCE','ENROLL_SEGMENTED_WORKER'}


class Revoked(ValueError):
    def __init__(self,status):
        self.status=status
        super().__init__('Segmented operator permission revoked')


def subject(token):
    if not isinstance(token,str) or re.fullmatch(r'[A-Za-z0-9_-]{32,256}',token) is None:
        raise ValueError('Bounded operator credential required')
    return hashlib.sha256(('fc666-segmented-operator-subject-v1:'+token).encode()).hexdigest()


def _lock(db,exclusive=False):
    db.execute(text("SELECT set_config('lock_timeout','2000ms',true)"))
    function='pg_advisory_xact_lock' if exclusive else 'pg_advisory_xact_lock_shared'
    db.execute(text(f'SELECT {function}(hashtext(:key))'),{'key':LOCK})


def audit(db):
    records=list(db.scalars(select(Record).order_by(Record.sequence).limit(LIMIT+1)))
    if len(records)>LIMIT:raise ValueError('Revocation capacity exceeded')
    parent=None;previous=0;identities=set();scopes=set()
    for index,row in enumerate(records):
        value=row.payload
        keys={'version','sequence','parent_sha256','command_id','subject_sha256','ledger_id','action','recorded_us','sha256'}
        if (not isinstance(value,dict) or set(value)!=keys or value['version']!=VERSION
            or type(value['sequence']) is not int or value['sequence']!=index or row.sequence!=index
            or value['parent_sha256']!=parent or row.payload_sha256!=value['sha256']
            or value['sha256']!=sha({key:item for key,item in value.items() if key!='sha256'})
            or any(getattr(row,key)!=value[key] for key in ['command_id','subject_sha256','ledger_id','action'])
            or not isinstance(value['subject_sha256'],str) or re.fullmatch('[0-9a-f]{64}',value['subject_sha256']) is None
            or not isinstance(value['command_id'],str) or not 1<=len(value['command_id'])<=128
            or value['action'] not in ACTIONS or type(value['recorded_us']) is not int
            or not 0<value['recorded_us']<=2**63-1 or value['recorded_us']<previous):
            raise ValueError('Invalid immutable revocation evidence')
        if value['ledger_id']!='*':_id(value['ledger_id'])
        scope=(value['subject_sha256'],value['ledger_id'],value['action'])
        if value['command_id'] in identities or scope in scopes:raise ValueError('Duplicate revocation identity/scope')
        identities.add(value['command_id']);scopes.add(scope)
        parent=value['sha256'];previous=value['recorded_us']
    checkpoint=db.get(Head,'global')
    if (checkpoint is None or checkpoint.revision!=len(records) or checkpoint.head_sha256!=parent
        or checkpoint.root_sha256!=(records[0].payload_sha256 if records else None)):
        raise ValueError('Revocation checkpoint differs from retained evidence')
    return records


def revoke(engine,token,command_id,*,ledger_id='*',action='*'):
    digest=subject(token)
    if not isinstance(command_id,str) or not 1<=len(command_id)<=128 or action not in ACTIONS:
        raise ValueError('Exact bounded revocation identity/scope required')
    if ledger_id!='*':_id(ledger_id)
    with transaction(engine) as db:
        _lock(db,True);records=audit(db)
        old=next((row for row in records if row.command_id==command_id),None)
        if old:
            if (old.subject_sha256,old.ledger_id,old.action)!=(digest,ledger_id,action):raise ValueError('Conflicting revocation retry')
            return deepcopy(old.payload)
        if any((row.subject_sha256,row.ledger_id,row.action)==(digest,ledger_id,action) for row in records):
            raise ValueError('Retry requires original revocation identity')
        if len(records)>=LIMIT:raise ValueError('Revocation capacity exceeded')
        now=db.scalar(text('SELECT (extract(epoch FROM clock_timestamp())*1000000)::bigint'))
        if records and now<records[-1].payload['recorded_us']:raise ValueError('Database clock regressed')
        body=dict(version=VERSION,sequence=len(records),parent_sha256=records[-1].payload_sha256 if records else None,
                  command_id=command_id,subject_sha256=digest,ledger_id=ledger_id,action=action,recorded_us=now)
        value={**body,'sha256':sha(body)}
        db.add(Record(sequence=len(records),command_id=command_id,subject_sha256=digest,ledger_id=ledger_id,
                      action=action,payload=value,payload_sha256=value['sha256']))
        checkpoint=db.get(Head,'global')
        checkpoint.revision=len(records)+1
        checkpoint.head_sha256=value['sha256']
        if not records:checkpoint.root_sha256=value['sha256']
        db.flush();audit(db)
        return deepcopy(value)


@contextmanager
def guard(engine,token,ledger_id,action):
    digest=subject(token)
    with transaction(engine) as db:
        _lock(db)
        for row in audit(db):
            if row.subject_sha256==digest and row.ledger_id in ['*',ledger_id] and row.action in ['*',action]:
                raise Revoked(401 if row.ledger_id=='*' and row.action=='*' else 403)
        yield
