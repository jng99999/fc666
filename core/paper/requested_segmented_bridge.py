"""Internal legacy-local atomic batch bridge; no new financial version or HTTP grant."""
from copy import deepcopy
import re
from sqlalchemy import select,text
from core.storage.models import PaperCapitalMemberRecord as Member
from core.paper import requested_segmented_account as accounts
from core.paper import requested_segmented_execution as segments
from core.paper import requested_journal as journals, requested_controls as controls
from core.paper.fault_adapter import encoded

BATCH_LIMIT=16


def _events(entry):
    return [event for segment in entry['report']['segments'] for event in segment['events']]


def _financial(report):
    return {key:report[key] for key in ('opening','account','active_request_id','last_clock','revision','total_events','total_finalizations')}


def _recorded(candidate,current):
    """A complete candidate is acknowledged only when every immutable event exists."""
    if encoded(candidate['opening'])!=encoded(current['opening']) or len(candidate['requests'])>len(current['requests']):return False
    for entry,now in zip(candidate['requests'],current['requests']):
        events,stored=_events(entry),_events(now)
        if (encoded(entry['report']['request'])!=encoded(now['report']['request']) or
            len(events)>len(stored) or encoded(events)!=encoded(stored[:len(events)]) or
            (entry['void'] is not None and encoded(entry['void'])!=encoded(now['void']))):return False
    return True


def apply(engine,account_id,expected_revision,expected_sha256,candidate):
    """Apply one existing-request batch to unmanaged local accounts, atomically.

    The supplied candidate is consistency evidence, not approval or venue origin.
    Frozen1000-event capacity, current internal legacy guards and exact history apply.
    """
    if (type(expected_revision) is not int or expected_revision<1 or
        not isinstance(expected_sha256,str) or re.fullmatch('[0-9a-f]{64}',expected_sha256) is None):
        raise ValueError('Exact financial checkpoint required')
    candidate=accounts.verify(candidate)
    entries=candidate['requests']
    if not entries or candidate['opening']['account_id']!=account_id:
        raise ValueError('Candidate account/request mismatch')
    last=entries[-1]
    if last['void'] is not None or not last['report']['segments']:
        raise ValueError('Only a new source segment can be applied')
    batch=last['report']['segments'][-1]['events']
    if not 1<=len(batch)<=BATCH_LIMIT:raise ValueError('Bridge batch capacity exceeded')
    if candidate['total_events']>journals.TOTAL_EVENTS:
        raise ValueError('Frozen database event capacity remains enforced')
    prior_reports=[entry['report'] for entry in entries[:-1]]
    prior_reports.append(segments.replay(last['report']['request'],[segment['events'] for segment in last['report']['segments'][:-1]]))
    finalizations={entry['report']['request']['request_id']:entry['void'] for entry in entries[:-1] if entry['void'] is not None}
    predecessor=accounts.replay(candidate['opening'],prior_reports,finalizations=finalizations)
    if predecessor['revision']!=expected_revision or predecessor['sha256']!=expected_sha256:
        raise ValueError('Candidate does not bind the expected financial checkpoint')
    with journals.transaction(engine) as db:
        db.execute(text("SELECT set_config('lock_timeout', '2000ms', true)"))
        row=journals.lock(db,account_id);view=journals.audit(db,row)
        if db.scalar(select(Member.account_id).where(Member.account_id==account_id)) is not None:
            raise ValueError('Pool accounts require whole-pool operational binding')
        if controls.check(db,row,view)['coverage']!='LEGACY_UNMANAGED':
            raise ValueError('Controlled accounts require separately authorized source/owner binding')
        current=accounts.from_journal(view)
        if _recorded(candidate,current):return deepcopy(candidate)
        if current['revision']!=expected_revision or current['sha256']!=expected_sha256:
            raise ValueError('Stale financial checkpoint')
        request_id=last['report']['request']['request_id']
        if row.active_request_id!=request_id:
            raise ValueError('Only the current prepared request can receive the batch')
        for event in batch:
            journals._accept(db,row,view,request_id,event)
            view=journals.audit(db,row)
        final=accounts.from_journal(view)
        if encoded(_financial(final))!=encoded(_financial(candidate)) or not _recorded(candidate,final):
            raise ValueError('Financial commit differs from candidate replay')
        return deepcopy(candidate)
