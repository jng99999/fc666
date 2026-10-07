"""Independent hand calculations and offline evidence; no production accounts."""
from copy import deepcopy
from decimal import Decimal
import json,subprocess,sys
import pytest
from core.paper import requested_preview as preview,requested_controls as controls,requested_journal as journal,requested_inspection as inspection
from core.paper.fault_adapter import sha
from tests.test_requested_controls import LIMITS
from tests.test_requested_execution import BASE,STAMP,RULES


def proposal(**updates):
    return {'account_id':'account','client_request_id':'preview-1','side':'BUY','requested_quantity':'4','limit_price':'100','max_fee_rate':'0.01',
            'rules':RULES,'created_at':STAMP.isoformat(),'expected_financial_revision':0,'expected_control_revision':2,**updates}


def snapshot(base=None,state='ACTIVE',limits=None):
    financial=inspection.replay(journal.opening('account','fixture:spot',base or BASE,STAMP.isoformat()),[])
    policy=controls.policy('account',limits or LIMITS)
    one=controls.record('account',0,'enroll','ENROLL',0,STAMP.isoformat(),sha(policy),None)
    records=[one]
    if state!='PAUSED':records.append(controls.record('account',1,'resume','RESUME',0,STAMP.isoformat(),sha(policy),'PAUSED'))
    if state in ['HALTED','STOPPED']:records.append(controls.record('account',2,'change','HALT' if state=='HALTED' else 'STOP',0,STAMP.isoformat(),sha(policy),'ACTIVE'))
    return financial,controls.replay(controls.VERSION,policy,records,[],financial)


@pytest.mark.parametrize('side',['BUY','SELL'])
def test_hand_calculated_projected_funding_does_not_change_account(side):
    base=BASE if side=='BUY' else {**BASE,'quantity':'10','cost_basis':'500','fees':'3','realized_pnl':'7'}
    financial,controlled=snapshot(base);report=preview.evaluate(financial,controlled,proposal(side=side))
    assert preview.verify(report)==report and report['request']['base']==base
    projected=report['projected_summary'];assert projected['account']==base and projected['source_events']==0
    assert {key:Decimal(value) for key,value in projected['funding'].items()}==({'reserved_cash':Decimal('404'),'reserved_quantity':Decimal('0'),'available_cash':Decimal('596'),'available_quantity':Decimal('0')} if side=='BUY' else {'reserved_cash':Decimal('0'),'reserved_quantity':Decimal('4'),'available_cash':Decimal('1000'),'available_quantity':Decimal('6')})
    assert report['reservation_created'] is False and not report['external_submission_allowed']
    assert financial['revision']==0 and financial['requests']==[]


@pytest.mark.parametrize('state,side',[('PAUSED','BUY'),('STOPPED','SELL'),('HALTED','BUY')])
def test_current_control_state_blocks_preview(state,side):
    financial,controlled=snapshot({**BASE,'quantity':'10'},state)
    with pytest.raises(ValueError):preview.evaluate(financial,controlled,proposal(side=side,expected_control_revision=controlled['revision']))


def test_halted_sell_is_allowed_but_not_a_resume():
    financial,controlled=snapshot({**BASE,'quantity':'10'},'HALTED')
    report=preview.evaluate(financial,controlled,proposal(side='SELL',expected_control_revision=3))
    assert report['admission']['state']=='HALTED' and controlled['state']=='HALTED'


@pytest.mark.parametrize('updates',[{'expected_financial_revision':1},{'expected_control_revision':1},{'expected_control_revision':True},
 {'expected_financial_revision':False},{'requested_quantity':'11'},{'limit_price':'100.001'},{'requested_quantity':'4.0001'},
 {'max_fee_rate':'0.02'},{'created_at':'2025-01-01T00:00:00+00:00'},{'base':BASE},{'account_id':'other'}])
def test_invalid_or_stale_proposal_is_rejected(updates):
    financial,controlled=snapshot()
    with pytest.raises((ValueError,TypeError)):preview.evaluate(financial,controlled,proposal(**updates))


@pytest.mark.parametrize('mutation',[
 lambda r:r.update(reservation_created=True),lambda r:r.update(external_submission_allowed=True),
 lambda r:r['projected_summary']['funding'].update(reserved_cash='0'),lambda r:r['request']['base'].update(cash='2000'),
 lambda r:r['proposal'].update(requested_quantity='3'),lambda r:r['admission'].update(control_revision=99),
 lambda r:r['controls'].update(state='PAUSED'),lambda r:r.update(version='future-preview-v99'),
 lambda r:r['proposal'].update(expected_financial_revision=True),lambda r:r['controls']['policy'].update(max_order_quantity='999'),
])
def test_rehashed_semantic_changes_fail_offline_replay(mutation):
    financial,controlled=snapshot();report=preview.evaluate(financial,controlled,proposal());mutation(report)
    report['sha256']=sha({key:value for key,value in report.items() if key!='sha256'})
    with pytest.raises((ValueError,TypeError,KeyError)):preview.verify(report)


def test_offline_cli_needs_no_database_and_rejects_duplicate_json(tmp_path,monkeypatch):
    financial,controlled=snapshot();report=preview.evaluate(financial,controlled,proposal());path=tmp_path/'preview.json';path.write_text(json.dumps(report))
    monkeypatch.setenv('DATABASE_URL','postgresql+psycopg://invalid:invalid@127.0.0.1:1/no_db')
    good=subprocess.run([sys.executable,'-m','scripts.verify_requested_preview',str(path)],capture_output=True,text=True,timeout=10)
    assert good.returncode==0 and 'reservation_created' in good.stdout,good.stderr
    path.write_text('{"version":"one","version":"two"}')
    bad=subprocess.run([sys.executable,'-m','scripts.verify_requested_preview',str(path)],capture_output=True,text=True,timeout=10)
    assert bad.returncode!=0 and 'Verified' not in bad.stdout


@pytest.mark.parametrize('key,value',[('max_order_quantity','3'),('max_request_notional','399'),('max_buy_inventory_quantity','3'),('max_fee_rate','0.009'),('min_cash_after_buy','597'),('allowed_sides',['SELL'])])
def test_full_quantity_policy_denial_is_recomputed(key,value):
    financial,controlled=snapshot(limits={**LIMITS,key:value})
    with pytest.raises(ValueError):preview.evaluate(financial,controlled,proposal())


@pytest.mark.parametrize('side,base',[('BUY',{**BASE,'cash':'403'}),('SELL',{**BASE,'quantity':'3'})])
def test_full_quantity_must_be_funded(side,base):
    financial,controlled=snapshot(base)
    with pytest.raises(ValueError):preview.evaluate(financial,controlled,proposal(side=side))


def test_v2_void_history_preview_and_reused_identity_rejection():
    from tests.test_requested_finalization import pure_view,time
    financial=pure_view();policy=controls.policy('account',LIMITS)
    records=[controls.record('account',0,'enroll','ENROLL',2,time(2),sha(policy),None),controls.record('account',1,'resume','RESUME',2,time(2),sha(policy),'PAUSED')]
    controlled=controls.replay(controls.VERSION,policy,records,[],financial)
    report=preview.evaluate(financial,controlled,proposal(expected_financial_revision=2,created_at=time(2)))
    assert preview.verify(report)==report and report['journal']['version']==inspection.VERSION_V2
    with pytest.raises(ValueError):preview.evaluate(financial,controlled,proposal(client_request_id='request-1',expected_financial_revision=2,created_at=time(2)))
