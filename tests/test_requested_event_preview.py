"""Hand-calculated local source forecasts; no source event persistence."""
from copy import deepcopy
from decimal import Decimal
from datetime import timedelta
import json,subprocess,sys
import pytest
from core.paper import requested_event_preview as preview,requested_controls as controls,requested_journal as journal,requested_inspection as inspection,requested_execution as contract
from core.paper.fault_adapter import sha
from tests.test_requested_execution import BASE,STAMP,request,event,fill,cumulative
from tests.test_requested_controls import LIMITS


def snapshot(events=None,stopped=False):
    req=request();events=events or [];financial=inspection.replay(journal.opening('account','fixture:spot',BASE,STAMP.isoformat()),[{'request':req,'events':events,'summary':contract.reduce(req,events)}])
    policy=controls.policy('account',LIMITS)
    records=[controls.record('account',0,'enroll','ENROLL',0,STAMP.isoformat(),sha(policy),None),controls.record('account',1,'resume','RESUME',0,STAMP.isoformat(),sha(policy),'PAUSED')]
    gates=[controls.decision(req,policy,records[-1],'PREPARE',0,req['created_at'])]
    if events:gates.append(controls.decision(req,policy,records[-1],'SUBMIT',1,events[0]['received_at']))
    if stopped:records.append(controls.record('account',2,'stop','STOP',financial['revision'],financial['last_clock'],sha(policy),'ACTIVE'))
    return req,financial,controls.replay(controls.VERSION,policy,records,gates,financial)


def proposal(req,financial,controlled,value):
    return {'account_id':'account','request_id':req['request_id'],'expected_financial_revision':financial['revision'],
            'expected_control_revision':controlled['revision'],'source':{'kind':'LOCAL_PAPER_OPERATOR_INPUT','source_id':'fixture-input'},'event':value}


def test_new_submit_is_projection_only_and_recomputes_admission():
    req,financial,controlled=snapshot();before=deepcopy(financial)
    report=preview.evaluate(financial,controlled,proposal(req,financial,controlled,event(req,0,'SUBMIT')))
    assert preview.verify(report)==report and not report['event_persisted'] and not report['external_submission_allowed']
    assert report['projected_financial_revision']==2 and report['classification']=='NEW_LOCAL_INPUT'
    assert report['projected_account']==BASE and Decimal(report['projected_request_summary']['funding']['reserved_cash'])==404
    assert report['admission']['phase']=='SUBMIT' and financial==before


def test_unknown_outcome_keeps_full_hold():
    req=request();req,financial,controlled=snapshot([event(req,0,'SUBMIT')])
    report=preview.evaluate(financial,controlled,proposal(req,financial,controlled,event(req,1,'UNKNOWN_SUBMISSION')))
    assert report['projected_request_summary']['state']=='UNKNOWN_SUBMISSION' and report['projected_account']==BASE
    assert Decimal(report['projected_request_summary']['funding']['reserved_cash'])==404 and report['admission'] is None


def test_stopped_account_accepts_late_fill_projection_but_not_new_submit():
    req=request();req,financial,controlled=snapshot([event(req,0,'SUBMIT'),fill(req,1)],True)
    report=preview.evaluate(financial,controlled,proposal(req,financial,controlled,fill(req,2,identity='second')))
    assert Decimal(report['projected_account']['cash'])==Decimal('818.20') and Decimal(report['projected_account']['quantity'])==2
    assert Decimal(report['projected_request_summary']['funding']['reserved_cash'])==202
    req,financial,controlled=snapshot(stopped=True)
    with pytest.raises(ValueError):preview.evaluate(financial,controlled,proposal(req,financial,controlled,event(req,0,'SUBMIT')))


def test_exact_redelivery_preserves_current_account_and_other_active_request():
    req=request();sources=[event(req,0,'SUBMIT'),fill(req,1,size='4',price='90',fee='3.60'),cumulative(req,2,'4','3.60','360',seal=True)]
    req,financial,controlled=snapshot(sources)
    next_req=request(client_request_id='next',base=financial['account'],created_at=sources[-1]['received_at'])
    submit=event(next_req,0,'SUBMIT');submit['received_at']=next_req['created_at']
    filled=fill(next_req,1,executed_seconds=3);filled['received_at']=(STAMP+timedelta(seconds=3)).isoformat()
    following=[submit,filled]
    history=financial['requests']+[{'request':next_req,'events':following,'summary':contract.reduce(next_req,following)}]
    financial=inspection.replay(financial['opening'],history)
    gates=controlled['gates']+[controls.decision(next_req,controlled['policy'],controlled['records'][-1],'PREPARE',4,next_req['created_at']),controls.decision(next_req,controlled['policy'],controlled['records'][-1],'SUBMIT',5,submit['received_at'])]
    controlled=controls.replay(controls.VERSION,controlled['policy'],controlled['records'],gates,financial)
    report=preview.evaluate(financial,controlled,proposal(req,financial,controlled,sources[1]))
    assert report['classification']=='EXACT_EVENT_REDELIVERY' and report['projected_financial_revision']==7
    assert report['projected_account']==financial['account'] and report['projected_active_request_id']==next_req['request_id']
    assert report['projected_request_summary']['account']!=financial['account']
    assert report['admission'] is None and preview.verify(report)==report


@pytest.mark.parametrize('change',[
 lambda p:p.update(expected_financial_revision=99),lambda p:p.update(expected_control_revision=True),
 lambda p:p['source'].update(kind='EXCHANGE_VERIFIED'),lambda p:p['source'].update(source_id=''),
 lambda p:p['event'].update(sequence=1),lambda p:p['event'].update(request_id='0'*64),
 lambda p:p['event'].update(payload={'invented':'ack'}),lambda p:p.update(account_id='other'),
])
def test_bad_source_clock_sequence_or_checkpoint_is_rejected(change):
    req,financial,controlled=snapshot();value=proposal(req,financial,controlled,event(req,0,'SUBMIT'));change(value)
    with pytest.raises((ValueError,TypeError)):preview.evaluate(financial,controlled,value)


@pytest.mark.parametrize('change',[
 lambda r:r.update(event_persisted=True),lambda r:r.update(projected_financial_revision=10),
 lambda r:r['projected_account'].update(cash='2000'),lambda r:r['projected_request_summary']['funding'].update(reserved_cash='0'),
 lambda r:r.update(classification='EXCHANGE_CONFIRMED'),lambda r:r['admission'].update(control_revision=99),
 lambda r:r['proposal']['source'].update(kind='EXCHANGE_VERIFIED'),lambda r:r.update(version='future-v99'),
])
def test_rehashed_inconsistent_projection_fails_offline_verification(change):
    req,financial,controlled=snapshot();report=preview.evaluate(financial,controlled,proposal(req,financial,controlled,event(req,0,'SUBMIT')));change(report)
    report['sha256']=sha({key:value for key,value in report.items() if key!='sha256'})
    with pytest.raises((ValueError,TypeError,KeyError)):preview.verify(report)


def test_offline_cli_needs_no_database_and_rejects_duplicate_json(tmp_path,monkeypatch):
    req,financial,controlled=snapshot();report=preview.evaluate(financial,controlled,proposal(req,financial,controlled,event(req,0,'SUBMIT')))
    path=tmp_path/'preview.json';path.write_text(json.dumps(report));monkeypatch.setenv('DATABASE_URL','postgresql+psycopg://invalid:invalid@127.0.0.1:1/no_db')
    good=subprocess.run([sys.executable,'-m','scripts.verify_requested_event_preview',str(path)],capture_output=True,text=True,timeout=10)
    assert good.returncode==0 and 'event_persisted' in good.stdout,good.stderr
    path.write_text('{"version":"one","version":"two"}')
    bad=subprocess.run([sys.executable,'-m','scripts.verify_requested_event_preview',str(path)],capture_output=True,text=True,timeout=10)
    assert bad.returncode!=0 and 'Verified' not in bad.stdout


def test_local_seal_release_remains_hypothetical():
    req=request();req,financial,controlled=snapshot([event(req,0,'SUBMIT'),event(req,1,'REJECT')]);before=deepcopy(financial)
    report=preview.evaluate(financial,controlled,proposal(req,financial,controlled,cumulative(req,2,'0','0','0',seal=True)))
    assert report['projected_active_request_id'] is None and report['projected_request_summary']['funding']['reserved_cash']=='0'
    assert report['projected_account']==BASE and financial==before and financial['active_request_id']==req['request_id']


def test_v2_void_history_supports_new_preview_but_blocks_old_voided_submit():
    from tests.test_requested_finalization import pure_view,time
    financial=pure_view();old=financial['requests'][0]['request'];next_req=request(client_request_id='next',created_at=time(2))
    entries=financial['requests']+[{'request':next_req,'events':[],'summary':contract.reduce(next_req,[]),'void':None}]
    financial=inspection.replay(financial['opening'],entries,version=journal.VERSION_V2)
    policy=controls.policy('account',LIMITS)
    records=[controls.record('account',0,'enroll','ENROLL',0,STAMP.isoformat(),sha(policy),None),controls.record('account',1,'resume','RESUME',0,STAMP.isoformat(),sha(policy),'PAUSED')]
    gates=[controls.decision(old,policy,records[-1],'PREPARE',0,old['created_at']),controls.decision(next_req,policy,records[-1],'PREPARE',2,next_req['created_at'])]
    controlled=controls.replay(controls.VERSION,policy,records,gates,financial)
    source=event(next_req,0,'SUBMIT');source['received_at']=time(2)
    report=preview.evaluate(financial,controlled,proposal(next_req,financial,controlled,source))
    assert preview.verify(report)==report and report['journal']['version']==inspection.VERSION_V2 and report['projected_financial_revision']==4
    with pytest.raises(ValueError):preview.evaluate(financial,controlled,proposal(old,financial,controlled,event(old,0,'SUBMIT')))
