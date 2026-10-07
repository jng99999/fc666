"""Complete offline replay against hand-calculated, isolated fixture journals."""
from copy import deepcopy
from datetime import timedelta
from decimal import Decimal
import json
import subprocess
import sys
import pytest
from core.paper import requested_inspection as inspection, requested_journal as journal, requested_execution as contract
from core.paper.fault_adapter import sha
from tests.test_requested_execution import BASE, STAMP, request, event, fill, cumulative


def entry(req,events):return {'request':req,'events':events,'summary':contract.reduce(req,events)}


def fixture_view(pending=False):
    seed=journal.opening('account','fixture:spot',BASE,STAMP.isoformat())
    first=request()
    buy=[event(first,0,'SUBMIT'),fill(first,1,size='4',price='100',fee='4'),cumulative(first,2,'4','4','400',seal=True)]
    first_entry=entry(first,buy)
    second=request('SELL',quantity='2',price='110',base=first_entry['summary']['account'],client_request_id='second',created_at=(STAMP+timedelta(seconds=3)).isoformat())
    def later(value):
        value['received_at']=(STAMP+timedelta(seconds=value['sequence']+3)).isoformat();return value
    sell=[later(event(second,0,'SUBMIT'))]
    if not pending:
        sell.extend([later(fill(second,1,price='120',fee='1.2',executed_seconds=4)),later(event(second,2,'CANCEL_REQUEST')),
                     later(event(second,3,'CANCEL_ACK')),later(fill(second,4,size='0.5',price='115',fee='0.575',identity='late',executed_seconds=5)),
                     later(cumulative(second,5,'1.5','1.775','177.5',seal=True))])
    return inspection.replay(seed,[first_entry,entry(second,sell)])


def reseal(report):
    body={key:value for key,value in report.items() if key!='sha256'}
    report['sha256']=sha(body);return report


def test_complete_two_request_replay_has_independently_calculated_balances():
    view=fixture_view();report=inspection.seal(view);original=deepcopy(report)
    result=inspection.verify(report)
    assert result['revision']==11 and result['total_events']==9 and result['active_request_id'] is None
    assert result['account']=={'cash':'771.725','quantity':'2.5','cost_basis':'252.500000000000000000000000000000000000',
                              'realized_pnl':'24.225000000000000000000000000000000000','fees':'5.775'}
    assert Decimal(result['requests'][-1]['summary']['funding']['reserved_quantity'])==0
    assert result['requests'][-1]['summary']['cancelled_quantity']=='0.5'
    result['account']['cash']='0'
    assert report==original and view==original['journal']


def test_empty_and_pending_complete_journals_do_not_invent_terminal_state():
    seed=journal.opening('account','fixture:spot',BASE,STAMP.isoformat())
    empty=inspection.verify(inspection.seal(inspection.replay(seed,[])))
    assert empty['revision']==0 and empty['total_events']==0 and empty['account']==BASE
    pending=inspection.verify(inspection.seal(fixture_view(True)))
    assert pending['revision']==6 and pending['total_events']==4
    assert pending['active_request_id']==pending['requests'][-1]['request']['request_id']
    assert Decimal(pending['requests'][-1]['summary']['funding']['reserved_quantity'])==2
    assert pending['requests'][-1]['summary']['cancelled_quantity'] is None


@pytest.mark.parametrize('mutation',[
    lambda v:v.update(revision=12),lambda v:v.update(total_events=True),lambda v:v.update(active_request_id='unknown'),
    lambda v:v.update(last_clock=STAMP.isoformat()),lambda v:v['account'].update(cash='999'),
    lambda v:v['opening'].update(external_submission_allowed=0),lambda v:v.update(version='future'),
    lambda v:v['requests'][-1]['summary'].update(cumulative_fees='0'),
    lambda v:v['requests'][0]['events'][0].update(sequence=False),
    lambda v:v['requests'].reverse(),lambda v:v['requests'].pop(),lambda v:v['requests'].pop(0),
    lambda v:v['requests'][0]['events'].pop(1),
    lambda v:v['requests'][0]['events'].append(deepcopy(v['requests'][0]['events'][0])),
    lambda v:v['requests'][0]['events'].reverse(),lambda v:v.update(extra='unsupported'),
])
def test_rehashed_wrong_complete_export_cannot_bypass_replay(mutation):
    report=inspection.seal(fixture_view());mutation(report['journal']);reseal(report)
    with pytest.raises((ValueError,TypeError,KeyError)):inspection.verify(report)


def test_duplicate_client_identity_rejected_even_with_valid_rehashed_requests():
    view=fixture_view();value=view['requests'][1]['request']
    rebuilt=contract.request(*(value[key] if key!='client_request_id' else 'request-1' for key in ['account_id','client_request_id','side','requested_quantity','limit_price','max_fee_rate','base','rules','created_at']))
    events=deepcopy(view['requests'][1]['events'])
    for value in events:value['request_id']=rebuilt['request_id']
    second=entry(rebuilt,events)
    with pytest.raises(ValueError,match='chain'):inspection.replay(view['opening'],[view['requests'][0],second])


def test_unsealed_request_cannot_be_followed_by_another_request():
    view=fixture_view();first=view['requests'][0]
    first=entry(first['request'],first['events'][:2])
    with pytest.raises(ValueError,match='chain'):inspection.replay(view['opening'],[first,view['requests'][1]])


def test_opening_account_identity_and_request_clock_are_chained():
    view=fixture_view();seed=journal.opening('other','fixture:spot',BASE,STAMP.isoformat())
    with pytest.raises(ValueError,match='chain'):inspection.replay(seed,view['requests'])
    seed=journal.opening('account','fixture:spot',BASE,(STAMP+timedelta(seconds=1)).isoformat())
    with pytest.raises(ValueError,match='chain'):inspection.replay(seed,view['requests'])


@pytest.mark.parametrize('mutation',[lambda r:r.update(version='future'),lambda r:r.update(scope='LATEST_ONLY'),lambda r:r.update(extra=1),lambda r:r.update(sha256='0'*64)])
def test_unknown_export_envelopes_or_hashes_fail(mutation):
    report=inspection.seal(fixture_view());mutation(report)
    with pytest.raises(ValueError):inspection.verify(report)


def test_boolean_revision_does_not_equal_integer_zero():
    seed=journal.opening('account','fixture:spot',BASE,STAMP.isoformat())
    report=inspection.seal(inspection.replay(seed,[]));report['journal']['revision']=False;reseal(report)
    with pytest.raises(ValueError):inspection.verify(report)


def test_capacity_limits_apply_to_the_complete_journal(monkeypatch):
    view=fixture_view()
    monkeypatch.setattr(journal,'REQUEST_LIMIT',1)
    with pytest.raises(ValueError,match='capacity'):inspection.seal(view)
    monkeypatch.setattr(journal,'REQUEST_LIMIT',100)
    monkeypatch.setattr(journal,'TOTAL_EVENTS',8)
    with pytest.raises(ValueError,match='capacity'):inspection.seal(view)
    monkeypatch.setattr(journal,'TOTAL_EVENTS',1000)
    monkeypatch.setattr(contract,'MAX_BYTES',100)
    with pytest.raises(ValueError,match='32 MiB'):inspection.seal(view)


def test_cli_validates_offline_and_rejects_tampering_and_duplicate_json_keys(tmp_path,monkeypatch):
    monkeypatch.setenv('DATABASE_URL','postgresql+psycopg://invalid:invalid@127.0.0.1:1/no_connection')
    report=inspection.seal(fixture_view());path=tmp_path/'journal.json';path.write_text(json.dumps(report))
    command=[sys.executable,'-m','scripts.verify_requested_journal',str(path)]
    valid=subprocess.run(command,capture_output=True,text=True,timeout=10)
    assert valid.returncode==0 and "'revision': 11" in valid.stdout
    report['journal']['account']['cash']='999';path.write_text(json.dumps(reseal(report)))
    assert subprocess.run(command,capture_output=True,timeout=10).returncode!=0
    path.write_text('{"version":"ignored","version":"paper-requested-journal-export-v1"}')
    bad=subprocess.run(command,capture_output=True,text=True,timeout=10)
    assert bad.returncode!=0 and 'Duplicate JSON object key' in bad.stderr


def test_complete_input_rewrite_can_only_prove_internal_consistency():
    # The hash is not a signature or a proof of the server's full/fresh history.
    seed=journal.opening('another-local-account','fixture:spot',{**BASE,'cash':'2000'},STAMP.isoformat())
    report=inspection.seal(inspection.replay(seed,[]))
    assert inspection.verify(report)['account']['cash']=='2000'


def test_validly_rehashed_request_with_wrong_base_cannot_credit_new_cash():
    view=fixture_view();value=view['requests'][1]['request']
    values={key:deepcopy(value[key]) for key in ['account_id','client_request_id','side','requested_quantity','limit_price','max_fee_rate','base','rules','created_at']}
    values['base']['cash']='597'
    rebuilt=contract.request(**values);events=deepcopy(view['requests'][1]['events'])
    for value in events:value['request_id']=rebuilt['request_id']
    with pytest.raises(ValueError,match='chain'):
        inspection.replay(view['opening'],[view['requests'][0],entry(rebuilt,events)])


def test_partial_cancel_acknowledgement_and_late_fill_keep_hold_in_export():
    view=fixture_view();second=view['requests'][1]
    second=entry(second['request'],second['events'][:-1])
    report=inspection.seal(inspection.replay(view['opening'],[view['requests'][0],second]))
    result=inspection.verify(report);summary=result['requests'][-1]['summary']
    assert result['revision']==10 and result['total_events']==8
    assert summary['state']=='PARTIAL_CANCEL_ACKNOWLEDGED' and summary['cancelled_quantity'] is None
    assert Decimal(summary['funding']['reserved_quantity'])==Decimal('0.5')
    assert Decimal(summary['funding']['available_quantity'])==2


def test_export_wrapper_size_and_cli_file_limit_are_enforced(tmp_path,monkeypatch):
    from core.paper.fault_adapter import encoded
    view=fixture_view();view_size=len(encoded(view).encode())
    with monkeypatch.context() as patch:
        patch.setattr(contract,'MAX_BYTES',view_size)
        with pytest.raises(ValueError,match='Export exceeds'):inspection.seal(view)
    path=tmp_path/'oversize.json'
    with path.open('wb') as file:file.truncate(contract.MAX_BYTES+1)
    result=subprocess.run([sys.executable,'-m','scripts.verify_requested_journal',str(path)],capture_output=True,text=True,timeout=10)
    assert result.returncode!=0 and 'Export exceeds 32 MiB' in result.stderr
