"""PostgreSQL-only explicit Paper journal. Local simulation; no external transport."""
from copy import deepcopy
from contextlib import contextmanager
from sqlalchemy import select, text
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session
from core.paper import requested_execution as contract
from core.paper.fault_adapter import encoded, sha
from core.storage.models import RequestedPaperAccountRecord as Account, RequestedPaperRequestRecord as Request, RequestedPaperEventRecord as Event, RequestedPaperVoidRecord as Void

VERSION='paper-requested-journal-v1'
VERSION_V2='paper-requested-journal-v2'
REQUEST_LIMIT=100
TOTAL_EVENTS=1000


class MissingAccount(ValueError):
    pass


@contextmanager
def transaction(engine):
    if engine.dialect.name!='postgresql':raise ValueError('PostgreSQL required for account locking')
    with Session(engine) as db,db.begin():
        if db.connection().connection.driver_connection.autocommit:
            raise ValueError('Autocommit cannot provide atomic Paper settlement')
        yield db


def opening(account_id,instrument_id,base,created_at):
    if any(not isinstance(v,str) or not v or len(v)>128 for v in [account_id,instrument_id]):
        raise ValueError('Bounded account/instrument identity required')
    contract.base_values(base);contract.clock(created_at)
    return {'version':VERSION,'account_id':account_id,'instrument_id':instrument_id,'base':deepcopy(base),
            'created_at':created_at,'mode':'LOCAL_PAPER_ONLY','external_submission_allowed':False}


def lock(db,account_id):
    row=db.scalar(select(Account).where(Account.account_id==account_id).with_for_update())
    if row is None:raise MissingAccount('Unknown explicit Paper account')
    return row


def _financial_audit(db,row):
    seed=row.opening
    expected=opening(*(seed[k] for k in ['account_id','instrument_id','base','created_at']))
    if encoded(seed)!=encoded(expected) or seed['account_id']!=row.account_id or row.opening_sha256!=sha(seed):
        raise ValueError('Opening evidence mismatch')
    requests=list(db.scalars(select(Request).where(Request.account_id==row.account_id).order_by(Request.ordinal).limit(REQUEST_LIMIT+1)))
    if len(requests)>REQUEST_LIMIT:raise ValueError('Request history exceeds account capacity')
    voids=list(db.scalars(select(Void).where(Void.account_id==row.account_id).limit(REQUEST_LIMIT+1)))
    if len(voids)>REQUEST_LIMIT:raise ValueError('Finalization capacity exceeded')
    by_request={void.request_id:void for void in voids};void_commands=set()
    current=deepcopy(seed['base']);active=None;last_clock=seed['created_at'];revision=0;total=0;history=[]
    for ordinal,request in enumerate(requests):
        value=request.payload
        contract.validate(value)
        if (active is not None or request.ordinal!=ordinal or request.request_id!=value['request_id'] or
            request.account_id!=value['account_id'] or request.client_request_id!=value['client_request_id'] or
            request.payload_sha256!=sha(value) or encoded(value['base'])!=encoded(current) or
            contract.clock(value['created_at'])<contract.clock(last_clock)):
            raise ValueError('Request journal chain mismatch')
        rows=list(db.scalars(select(Event).where(Event.request_id==request.request_id).order_by(Event.sequence).limit(TOTAL_EVENTS+1)))
        total+=len(rows)
        if total>TOTAL_EVENTS:raise ValueError('Event history exceeds account capacity')
        events=[];summary=contract.reduce(value,[])
        for event in rows:
            payload=event.payload
            if event.payload_sha256!=sha(payload) or event.sequence!=payload['sequence'] or event.event_id!=payload['event_id']:
                raise ValueError('Event journal identity mismatch')
            events.append(payload);summary=contract.reduce(value,events)
            if encoded(summary)!=encoded(event.summary) or event.summary_sha256!=sha(summary):
                raise ValueError('Event settlement evidence mismatch')
        revision+=1+len(rows)
        void=by_request.pop(request.request_id,None)
        if void is not None:
            from core.paper import requested_finalization
            if (void.account_id!=row.account_id or void.command_id!=void.payload.get('command_id') or
                void.payload_sha256!=sha(void.payload) or void.command_id in void_commands):raise ValueError('Finalization identity/hash conflict')
            void_commands.add(void.command_id)
            summary=requested_finalization.summary(value,void.payload,revision,events);revision+=1
        current=summary['account']
        active=None if summary['local_source_sealed'] or void is not None else request.request_id
        last_clock=void.payload['created_at'] if void is not None else events[-1]['received_at'] if events else value['created_at']
        entry={'request':deepcopy(value),'events':deepcopy(events),'summary':summary}
        if voids:entry['void']=deepcopy(void.payload) if void is not None else None
        history.append(entry)
    if by_request:raise ValueError('Finalization lacks account request')
    result={'version':VERSION_V2 if voids else VERSION,'opening':deepcopy(seed),'account':current,'active_request_id':active,
            'revision':revision,'last_clock':last_clock,'requests':history,'total_events':total,
            'external_submission_allowed':False}
    if voids:result['total_finalizations']=len(voids)
    if len(encoded(result).encode())>contract.MAX_BYTES:raise ValueError('Journal exceeds 32 MiB')
    if row.revision!=revision or row.active_request_id!=active or encoded(row.current)!=encoded(current):
        raise ValueError('Account cache differs from immutable journal')
    return result


def audit(db,row):
    financial=_financial_audit(db,row)
    from core.paper import requested_controls
    controlled=requested_controls.check(db,row,financial)
    from core.paper import requested_sources
    requested_sources.check(db,row,financial,controlled)
    from core.paper import requested_ownership
    requested_ownership.check(db,row,financial,controlled)
    return financial


def create(engine,account_id,instrument_id,base,created_at):
    value=opening(account_id,instrument_id,base,created_at)
    with transaction(engine) as db:
        db.execute(insert(Account).values(account_id=account_id,opening=value,opening_sha256=sha(value),current=base,
                                         active_request_id=None,revision=0).on_conflict_do_nothing(index_elements=['account_id']))
        row=lock(db,account_id)
        if encoded(row.opening)!=encoded(value):raise ValueError('Conflicting account creation retry')
        return audit(db,row)


def read(engine,account_id,*,lock_timeout_ms=None):
    # Lock prevents READ COMMITTED from combining different account revisions.
    if lock_timeout_ms is not None and (type(lock_timeout_ms) is not int or not 1<=lock_timeout_ms<=5000):
        raise ValueError('Bounded integer lock timeout required')
    with transaction(engine) as db:
        if lock_timeout_ms is not None:
            db.execute(text("SELECT set_config('lock_timeout', :value, true)"),{'value':f'{lock_timeout_ms}ms'})
        return audit(db,lock(db,account_id))


def _prepare(db,row,view,value):
    existing=next((r for r in view['requests'] if r['request']['client_request_id']==value['client_request_id']),None)
    if existing is not None:
        if encoded(existing['request'])!=encoded(value):raise ValueError('Conflicting client request retry')
        return deepcopy(existing['summary'])
    if view['active_request_id'] is not None:raise ValueError('Account already has an unsealed request')
    if len(view['requests'])>=REQUEST_LIMIT:raise ValueError('Request history capacity reached')
    if encoded(value['base'])!=encoded(view['account']) or contract.clock(value['created_at'])<contract.clock(view['last_clock']):
        raise ValueError('Stale account base or creation clock')
    summary=contract.reduce(value,[])
    from core.paper import requested_controls
    admitted=requested_controls.gate(db,row,view,value,'PREPARE',value['created_at'])
    db.add(Request(request_id=value['request_id'],account_id=row.account_id,client_request_id=value['client_request_id'],
                   ordinal=len(view['requests']),payload=deepcopy(value),payload_sha256=sha(value)))
    db.flush()  # Parent request must exist before its FK admission row; same transaction.
    if admitted is not None:db.add(admitted)
    row.current=summary['account'];row.active_request_id=value['request_id'];row.revision+=1
    db.flush();audit(db,row)
    return summary



def prepare(engine,value):
    value=contract.validate(value)
    with transaction(engine) as db:
        row=lock(db,value['account_id']);view=audit(db,row)
        return _prepare(db,row,view,value)


def _accept(db,row,view,request_id,event,*,source_receipt=None,ownership_token=None):
    from core.paper import requested_ownership
    requested_ownership.guard(db,request_id,ownership_token)
    entry=next((r for r in view['requests'] if r['request']['request_id']==request_id),None)
    if entry is None:raise ValueError('Request does not belong to account')
    events=entry['events']
    if not isinstance(event,dict):raise ValueError('Invalid event envelope')
    old=next((e for e in events if e['event_id']==event.get('event_id')),None)
    if old is not None:
        if encoded(old)!=encoded(event):raise ValueError('Conflicting event redelivery')
        return deepcopy(entry['summary'])
    summary=contract.reduce(entry['request'],events+[event])
    if row.active_request_id!=request_id:raise ValueError('Request is not the active account request')
    if view['total_events']>=TOTAL_EVENTS:raise ValueError('Event history capacity reached')
    if event['kind']=='SUBMIT':
        from core.paper import requested_controls
        admitted=requested_controls.gate(db,row,view,entry['request'],'SUBMIT',event['received_at'])
        if admitted is not None:db.add(admitted)
    db.add(Event(request_id=request_id,sequence=event['sequence'],event_id=event['event_id'],payload=deepcopy(event),
                 payload_sha256=sha(event),summary=deepcopy(summary),summary_sha256=sha(summary),ownership_token=ownership_token,ownership_accepted_us=requested_ownership.clock(db) if ownership_token is not None else None,source_version=source_receipt.payload['version'] if source_receipt is not None else None))
    row.current=summary['account'];row.active_request_id=None if summary['local_source_sealed'] else request_id;row.revision+=1
    db.flush()
    if source_receipt is not None:
        db.add(source_receipt);db.flush()
    audit(db,row)
    return summary



def accept(engine,account_id,request_id,event):
    with transaction(engine) as db:
        row=lock(db,account_id);view=audit(db,row)
        return _accept(db,row,view,request_id,event)
