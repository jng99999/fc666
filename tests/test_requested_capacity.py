"""Structural capacity and current admission; fixture histories only."""
from copy import deepcopy
from datetime import timedelta
from decimal import Decimal
import json, subprocess, sys
import pytest
from core.paper import requested_capacity as capacity, requested_inspection as inspection, requested_journal as journal, requested_execution as execution
from core.paper.fault_adapter import sha
from tests.test_requested_execution import BASE, STAMP, request, event, fill, cumulative
from tests.test_integration import database


def report(events=None):
    req=request();events=[] if events is None else events(req)
    view=inspection.replay(journal.opening('account','fixture:spot',BASE,STAMP.isoformat()),
                           [{'request':req,'events':events,'summary':execution.reduce(req,events)}])
    return capacity.evaluate(inspection.seal(view))


@pytest.mark.parametrize('events,needed',[
    (lambda r:[],3),
    (lambda r:[event(r,0,'SUBMIT')],2),
    (lambda r:[event(r,0,'SUBMIT'),event(r,1,'UNKNOWN_SUBMISSION')],2),
    (lambda r:[event(r,0,'SUBMIT'),fill(r,1)],2),
    (lambda r:[event(r,0,'SUBMIT'),fill(r,1),event(r,2,'CANCEL_REQUEST')],2),
    (lambda r:[event(r,0,'SUBMIT'),fill(r,1),event(r,2,'CANCEL_REQUEST'),event(r,3,'CANCEL_ACK')],1),
    (lambda r:[event(r,0,'SUBMIT'),event(r,1,'REJECT')],1),
    (lambda r:[event(r,0,'SUBMIT'),fill(r,1,size='4.000',fee='3.60')],1),
])
def test_active_shortest_path_is_not_a_settlement_guarantee(events,needed):
    value=report(events);assert capacity.verify(value)==value
    assert value['active_request']['shortest_terminal_path_events']==needed
    assert value['active_request']['shortest_terminal_path_fits']
    assert not value['idle_account_capacity_fits']
    assert not value['future_settlement_guaranteed'] and not value['submission_allowed']


@pytest.mark.parametrize('count',[998,999,1000])
def test_near_limit_completed_history_is_preserved(count):
    def events(r):
        return [event(r,0,'SUBMIT')]+[event(r,i,'ACK') for i in range(1,count-2)]+[
            fill(r,count-2,size='4',fee='3.60'),cumulative(r,count-1,'4','3.60','360',seal=True)]
    value=report(events);assert value['remaining_event_slots']==1000-count
    assert not value['new_request_capacity_fits'] and value['active_request'] is None
    assert capacity.verify(value)==value


def test_partial_hold_with_one_slot_is_reported_without_inventing_release():
    value=report(lambda r:[event(r,0,'SUBMIT'),fill(r,1)]+[event(r,i,'ACK') for i in range(2,999)])
    active=value['active_request'];assert active['remaining_event_slots']==1
    assert active['shortest_terminal_path_events']==2 and not active['shortest_terminal_path_fits']
    assert Decimal(active['funding']['reserved_cash'])==303
    assert capacity.verify(value)==value


@pytest.mark.parametrize('key,value', [('remaining_event_slots',999),('submission_allowed',True),
                                      ('future_settlement_guaranteed',True),('limits',{'account_events':2000})])
def test_resealed_capacity_claims_fail(key,value):
    result=deepcopy(report());result[key]=value
    result['sha256']=sha({k:v for k,v in result.items() if k!='sha256'})
    with pytest.raises(ValueError):capacity.verify(result)


def test_offline_cli_replay_and_duplicate_keys(tmp_path):
    path=tmp_path/'capacity.json';path.write_text(json.dumps(report()))
    command=[sys.executable,'-m','scripts.verify_requested_capacity',str(path)]
    result=subprocess.run(command,capture_output=True,text=True);assert result.returncode==0 and 'guaranteed: False' in result.stdout
    path.write_text('{"version":"a","version":"b"}')
    assert subprocess.run(command,capture_output=True).returncode!=0


@pytest.mark.parametrize('slots',[1,2])
def test_live_previews_and_preparations_refuse_inadequate_headroom_preserving_retries(database,monkeypatch,slots):
    from core.paper import shared_capital_admission as admission, requested_controls as controls, requested_preview as preview, shared_capital as capital
    from tests.test_shared_capital_admission import ready,proposed
    engine,_,_=database;ready(engine);monkeypatch.setattr(journal,'TOTAL_EVENTS',3+slots)
    first,digest=proposed(engine,'a');receipt=admission.prepare(engine,'pool',first,digest);req=receipt['local_preview']['request']
    for value in [event(req,0,'SUBMIT'),fill(req,1,size='4',fee='3.60'),cumulative(req,2,'4','3.60','360',seal=True)]:journal.accept(engine,'a',req['request_id'],value)
    financial,controlled=controls.read(engine,'a')
    future=request(account_id='a',client_request_id='insufficient-space',quantity='1',base=financial['account'],created_at=(STAMP+timedelta(seconds=3)).isoformat())
    proposal={k:future[k] for k in capital.CANDIDATE_KEYS};proposal.update(expected_financial_revision=financial['revision'],expected_control_revision=controlled['revision'])
    # Frozen v1 replay stays valid; current admission has a separate stronger gate.
    old_preview=preview.evaluate(financial,controlled,proposal);assert preview.verify(old_preview)==old_preview
    for operation in [lambda:preview.capture(engine,proposal),lambda:journal.prepare(engine,future),
                      lambda:admission.capture_preview(engine,'pool',proposal),
                      lambda:admission.prepare(engine,'pool',proposal,old_preview['sha256'])]:
        with pytest.raises(ValueError,match='three-event preparation headroom'):operation()
    assert journal.read(engine,'a')==financial
    assert admission.prepare(engine,'pool',first,digest)==receipt
    assert capacity.capture(engine,'a')['financial_revision']==financial['revision']


def test_exactly_three_slots_support_fill_seal_and_keep_duplicate_receipts(database,monkeypatch):
    engine,_,_=database;monkeypatch.setattr(journal,'TOTAL_EVENTS',3)
    journal.create(engine,'account','fixture:spot',BASE,STAMP.isoformat());req=request();journal.prepare(engine,req)
    events=[event(req,0,'SUBMIT'),fill(req,1,size='4',fee='3.60'),cumulative(req,2,'4','3.60','360',seal=True)]
    for value in events:journal.accept(engine,'account',req['request_id'],value)
    final=journal.read(engine,'account');assert final['active_request_id'] is None
    assert Decimal(final['account']['cash'])==Decimal('636.40')
    for value in events:journal.accept(engine,'account',req['request_id'],value)
    assert journal.prepare(engine,req)==final['requests'][0]['summary']
    assert journal.read(engine,'account')==final


def test_legacy_insufficient_headroom_admission_remains_auditable_and_retryable(database,monkeypatch):
    from core.paper import shared_capital_admission as admission, requested_controls as controls, requested_preview as preview, shared_capital as capital
    from tests.test_shared_capital_admission import ready,proposed
    engine,_,_=database;ready(engine);monkeypatch.setattr(journal,'TOTAL_EVENTS',5)
    proposal,digest=proposed(engine,'a');receipt=admission.prepare(engine,'pool',proposal,digest);req=receipt['local_preview']['request']
    for value in [event(req,0,'SUBMIT'),fill(req,1,size='4',fee='3.60'),cumulative(req,2,'4','3.60','360',seal=True)]:journal.accept(engine,'a',req['request_id'],value)
    financial,controlled=controls.read(engine,'a')
    future=request(account_id='a',client_request_id='legacy',quantity='1',base=financial['account'],created_at=(STAMP+timedelta(seconds=3)).isoformat())
    proposal={k:future[k] for k in capital.CANDIDATE_KEYS};proposal.update(expected_financial_revision=financial['revision'],expected_control_revision=controlled['revision'])
    digest=preview.evaluate(financial,controlled,proposal)['sha256']
    # Reproduce admission under the previous policy, then restore the current gate.
    with monkeypatch.context() as old:
        old.setattr(capacity,'require_new',lambda *args:None)
        legacy=admission.prepare(engine,'pool',proposal,digest)
    before=journal.read(engine,'a')
    assert admission.verify(legacy)==legacy and admission.prepare(engine,'pool',proposal,digest)==legacy
    assert journal.read(engine,'a')==before and before['active_request_id']==future['request_id']
