"""Durable local failure-assessment attempts; no transport capability."""
from copy import deepcopy
import re
from sqlalchemy import select,text
from core.storage.models import RequestedPaperAttemptRecord as Attempt
from core.paper import requested_journal as journal, requested_ownership as own, requested_dispatch as dispatch, requested_dispatch_query as query, requested_transport_boundary as boundary
from core.paper.fault_adapter import sha,encoded
from core.paper.requested_execution import MAX_BYTES

VERSION='paper-requested-local-attempt-v1'
KEYS={'account_id','request_id','client_id','owner','ownership_token','attempt_id','failure','expected_financial_revision','expected_control_revision'}
LIMIT=16


def validate(command):
    if not isinstance(command,dict) or set(command)!=KEYS:raise ValueError('Exact attempt command required')
    for key in ('account_id','owner','attempt_id'):
        if type(command[key]) is not str or not 1<=len(command[key])<=128:raise ValueError('Invalid command identity')
    for key in ('request_id','client_id'):
        if type(command[key]) is not str or re.fullmatch('[0-9a-f]{64}',command[key]) is None:raise ValueError('Invalid original identity')
    for key in ('ownership_token','expected_financial_revision','expected_control_revision'):
        if type(command[key]) is not int or command[key]<1:raise ValueError('Strict positive command fence required')
    if command['ownership_token']>64 or type(command['failure']) is not str or command['failure'] not in boundary.FAILURES:raise ValueError('Unsupported token/failure')


def verify(value):
    if not isinstance(value,dict) or set(value)!={'version','command','ordinal','assessment','remote_send_performed','sha256'} or value['version']!=VERSION or len(encoded(value).encode())>MAX_BYTES:raise ValueError('Invalid local attempt evidence')
    command=value['command'];validate(command)
    if type(value['ordinal']) is not int or not 0<=value['ordinal']<LIMIT:raise ValueError('Invalid attempt ordinal')
    report=boundary.verify(value['assessment']);q=report['query_evidence']
    for key in ('request_id','client_id','owner','ownership_token'):
        if encoded(command[key])!=encoded(q[key]):raise ValueError('Attempt identity differs from evidence')
    source=q['dispatch_evidence']['ownership_evidence']['source_evidence']
    if (command['account_id']!=source['journal']['journal']['opening']['account_id'] or
        command['expected_financial_revision']!=source['journal']['journal']['revision'] or
        command['expected_control_revision']!=source['controls']['revision'] or command['failure']!=report['failure']):raise ValueError('Attempt checkpoint differs')
    body={'version':VERSION,'command':command,'ordinal':value['ordinal'],'assessment':report,'remote_send_performed':False}
    if encoded(value)!=encoded({**body,'sha256':sha(body)}):raise ValueError('Attempt differs from replay')
    return deepcopy(value)


def check(db,account_id,request_id):
    saved=list(db.scalars(select(Attempt).where(Attempt.account_id==account_id,Attempt.request_id==request_id).order_by(Attempt.ordinal).limit(LIMIT+1)))
    if len(saved)>LIMIT:raise ValueError('Attempt capacity exceeded')
    values=[]
    for index,row in enumerate(saved):
        value=verify(row.payload);command=value['command']
        if row.ordinal!=index or value['ordinal']!=index or row.payload_sha256!=value['sha256'] or row.attempt_id!=command['attempt_id'] or row.token!=command['ownership_token'] or row.account_id!=command['account_id'] or row.request_id!=command['request_id']:raise ValueError('Attempt storage differs from evidence')
        values.append(value)
    return values


def record(engine,command):
    validate(command)
    with journal.transaction(engine) as db:
        db.execute(text("SELECT set_config('lock_timeout','2000ms',true)"))
        row=journal.lock(db,command['account_id']);financial=journal.audit(db,row);controlled=own.controls.check(db,row,financial)
        request_id=command['request_id']
        if not any(entry['request']['request_id']==request_id for entry in financial['requests']):raise ValueError('Request account differs')
        own._owned(db,request_id,command['owner'],command['ownership_token'])
        history=check(db,row.account_id,request_id)
        old=next((value for value in history if value['command']['attempt_id']==command['attempt_id']),None)
        if old is not None:
            if encoded(old['command'])!=encoded(command):raise ValueError('Conflicting attempt retry')
            own._owned(db,request_id,command['owner'],command['ownership_token'])
            return old
        if len(history)>=LIMIT or financial['revision']!=command['expected_financial_revision'] or controlled['revision']!=command['expected_control_revision']:raise ValueError('Attempt capacity/checkpoint differs')
        q=query.evaluate(dispatch.snapshot(db,row,financial,controlled),request_id,command['client_id'],command['owner'],command['ownership_token'])
        assessment=boundary.evaluate(q,command['failure'])
        body={'version':VERSION,'command':deepcopy(command),'ordinal':len(history),'assessment':assessment,'remote_send_performed':False}
        value=verify({**body,'sha256':sha(body)})
        db.add(Attempt(request_id=request_id,ordinal=len(history),account_id=row.account_id,attempt_id=command['attempt_id'],token=command['ownership_token'],payload=value,payload_sha256=value['sha256']))
        db.flush();check(db,row.account_id,request_id)
        own._owned(db,request_id,command['owner'],command['ownership_token'])
        return value


def capture(engine,account_id,request_id):
    with journal.transaction(engine) as db:
        db.execute(text("SELECT set_config('lock_timeout','2000ms',true)"))
        row=journal.lock(db,account_id);financial=journal.audit(db,row)
        if not any(entry['request']['request_id']==request_id for entry in financial['requests']):raise ValueError('Request account differs')
        return check(db,account_id,request_id)
