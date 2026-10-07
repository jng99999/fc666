"""Immutable local input receipts, never authenticated exchange provenance."""
from copy import deepcopy
from sqlalchemy import select,text
from core.paper import requested_journal as journal,requested_execution as contract,requested_controls as controls,requested_inspection as inspection,requested_event_preview as preview
from core.paper.fault_adapter import encoded,sha
from core.storage.models import RequestedPaperSourceRecord as Source,RequestedPaperEventRecord as Event,RequestedPaperRequestRecord as Request

VERSION='paper-requested-local-source-v1'
EXPORT_VERSION='paper-requested-sources-export-v1'


def before_event(financial,index,sequence):
    entries=deepcopy(financial['requests'][:index+1]);entry=entries[-1]
    entry['events']=entry['events'][:sequence];entry['summary']=contract.reduce(entry['request'],entry['events'])
    if 'void' in entry:entry['void']=None
    v2=any(item.get('void') is not None for item in entries)
    if not v2:
        for item in entries:item.pop('void',None)
    return inspection.replay(financial['opening'],entries,version=journal.VERSION_V2 if v2 else journal.VERSION)


def reconstruct(financial,controlled,index,sequence,source):
    before=before_event(financial,index,sequence)
    records=[record for record in controlled['records'] if record['journal_revision']<=before['revision']]
    if not records:raise ValueError('Source lacks controlled origin')
    included={entry['request']['request_id']:entry for entry in before['requests']}
    gates=[gate for gate in controlled['gates'] if gate['request_id'] in included and (gate['phase']=='PREPARE' or included[gate['request_id']]['events'])]
    old=controls.replay(controls.VERSION,controlled['policy'],records,gates,before)
    entry=financial['requests'][index];event=entry['events'][sequence]
    proposal={'account_id':financial['opening']['account_id'],'request_id':entry['request']['request_id'],
              'expected_financial_revision':before['revision'],'expected_control_revision':old['revision'],'source':deepcopy(source),'event':deepcopy(event)}
    return preview.evaluate(before,old,proposal)


def receipt(report):
    if report['classification']!='NEW_LOCAL_INPUT':raise ValueError('Only a new source input creates a receipt')
    value=report['proposal'];event=value['event']
    return {'version':VERSION,'account_id':value['account_id'],'request_id':value['request_id'],'event_id':event['event_id'],'sequence':event['sequence'],
            'source':deepcopy(value['source']),'expected_financial_revision':value['expected_financial_revision'],
            'expected_control_revision':value['expected_control_revision'],'preview_sha256':report['sha256'],
            'event_sha256':sha(event),'summary_sha256':sha(report['projected_request_summary']),'external_submission_allowed':False}


def replay(financial,controlled,records):
    if not isinstance(records,list) or len(records)!=financial['total_events'] or len(records)>journal.TOTAL_EVENTS:raise ValueError('Incomplete source evidence')
    ordinal=0
    for index,entry in enumerate(financial['requests']):
        for sequence,event in enumerate(entry['events']):
            slot=records[ordinal];ordinal+=1
            if not isinstance(slot,dict) or set(slot)!={'request_id','sequence','event_id','source_version','receipt'}:
                raise ValueError('Invalid source evidence slot')
            identity={'request_id':entry['request']['request_id'],'sequence':sequence,'event_id':event['event_id']}
            if encoded({key:slot[key] for key in identity})!=encoded(identity):raise ValueError('Source/event identity differs')
            if slot['source_version'] is None:
                if slot['receipt'] is not None:raise ValueError('Undeclared source receipt')
            else:
                if slot['source_version']!=VERSION or not isinstance(slot['receipt'],dict):raise ValueError('Missing/unsupported declared source')
                report=reconstruct(financial,controlled,index,sequence,slot['receipt'].get('source'))
                if encoded(slot['receipt'])!=encoded(receipt(report)):raise ValueError('Source receipt differs from historical replay')
    return deepcopy(records)


def check(db,row,financial,controlled):
    events=list(db.scalars(select(Event).join(Request,Request.request_id==Event.request_id).where(Request.account_id==row.account_id).order_by(Request.ordinal,Event.sequence).limit(journal.TOTAL_EVENTS+1)))
    sources=list(db.scalars(select(Source).where(Source.account_id==row.account_id).limit(journal.TOTAL_EVENTS+1)))
    if len(sources)>journal.TOTAL_EVENTS:raise ValueError('Source history exceeds capacity')
    lookup={(value.request_id,value.sequence):value for value in sources};records=[]
    for event in events:
        stored=lookup.pop((event.request_id,event.sequence),None)
        if stored is not None and (stored.event_id!=event.event_id or stored.payload_sha256!=sha(stored.payload)):
            raise ValueError('Source storage metadata/hash mismatch')
        records.append({'request_id':event.request_id,'sequence':event.sequence,'event_id':event.event_id,'source_version':event.source_version,
                        'receipt':deepcopy(stored.payload) if stored is not None else None})
    if lookup:raise ValueError('Orphan source evidence')
    return replay(financial,controlled,records)


def accept(engine,proposal,preview_sha256):
    import re
    if not isinstance(proposal,dict) or set(proposal)!=preview.KEYS or not isinstance(proposal['event'],dict) or not isinstance(preview_sha256,str) or re.fullmatch('[0-9a-f]{64}',preview_sha256) is None:
        raise ValueError('Exact source proposal and digest required')
    with journal.transaction(engine) as db:
        db.execute(text("SELECT set_config('lock_timeout', '2000ms', true)"))
        row=journal.lock(db,proposal['account_id']);financial=journal.audit(db,row);controlled=controls.check(db,row,financial)
        if controlled['coverage']!='CONTROLLED':raise ValueError('Controlled account required')
        records=check(db,row,financial,controlled)
        existing=next((slot for slot in records if slot['request_id']==proposal['request_id'] and slot['event_id']==proposal['event'].get('event_id')),None)
        if existing is not None:
            if existing['receipt'] is None:raise ValueError('Unlabeled event cannot acquire retrospective provenance')
            index=next(index for index,entry in enumerate(financial['requests']) if entry['request']['request_id']==proposal['request_id'])
            report=reconstruct(financial,controlled,index,existing['sequence'],existing['receipt']['source'])
            if encoded(report['proposal'])!=encoded(proposal) or report['sha256']!=preview_sha256:raise ValueError('Conflicting source input retry')
            value=existing['receipt']
        else:
            report=preview.evaluate(financial,controlled,proposal)
            if report['sha256']!=preview_sha256:raise ValueError('Event preview differs from audited checkpoint')
            value=receipt(report)
            stored=Source(request_id=value['request_id'],sequence=value['sequence'],account_id=value['account_id'],event_id=value['event_id'],payload=value,payload_sha256=sha(value))
            journal._accept(db,row,financial,value['request_id'],proposal['event'],source_receipt=stored)
        return {'version':'paper-requested-source-command-result-v1','accepted_receipt':deepcopy(value),'event_committed':True,'external_submission_allowed':False}


def capture(engine,account_id):
    with journal.transaction(engine) as db:
        db.execute(text("SELECT set_config('lock_timeout', '2000ms', true)"))
        row=journal.lock(db,account_id);financial=journal.audit(db,row);controlled=controls.check(db,row,financial)
        records=check(db,row,financial,controlled)
        body={'version':EXPORT_VERSION,'journal':inspection.seal(financial),'controls':controlled,'sources':records}
        report={**body,'sha256':sha(body)}
        if len(encoded(report).encode())>contract.MAX_BYTES:raise ValueError('Source export exceeds 32 MiB')
        return report


def verify(report):
    if not isinstance(report,dict) or set(report)!={'version','journal','controls','sources','sha256'} or report['version']!=EXPORT_VERSION or len(encoded(report).encode())>contract.MAX_BYTES:
        raise ValueError('Invalid source export envelope/bounds')
    financial=inspection.verify(report['journal']);view=report['controls']
    if not isinstance(view,dict):raise ValueError('Invalid control view')
    marker=controls.VERSION if view.get('coverage')=='CONTROLLED' else None if view.get('coverage')=='LEGACY_UNMANAGED' else 'unsupported'
    expected=controls.replay(marker,view.get('policy'),view.get('records'),view.get('gates'),financial)
    records=replay(financial,expected,report['sources'])
    body={'version':EXPORT_VERSION,'journal':report['journal'],'controls':expected,'sources':records}
    if encoded(report)!=encoded({**body,'sha256':sha(body)}):raise ValueError('Source export differs from replay')
    return deepcopy(body)
