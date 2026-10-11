"""Complete bounded multi-request replay prerequisite, never financial write authority."""
from copy import deepcopy
from core.paper import requested_execution as settlement, requested_journal as journals
from core.paper import requested_segmented_execution as segmented
from core.paper.fault_adapter import encoded, sha

VERSION='paper-requested-segmented-account-v1'
REQUEST_LIMIT=100
TOTAL_EVENTS=8192
MAX_BYTES=32*1024*1024
OPENING_KEYS={'version','account_id','instrument_id','base','created_at','mode','external_submission_allowed'}


def replay(opening,reports,*,finalizations=None):
    if not isinstance(opening,dict) or set(opening)!=OPENING_KEYS:
        raise ValueError('Invalid complete account opening')
    expected=journals.opening(*(opening[key] for key in ('account_id','instrument_id','base','created_at')))
    if encoded(opening)!=encoded(expected):raise ValueError('Opening integrity mismatch')
    if not isinstance(reports,list) or len(reports)>REQUEST_LIMIT:
        raise ValueError('Account request capacity exceeded')
    if finalizations is None:finalizations={}
    if (not isinstance(finalizations,dict) or len(finalizations)>REQUEST_LIMIT or
        any(not isinstance(key,str) or len(key)!=64 or not isinstance(value,dict) for key,value in finalizations.items())):
        raise ValueError('Invalid bounded finalization map')
    if len(encoded({'opening':opening,'requests':reports,'finalizations':finalizations}).encode())>MAX_BYTES:
        raise ValueError('Account input byte capacity exceeded')
    remaining=deepcopy(finalizations);commands=set();void_count=0
    current=deepcopy(opening['base']);active=None;last_clock=opening['created_at'];revision=total=0
    clients=set();identities=set();entries=[]
    header={'version':VERSION,'opening':deepcopy(opening),'limits':{'requests':REQUEST_LIMIT,'total_events':TOTAL_EVENTS,'total_bytes':MAX_BYTES}}
    previous=sha(header)
    for ordinal,source in enumerate(reports):
        report=segmented.verify(source)
        request=report['request'];count=report['total_events']
        if (active is not None or request['account_id']!=opening['account_id'] or
            request['client_request_id'] in clients or request['request_id'] in identities or
            encoded(request['base'])!=encoded(current) or
            settlement.clock(request['created_at'])<settlement.clock(last_clock)):
            raise ValueError('Request differs from settled account lineage')
        total+=count
        if total>TOTAL_EVENTS:raise ValueError('Account total event capacity exceeded')
        clients.add(request['client_request_id']);identities.add(request['request_id'])
        revision+=1+count
        summary=report['summary'];void=remaining.pop(request['request_id'],None)
        if void is not None:
            from core.paper import requested_finalization
            events=[event for segment in report['segments'] for event in segment['events']]
            summary=requested_finalization.summary(request,void,revision,events)
            if void['command_id'] in commands:raise ValueError('Duplicate finalization command')
            commands.add(void['command_id']);void_count+=1;revision+=1
        current=deepcopy(summary['account'])
        active=None if summary['local_source_sealed'] or void is not None else request['request_id']
        last_clock=void['created_at'] if void is not None else report['segments'][-1]['events'][-1]['received_at'] if count else request['created_at']
        body={'ordinal':ordinal,'previous_sha256':previous,'report':report,'void':deepcopy(void),'summary':deepcopy(summary),'account_revision':revision}
        entry={**body,'sha256':sha(body)};entries.append(entry);previous=entry['sha256']
    if remaining:raise ValueError('Finalization lacks retained request')
    body={**header,'requests':entries,'total_finalizations':void_count,'account':current,'active_request_id':active,
          'last_clock':last_clock,'revision':revision,'total_events':total,'tail_sha256':previous,
          'read_only':True,'ledger_application_supported':False,'external_submission_allowed':False}
    result={**body,'sha256':sha(body)}
    if len(encoded(result).encode())>MAX_BYTES:raise ValueError('Account export byte capacity exceeded')
    return result


def verify(value):
    keys={'version','opening','limits','requests','account','active_request_id','last_clock','revision','total_events','tail_sha256','read_only','ledger_application_supported','external_submission_allowed','sha256','total_finalizations'}
    if not isinstance(value,dict) or set(value)!=keys or value['version']!=VERSION or len(encoded(value).encode())>MAX_BYTES:
        raise ValueError('Unsupported segmented account envelope')
    entries=value['requests']
    if not isinstance(entries,list) or len(entries)>REQUEST_LIMIT or any(not isinstance(entry,dict) or 'report' not in entry for entry in entries):
        raise ValueError('Invalid complete request coverage')
    finalizations={}
    for entry in entries:
        if entry.get('void') is not None:
            try:identity=entry['report']['request']['request_id']
            except (KeyError,TypeError):raise ValueError('Invalid finalized request coverage') from None
            if not isinstance(identity,str):raise ValueError('Invalid finalized request identity')
            if identity in finalizations:raise ValueError('Duplicate finalized request')
            finalizations[identity]=entry['void']
    expected=replay(value['opening'],[entry['report'] for entry in entries],finalizations=finalizations)
    if encoded(expected)!=encoded(value):raise ValueError('Account checkpoint differs from complete replay')
    return deepcopy(expected)


def from_journal(view):
    """Verify and convert a frozen v1/v2 financial checkpoint; no increased write capacity."""
    from core.paper import requested_inspection
    if not isinstance(view,dict) or view.get('version') not in (journals.VERSION,journals.VERSION_V2):
        raise ValueError('Unsupported frozen journal version')
    financial=requested_inspection.verify(requested_inspection.seal(view))
    reports=[]
    for entry in financial['requests']:
        events=entry['events']
        reports.append(segmented.replay(entry['request'],[events[start:start+segmented.SEGMENT_EVENTS] for start in range(0,len(events),segmented.SEGMENT_EVENTS)]))
    finalizations={entry['request']['request_id']:entry['void'] for entry in financial['requests'] if entry.get('void') is not None}
    result=replay(financial['opening'],reports,finalizations=finalizations)
    for key in ('account','active_request_id','last_clock','revision','total_events'):
        if encoded(result[key])!=encoded(financial[key]):raise ValueError('Segmented replay differs from frozen financial checkpoint')
    return result


def capture(engine,account_id):
    # Existing journal.read takes an account lock and verifies the operational gates.
    return from_journal(journals.read(engine,account_id,lock_timeout_ms=2000))
