"""Read-only local input preview; labels never establish venue provenance."""
from copy import deepcopy
from core.paper import requested_execution as contract,requested_journal as journal,requested_controls as controls,requested_inspection as inspection
from core.paper.fault_adapter import encoded,sha

VERSION='paper-requested-event-preview-v1'
KEYS={'account_id','request_id','expected_financial_revision','expected_control_revision','source','event'}


def evaluate(financial,controlled,proposal):
    inspection.seal(financial)
    if not isinstance(controlled,dict) or controlled.get('coverage')!='CONTROLLED':raise ValueError('Controlled account required')
    expected=controls.replay(controls.VERSION,controlled.get('policy'),controlled.get('records'),controlled.get('gates'),financial)
    if encoded(expected)!=encoded(controlled):raise ValueError('Control view differs from replay')
    if not isinstance(proposal,dict) or set(proposal)!=KEYS:raise ValueError('Exact event proposal required')
    for key,value in [('expected_financial_revision',financial['revision']),('expected_control_revision',controlled['revision'])]:
        if type(proposal[key]) is not int or proposal[key]!=value:raise ValueError('Event preview revision conflict')
    if proposal['account_id']!=financial['opening']['account_id']:raise ValueError('Account mismatch')
    source=proposal['source']
    if (not isinstance(source,dict) or set(source)!={'kind','source_id'} or source['kind']!='LOCAL_PAPER_OPERATOR_INPUT' or
        not isinstance(source['source_id'],str) or not 1<=len(source['source_id'])<=128):raise ValueError('Explicit local input source required')
    entry=next((entry for entry in financial['requests'] if entry['request']['request_id']==proposal['request_id']),None)
    if entry is None:raise ValueError('Request does not belong to account')
    event=proposal['event'];events=entry['events'];admission=None
    if not isinstance(event,dict):raise ValueError('Event object required')
    old=next((stored for stored in events if stored['event_id']==event.get('event_id')),None)
    if old is not None:
        if encoded(old)!=encoded(event):raise ValueError('Conflicting event redelivery')
        projected=deepcopy(entry['summary']);is_new=False
    else:
        if financial['active_request_id']!=proposal['request_id'] or financial['total_events']>=journal.TOTAL_EVENTS:raise ValueError('Request/capacity cannot accept event')
        projected=contract.reduce(entry['request'],events+[event]);is_new=True
        if event['kind']=='SUBMIT':
            admission=controls.decision(entry['request'],controlled['policy'],controlled['records'][-1],'SUBMIT',financial['revision'],event['received_at'])
    body={'version':VERSION,'journal':inspection.seal(financial),'controls':deepcopy(controlled),'proposal':deepcopy(proposal),
          'classification':'NEW_LOCAL_INPUT' if is_new else 'EXACT_EVENT_REDELIVERY','projected_request_summary':projected,'admission':admission,
          'projected_account':deepcopy(projected['account'] if is_new else financial['account']),
          'projected_active_request_id':(None if projected['local_source_sealed'] else proposal['request_id']) if is_new else financial['active_request_id'],
          'projected_financial_revision':financial['revision']+int(is_new),'event_persisted':False,'external_submission_allowed':False}
    result={**body,'sha256':sha(body)}
    if len(encoded(result).encode())>contract.MAX_BYTES:raise ValueError('Event preview exceeds 32 MiB')
    return result


def capture(engine,proposal):
    if not isinstance(proposal,dict) or set(proposal)!=KEYS:raise ValueError('Exact event proposal required')
    financial,controlled=controls.read(engine,proposal['account_id'])
    result=evaluate(financial,controlled,proposal)
    if result['classification']=='NEW_LOCAL_INPUT':
        from core.paper import requested_capacity
        entry=next(entry for entry in financial['requests'] if entry['request']['request_id']==proposal['request_id'])
        requested_capacity.require_information(financial,entry,proposal['event'],result['projected_request_summary'],journal.TOTAL_EVENTS)
    return result


def verify(report):
    keys={'version','journal','controls','proposal','classification','projected_request_summary','admission','projected_account','projected_active_request_id','projected_financial_revision','event_persisted','external_submission_allowed','sha256'}
    if not isinstance(report,dict) or set(report)!=keys or report['version']!=VERSION or len(encoded(report).encode())>contract.MAX_BYTES:raise ValueError('Invalid event preview envelope/version/bounds')
    expected=evaluate(inspection.verify(report['journal']),report['controls'],report['proposal'])
    if encoded(expected)!=encoded(report):raise ValueError('Event preview differs from replay')
    return deepcopy(expected)
