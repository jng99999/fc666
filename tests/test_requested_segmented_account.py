from copy import deepcopy
from datetime import timedelta
from decimal import Decimal
import subprocess
import sys
import pytest
from core.paper import requested_segmented_account as account, requested_segmented_execution as segmented
from core.paper import requested_journal as journal
from core.paper.fault_adapter import encoded,sha
from tests.test_requested_execution import BASE,STAMP,request,event,fill,cumulative
from tests.test_requested_segmented_execution import long_history
from tests.test_integration import database


def opening():return journal.opening('account','fixture:spot',BASE,STAMP.isoformat())


def settled_chain():
    req,batches=long_history()
    first=segmented.replay(req,batches)
    next_req=request('SELL',quantity='2',price='100',base=first['summary']['account'],client_request_id='sell',created_at=(STAMP+timedelta(seconds=1003)).isoformat())
    events=[event(next_req,0,'SUBMIT'),fill(next_req,1,size='2',price='110',fee='2.2',identity='f1',executed_seconds=1004),cumulative(next_req,2,'2','2.2','220',seal=True)]
    for item in events:item['received_at']=(STAMP+timedelta(seconds=1003+item['sequence'])).isoformat()
    return [first,segmented.replay(next_req,[events])]


def test_multi_request_buy_sell_financial_continuity_beyond_frozen_limit():
    reports=settled_chain()
    result=account.replay(opening(),reports)
    assert account.verify(result)==result
    assert result['revision']==1008 and result['total_events']==1006
    assert result['active_request_id'] is None
    assert Decimal(result['account']['cash'])==Decimal('854.2')
    assert Decimal(result['account']['quantity'])==2
    assert Decimal(result['account']['cost_basis'])==Decimal('181.8')
    assert Decimal(result['account']['realized_pnl'])==36
    assert Decimal(result['account']['fees'])==Decimal('5.8')
    # Fill identity is request-scoped; a new request's f1 is a distinct fill.
    assert [entry['report']['summary']['unique_fills'] for entry in result['requests']]==[2,1]
    assert result['ledger_application_supported'] is False


def test_unsealed_unknown_hold_prevents_next_request():
    req=request()
    first=segmented.replay(req,[[event(req,0,'SUBMIT'),event(req,1,'UNKNOWN_SUBMISSION')]])
    view=account.replay(opening(),[first])
    assert view['active_request_id']==req['request_id']
    assert Decimal(view['requests'][0]['report']['summary']['funding']['reserved_cash'])==404
    second=request(client_request_id='second',base=view['account'],created_at=(STAMP+timedelta(seconds=2)).isoformat())
    with pytest.raises(ValueError,match='lineage'):account.replay(opening(),[first,segmented.replay(second,[])])


@pytest.mark.parametrize('field,value',[('account_id','another'),('base',BASE),('client_request_id','request-1'),('created_at',STAMP.isoformat())])
def test_cross_account_stale_funding_duplicate_client_and_clock_rejected(field,value):
    reports=settled_chain()
    old=reports[1]['request']
    args={key:old[key] for key in ('account_id','client_request_id','side','requested_quantity','limit_price','max_fee_rate','base','rules','created_at')}
    args[field]=value
    # A stale sell base has no inventory; request validation must itself deny it.
    from core.paper import requested_execution
    with pytest.raises(ValueError):
        changed=requested_execution.request(**args)
        account.replay(opening(),[reports[0],segmented.replay(changed,[])])


@pytest.mark.parametrize('mutation',[
    lambda v:v['requests'].reverse(),lambda v:v['requests'].pop(0),
    lambda v:v['account'].update(cash='999'),lambda v:v.update(revision=1),
    lambda v:v['requests'][1].update(previous_sha256='0'*64),
    lambda v:v.update(ledger_application_supported=True),lambda v:v.update(external_submission_allowed=True),
])
def test_resealed_missing_reordered_or_forged_account_rejected(mutation):
    result=account.replay(opening(),settled_chain());mutation(result)
    for entry in result['requests']:entry['sha256']=sha({key:value for key,value in entry.items() if key!='sha256'})
    result['sha256']=sha({key:value for key,value in result.items() if key!='sha256'})
    with pytest.raises(ValueError):account.verify(result)


def test_empty_opening_and_unsubmitted_request_keep_exact_funding():
    empty=account.replay(opening(),[])
    assert empty['account']==BASE and empty['revision']==0
    assert account.verify(empty)==empty
    req=request();pending=account.replay(opening(),[segmented.replay(req,[])])
    assert pending['account']==BASE and pending['revision']==1
    assert pending['active_request_id']==req['request_id']
    assert Decimal(pending['requests'][0]['report']['summary']['funding']['reserved_cash'])==404


def test_account_aggregate_capacity_not_reset_per_request(monkeypatch):
    reports=settled_chain();original=deepcopy(reports)
    with monkeypatch.context() as limited:
        limited.setattr(account,'TOTAL_EVENTS',1005)
        with pytest.raises(ValueError,match='total event'):account.replay(opening(),reports)
    with monkeypatch.context() as limited:
        limited.setattr(account,'REQUEST_LIMIT',1)
        with pytest.raises(ValueError,match='request capacity'):account.replay(opening(),reports)
    with monkeypatch.context() as limited:
        limited.setattr(account,'MAX_BYTES',100)
        with pytest.raises(ValueError,match='byte capacity'):account.replay(opening(),reports)
    assert reports==original


def test_isolated_postgresql_capture_matches_existing_funding_and_does_not_write(database):
    engine,_,_=database
    journal.create(engine,'account','fixture:spot',BASE,STAMP.isoformat())
    req=request();journal.prepare(engine,req)
    events=[event(req,0,'SUBMIT'),fill(req,1),event(req,2,'CANCEL_REQUEST'),event(req,3,'CANCEL_ACK'),cumulative(req,4,seal=True)]
    for item in events:journal.accept(engine,'account',req['request_id'],item)
    before=journal.read(engine,'account')
    next_req=request(client_request_id='next',base=before['account'],created_at=(STAMP+timedelta(seconds=5)).isoformat())
    journal.prepare(engine,next_req)
    before=journal.read(engine,'account')
    result=account.capture(engine,'account')
    engine.dispose()
    assert journal.read(engine,'account')==before
    for key in ('account','revision','last_clock','total_events','active_request_id'):assert result[key]==before[key]
    assert result['requests'][1]['report']['summary']==before['requests'][1]['summary']
    assert account.verify(result)==result


def test_incomplete_database_views_fail_closed():
    with pytest.raises(ValueError):account.from_journal({'version':journal.VERSION_V2})
    with pytest.raises(ValueError):account.from_journal({'version':journal.VERSION})


def test_offline_cli_and_duplicate_keys(tmp_path):
    path=tmp_path/'account.json';path.write_text(encoded(account.replay(opening(),settled_chain())))
    cmd=[sys.executable,'-m','scripts.verify_segmented_account',str(path)]
    result=subprocess.run(cmd,capture_output=True)
    assert result.returncode==0 and b'1006' in result.stdout and b'"ledger_application_supported": false' in result.stdout
    path.write_text('{"version":"one","version":"two"}')
    assert subprocess.run(cmd,capture_output=True).returncode==1


def test_isolated_explicit_finalization_preserves_release_and_later_hold(database):
    from core.paper import requested_finalization
    engine,_,_=database
    journal.create(engine,'account','fixture:spot',BASE,STAMP.isoformat())
    req=request();journal.prepare(engine,req)
    requested_finalization.finalize(engine,'account',req['request_id'],'withdraw',1,(STAMP+timedelta(seconds=1)).isoformat())
    next_req=request(client_request_id='next',created_at=(STAMP+timedelta(seconds=2)).isoformat())
    journal.prepare(engine,next_req)
    before=journal.read(engine,'account')
    result=account.capture(engine,'account')
    assert result['revision']==3 and result['total_finalizations']==1
    assert result['active_request_id']==next_req['request_id']
    assert Decimal(result['requests'][0]['summary']['funding']['reserved_cash'])==0
    assert Decimal(result['requests'][1]['summary']['funding']['reserved_cash'])==404
    assert result['requests'][0]['summary']==before['requests'][0]['summary']
    assert account.verify(result)==result
    assert journal.read(engine,'account')==before
    corrupt=deepcopy(result);corrupt['requests'][0]['void']['expected_revision']=2
    corrupt['sha256']=sha({key:value for key,value in corrupt.items() if key!='sha256'})
    with pytest.raises(ValueError):account.verify(corrupt)


def test_finalization_cannot_release_submitted_hold_or_unknown_request():
    from core.paper import requested_finalization
    req=request()
    void=requested_finalization.record(req,'withdraw',1,(STAMP+timedelta(seconds=1)).isoformat())
    pending=segmented.replay(req,[[event(req,0,'SUBMIT')]])
    with pytest.raises(ValueError):account.replay(opening(),[pending],finalizations={req['request_id']:void})
    with pytest.raises(ValueError):account.replay(opening(),[],finalizations={req['request_id']:void})


def test_real_global_account_event_boundary_spans_two_requests():
    first=settled_chain()[0]
    next_req=request('SELL',quantity='2',price='100',base=first['summary']['account'],client_request_id='pending',created_at=(STAMP+timedelta(seconds=1003)).isoformat())
    events=[event(next_req,i,'ACK' if i else 'SUBMIT') for i in range(7189)]
    for item in events:item['received_at']=(STAMP+timedelta(seconds=1003+item['sequence'])).isoformat()
    batches=[events[start:start+128] for start in range(0,len(events),128)]
    second=segmented.replay(next_req,batches)
    result=account.replay(opening(),[first,second])
    assert result['total_events']==8192
    assert Decimal(result['requests'][1]['summary']['funding']['reserved_quantity'])==2
    extra=event(next_req,7189,'ACK');extra['received_at']=(STAMP+timedelta(seconds=8192)).isoformat()
    over=segmented.append(second,[extra])
    with pytest.raises(ValueError,match='total event'):account.replay(opening(),[first,over])


def test_duplicate_withdrawal_commands_and_null_declarations_rejected():
    from core.paper import requested_finalization
    first=request()
    second=request(client_request_id='second',created_at=(STAMP+timedelta(seconds=2)).isoformat())
    void1=requested_finalization.record(first,'same',1,(STAMP+timedelta(seconds=1)).isoformat())
    void2=requested_finalization.record(second,'same',3,(STAMP+timedelta(seconds=3)).isoformat())
    with pytest.raises(ValueError,match='Duplicate finalization'):
        account.replay(opening(),[segmented.replay(first,[]),segmented.replay(second,[])],finalizations={first['request_id']:void1,second['request_id']:void2})
    with pytest.raises(ValueError,match='finalization map'):
        account.replay(opening(),[segmented.replay(first,[])],finalizations={first['request_id']:None})
