"""Bounded complete local Paper journal exports; offline internal consistency."""
from copy import deepcopy
from core.paper import requested_execution as contract, requested_journal as journal
from core.paper.fault_adapter import encoded, sha

VERSION='paper-requested-journal-export-v1'
SCOPE='COMPLETE_RETAINED_SINGLE_ACCOUNT_LOCAL_PAPER_JOURNAL'
VIEW_KEYS={'version','opening','account','active_request_id','revision','last_clock','requests','total_events','external_submission_allowed'}


def replay(seed,history):
    if not isinstance(seed,dict) or set(seed)!={'version','account_id','instrument_id','base','created_at','mode','external_submission_allowed'}:
        raise ValueError('Invalid opening envelope')
    expected=journal.opening(*(seed[key] for key in ['account_id','instrument_id','base','created_at']))
    if encoded(seed)!=encoded(expected):raise ValueError('Unsupported opening identity/version')
    if not isinstance(history,list) or len(history)>journal.REQUEST_LIMIT:
        raise ValueError('Request history exceeds account capacity')
    if len(encoded({'opening':seed,'requests':history}).encode())>contract.MAX_BYTES:
        raise ValueError('Journal exceeds 32 MiB')
    current=deepcopy(seed['base']);active=None;last_clock=seed['created_at'];revision=total=0
    clients=set();identities=set();entries=[]
    for entry in history:
        if not isinstance(entry,dict) or set(entry)!={'request','events','summary'}:
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
        if encoded(entry['summary'])!=encoded(summary):raise ValueError('Request settlement summary mismatch')
        clients.add(value['client_request_id']);identities.add(value['request_id'])
        total+=len(events);revision+=1+len(events);current=summary['account']
        active=None if summary['local_source_sealed'] else value['request_id']
        last_clock=events[-1]['received_at'] if events else value['created_at']
        entries.append({'request':value,'events':deepcopy(events),'summary':summary})
    result={'version':journal.VERSION,'opening':deepcopy(seed),'account':current,'active_request_id':active,
            'revision':revision,'last_clock':last_clock,'requests':entries,'total_events':total,
            'external_submission_allowed':False}
    if len(encoded(result).encode())>contract.MAX_BYTES:raise ValueError('Journal exceeds 32 MiB')
    return result


def seal(view):
    if not isinstance(view,dict) or set(view)!=VIEW_KEYS:raise ValueError('Invalid complete journal envelope')
    if len(encoded(view).encode())>contract.MAX_BYTES:raise ValueError('Journal exceeds 32 MiB')
    result=replay(view['opening'],view['requests'])
    if encoded(result)!=encoded(view):raise ValueError('Journal differs from complete account replay')
    body={'version':VERSION,'scope':SCOPE,'journal':result}
    report={**body,'sha256':sha(body)}
    if len(encoded(report).encode())>contract.MAX_BYTES:raise ValueError('Export exceeds 32 MiB')
    return report


def capture(engine,account_id):
    return seal(journal.read(engine,account_id,lock_timeout_ms=2000))


def verify(report):
    if not isinstance(report,dict) or set(report)!={'version','scope','journal','sha256'} or report['version']!=VERSION or report['scope']!=SCOPE:
        raise ValueError('Unsupported journal export envelope/version')
    if len(encoded(report).encode())>contract.MAX_BYTES or encoded(report)!=encoded(seal(report['journal'])):
        raise ValueError('Journal export differs from deterministic replay')
    return deepcopy(report['journal'])
