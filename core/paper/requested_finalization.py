"""Local unsubmitted-request closure; never represents exchange cancellation."""
from copy import deepcopy
from core.paper import requested_execution as contract
from core.paper.fault_adapter import encoded, sha

VERSION='paper-requested-unsubmitted-finalization-v1'
SUMMARY_VERSION='paper-requested-local-void-v1'


def record(value,command_id,expected_revision,created_at):
    value=contract.validate(value)
    if not isinstance(command_id,str) or not command_id or len(command_id)>128 or type(expected_revision) is not int or expected_revision<1:
        raise ValueError('Stable command identity and financial revision required')
    if contract.clock(created_at)<contract.clock(value['created_at']):raise ValueError('Finalization predates request')
    return {'version':VERSION,'account_id':value['account_id'],'request_id':value['request_id'],'command_id':command_id,
            'expected_revision':expected_revision,'created_at':created_at,'request_sha256':sha(value),'source_events':0,
            'proof':'NO_DURABLE_LOCAL_SUBMIT','reason':'LOCAL_WITHDRAWAL_BEFORE_SUBMIT','external_submission_allowed':False}


def summary(value,void,before,events):
    if events or not isinstance(void,dict):raise ValueError('Local finalization requires zero source events')
    expected=record(value,void.get('command_id'),before,void.get('created_at'))
    if encoded(void)!=encoded(expected):raise ValueError('Finalization evidence differs from request/revision')
    result=contract.reduce(value,[])
    result.update(version=SUMMARY_VERSION,state='VOID_UNSUBMITTED',active_unfilled_quantity='0',local_unsubmitted_finalized=True)
    result['funding']={'reserved_cash':'0','reserved_quantity':'0','available_cash':result['account']['cash'],
                       'available_quantity':result['account']['quantity']}
    return result


def finalize(engine,account_id,request_id,command_id,expected_revision,created_at,*,require_controlled=False):
    from sqlalchemy import select
    from core.paper import requested_journal as journal, requested_controls as controls
    from core.storage.models import RequestedPaperVoidRecord as Void
    with journal.transaction(engine) as db:
        if require_controlled:
            from sqlalchemy import text
            db.execute(text("SELECT set_config('lock_timeout', '2000ms', true)"))
        row=journal.lock(db,account_id);view=journal.audit(db,row)
        controlled=controls.check(db,row,view)
        if require_controlled and controlled['coverage']!='CONTROLLED':raise ValueError('Explicit policy enrollment required')
        entry=next((entry for entry in view['requests'] if entry['request']['request_id']==request_id),None)
        if entry is None:raise ValueError('Request does not belong to account')
        payload=record(entry['request'],command_id,expected_revision,created_at)
        old=entry.get('void')
        if old is not None:
            if encoded(old)!=encoded(payload):raise ValueError('Conflicting finalization retry')
            return deepcopy(entry['summary'])
        if row.active_request_id!=request_id or entry['events'] or view['revision']!=expected_revision:
            raise ValueError('Only current undispatched request can be finalized')
        if db.scalar(select(Void).where(Void.account_id==account_id,Void.command_id==command_id)) is not None:
            raise ValueError('Finalization command belongs to another request')
        latest=controlled['records'][-1]['created_at'] if controlled['coverage']=='CONTROLLED' else view['last_clock']
        if contract.clock(created_at)<contract.clock(latest):raise ValueError('Finalization predates current control')
        result=summary(entry['request'],payload,view['revision'],[])
        db.add(Void(request_id=request_id,account_id=account_id,command_id=command_id,payload=payload,payload_sha256=sha(payload)))
        row.current=result['account'];row.active_request_id=None;row.revision+=1
        db.flush();journal.audit(db,row)
        return result
