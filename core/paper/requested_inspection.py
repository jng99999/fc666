"""Bounded complete local Paper journal exports; offline internal consistency."""
from copy import deepcopy
from core.paper import requested_execution as contract, requested_journal as journal
from core.paper.fault_adapter import encoded, sha

VERSION='paper-requested-journal-export-v1'
VERSION_V2='paper-requested-journal-export-v2'
SCOPE='COMPLETE_RETAINED_SINGLE_ACCOUNT_LOCAL_PAPER_JOURNAL'
VIEW_KEYS={'version','opening','account','active_request_id','revision','last_clock','requests','total_events','external_submission_allowed'}


def replay(seed,history,*,version=journal.VERSION):
    if version not in [journal.VERSION,journal.VERSION_V2]:raise ValueError('Unsupported journal version')
    v2=version==journal.VERSION_V2
    if not isinstance(seed,dict) or set(seed)!={'version','account_id','instrument_id','base','created_at','mode','external_submission_allowed'}:
        raise ValueError('Invalid opening envelope')
    expected=journal.opening(*(seed[key] for key in ['account_id','instrument_id','base','created_at']))
    if encoded(seed)!=encoded(expected):raise ValueError('Unsupported opening identity/version')
    if not isinstance(history,list) or len(history)>journal.REQUEST_LIMIT:
        raise ValueError('Request history exceeds account capacity')
    if len(encoded({'opening':seed,'requests':history}).encode())>contract.MAX_BYTES:
        raise ValueError('Journal exceeds 32 MiB')
    current=deepcopy(seed['base']);active=None;last_clock=seed['created_at'];revision=total=0
    clients=set();identities=set();entries=[];void_commands=set();void_count=0
    for entry in history:
        if not isinstance(entry,dict) or set(entry)!=({'request','events','summary','void'} if v2 else {'request','events','summary'}):
            raise ValueError('Invalid request journal entry')
        value=contract.validate(entry['request']);events=entry['events']
        if not isinstance(events,list) or len(events)>journal.TOTAL_EVENTS-total:
            raise ValueError('Event history exceeds account capacity')
        if (active is not None or value['account_id']!=seed['account_id'] or
            value['client_request_id'] in clients or value['request_id'] in identities or
            encoded(value['base'])!=encoded(current) or contract.clock(value['created_at'])<contract.clock(last_clock)):
            raise ValueError('Request chain differs from settled account history')
        summary=contract.reduce(value,events)
        if summary['source_events']!=len(events) or any(event['sequence']!=ordinal for ordinal,event in enumerate(events)):
            raise ValueError('Stored journal requires one ordered row per source sequence')
        void=entry.get('void')
        if void is not None:
            from core.paper import requested_finalization
            summary=requested_finalization.summary(value,void,revision+1+len(events),events)
            if void['command_id'] in void_commands:raise ValueError('Duplicate finalization command')
            void_commands.add(void['command_id']);void_count+=1
        if encoded(entry['summary'])!=encoded(summary):raise ValueError('Request settlement summary mismatch')
        clients.add(value['client_request_id']);identities.add(value['request_id'])
        total+=len(events);revision+=1+len(events)+(1 if void is not None else 0);current=summary['account']
        active=None if summary['local_source_sealed'] or void is not None else value['request_id']
        last_clock=void['created_at'] if void is not None else events[-1]['received_at'] if events else value['created_at']
        result_entry={'request':value,'events':deepcopy(events),'summary':summary}
        if v2:result_entry['void']=deepcopy(void)
        entries.append(result_entry)
    if v2 and not void_count:raise ValueError('Journal v2 requires explicit finalization')
    result={'version':version,'opening':deepcopy(seed),'account':current,'active_request_id':active,
            'revision':revision,'last_clock':last_clock,'requests':entries,'total_events':total,
            'external_submission_allowed':False}
    if v2:result['total_finalizations']=void_count
    if len(encoded(result).encode())>contract.MAX_BYTES:raise ValueError('Journal exceeds 32 MiB')
    return result


def seal(view):
    if not isinstance(view,dict) or set(view)!=(VIEW_KEYS|{'total_finalizations'} if view.get('version')==journal.VERSION_V2 else VIEW_KEYS):raise ValueError('Invalid complete journal envelope')
    if len(encoded(view).encode())>contract.MAX_BYTES:raise ValueError('Journal exceeds 32 MiB')
    result=replay(view['opening'],view['requests'],version=view['version'])
    if encoded(result)!=encoded(view):raise ValueError('Journal differs from complete account replay')
    body={'version':VERSION_V2 if result['version']==journal.VERSION_V2 else VERSION,'scope':SCOPE,'journal':result}
    report={**body,'sha256':sha(body)}
    if len(encoded(report).encode())>contract.MAX_BYTES:raise ValueError('Export exceeds 32 MiB')
    return report


def capture(engine,account_id):
    return seal(journal.read(engine,account_id,lock_timeout_ms=2000))


def verify(report):
    if not isinstance(report,dict) or set(report)!={'version','scope','journal','sha256'} or report['version'] not in [VERSION,VERSION_V2] or report['scope']!=SCOPE:
        raise ValueError('Unsupported journal export envelope/version')
    if len(encoded(report).encode())>contract.MAX_BYTES or encoded(report)!=encoded(seal(report['journal'])):
        raise ValueError('Journal export differs from deterministic replay')
    return deepcopy(report['journal'])
