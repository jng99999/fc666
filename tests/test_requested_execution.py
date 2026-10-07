"""Hand-calculated explicit requests; fixture-only, never product market data."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from decimal import Decimal, localcontext
import json
import subprocess
import sys

import pytest
from core.paper import requested_execution as engine
from core.paper.fault_adapter import sha

STAMP=datetime(2026,1,1,tzinfo=timezone.utc)
RULES={'tick_size':'0.01','quantity_step':'0.001','min_quantity':'0.001','min_notional':'1'}
BASE={'cash':'1000','quantity':'0','cost_basis':'0','realized_pnl':'0','fees':'0'}


def request(side='BUY',quantity='4',price='100',rate='0.01',base=None,**updates):
    values={'account_id':'account','client_request_id':'request-1','side':side,'requested_quantity':quantity,
            'limit_price':price,'max_fee_rate':rate,'base':base or BASE,'rules':RULES,'created_at':STAMP.isoformat()}
    values.update(updates)
    return engine.request(**values)


def event(req,sequence,kind,payload=None):
    return {'event_id':f'e{sequence}','request_id':req['request_id'],'sequence':sequence,
            'received_at':(STAMP+timedelta(seconds=sequence)).isoformat(),'kind':kind,'payload':payload or {}}


def fill(req,seq,size='1',price='90',fee='0.90',identity='f1',executed_seconds=1):
    return event(req,seq,'FILL',{'fill_id':identity,'quantity':size,'price':price,'fee':fee,'executed_at':(STAMP+timedelta(seconds=executed_seconds)).isoformat()})


def cumulative(req,seq,size='1',fees='0.90',notional='90',seal=False):
    payload={'quantity':size,'fees':fees,'notional':notional}
    if seal:payload['last_sequence']=seq-1
    return event(req,seq,'SEAL' if seal else 'RECEIPT',payload)


def n(value):return Decimal(value)


def test_full_request_is_reserved_before_submission():
    req=request()
    original=deepcopy(req)
    view=engine.reduce(req,[])
    assert view['state']=='CREATED' and view['filled_quantity']=='0'
    assert n(view['funding']['reserved_cash'])==404
    assert n(view['funding']['available_cash'])==596
    assert view['account']==BASE
    assert not view['execution_enabled'] and not view['external_reconciliation_supported']
    assert req==original and req==request()


def test_partial_buy_price_improvement_and_late_cancel_fill_then_seal():
    req=request()
    events=[event(req,0,'SUBMIT'),event(req,1,'ACK'),fill(req,2),event(req,3,'CANCEL_REQUEST'),event(req,4,'CANCEL_ACK'),cumulative(req,5)]
    first=engine.reduce(req,events)
    assert first['state']=='PARTIAL_CANCEL_ACKNOWLEDGED' and first['cancelled_quantity'] is None
    assert n(first['account']['cash'])==n('909.10') and n(first['account']['cost_basis'])==n('90.90')
    assert n(first['funding']['reserved_cash'])==303 and n(first['funding']['available_cash'])==n('606.10')
    events.append(fill(req,6,price='95',fee='0.95',identity='f2',executed_seconds=3))
    late=engine.reduce(req,events)
    assert not late['receipt_confirmed'] and not late['local_source_sealed']
    assert n(late['account']['cash'])==n('813.15') and n(late['account']['cost_basis'])==n('186.85')
    assert n(late['funding']['reserved_cash'])==202 and n(late['funding']['available_cash'])==n('611.15')
    events.append(cumulative(req,7,'2','1.85','185'))
    events.append(cumulative(req,8,'2','1.85','185',seal=True))
    final=engine.reduce(req,events)
    assert final['state']=='PARTIAL_CANCELLED' and final['local_source_sealed']
    assert final['filled_quantity']=='2' and final['unfilled_quantity']=='2' and final['cancelled_quantity']=='2'
    assert final['active_unfilled_quantity']=='0' and n(final['funding']['reserved_cash'])==0
    assert n(final['funding']['available_cash'])==n('813.15')
    assert final['unique_fills']==2 and final['receipt_confirmed']
    assert engine.reduce(req,list(reversed(events))+events)==final


def test_sell_inventory_reservation_basis_and_pnl_are_hand_calculated():
    base={**BASE,'cash':'100','quantity':'10','cost_basis':'100'}
    req=request('SELL',price='12',base=base)
    events=[event(req,0,'SUBMIT'),fill(req,1,price='15',fee='0.15'),fill(req,2,price='16',fee='0.16',identity='f2'),event(req,3,'CANCEL_REQUEST'),event(req,4,'CANCEL_ACK')]
    before=engine.reduce(req,events)
    assert n(before['funding']['reserved_quantity'])==2 and n(before['funding']['available_quantity'])==6
    assert before['account']=={'cash':'130.69','quantity':'8','cost_basis':'80.000000000000000000000000000000000000','realized_pnl':'10.690000000000000000000000000000000000','fees':'0.31'}
    final=engine.reduce(req,events+[cumulative(req,5,'2','0.31','31',seal=True)])
    assert final['state']=='PARTIAL_CANCELLED' and final['cancelled_quantity']=='2'
    assert n(final['funding']['available_quantity'])==8 and n(final['funding']['reserved_quantity'])==0


def test_full_fill_has_no_remaining_hold_and_seal_prevents_new_facts():
    req=request()
    events=[event(req,0,'SUBMIT'),fill(req,1,size='4',price='100',fee='4')]
    view=engine.reduce(req,events)
    assert view['state']=='FILLED' and not view['local_source_sealed']
    assert n(view['funding']['reserved_cash'])==0 and n(view['account']['cash'])==596
    events.append(cumulative(req,2,'4','4','400',seal=True))
    assert engine.reduce(req,events)['local_source_sealed']
    with pytest.raises(ValueError):engine.reduce(req,events+[event(req,3,'ACK')])
    assert engine.reduce(req,events+events)==engine.reduce(req,events)


def test_cancel_without_fill_holds_until_source_seal():
    req=request()
    events=[event(req,0,'SUBMIT'),event(req,1,'CANCEL_REQUEST'),event(req,2,'CANCEL_ACK')]
    view=engine.reduce(req,events)
    assert view['state']=='CANCEL_ACKNOWLEDGED' and n(view['funding']['reserved_cash'])==404
    final=engine.reduce(req,events+[cumulative(req,3,'0','0','0',seal=True)])
    assert final['state']=='CANCELLED' and n(final['funding']['available_cash'])==1000
    assert final['account']==BASE and final['cancelled_quantity']=='4'


def test_unknown_submission_and_rejection_never_enable_execution():
    req=request()
    events=[event(req,0,'SUBMIT'),event(req,1,'UNKNOWN_SUBMISSION')]
    assert engine.reduce(req,events)['state']=='UNKNOWN_SUBMISSION'
    events.append(event(req,2,'REJECT'))
    assert engine.reduce(req,events)['state']=='REJECTED_UNCONFIRMED'
    assert n(engine.reduce(req,events)['funding']['reserved_cash'])==404
    final=engine.reduce(req,events+[cumulative(req,3,'0','0','0',seal=True)])
    assert final['state']=='REJECTED' and n(final['funding']['reserved_cash'])==0
    assert not final['execution_enabled']


def test_same_fill_identity_new_delivery_counts_once():
    req=request()
    events=[event(req,0,'SUBMIT'),fill(req,1),fill(req,2)]
    view=engine.reduce(req,events)
    assert view['unique_fills']==1 and n(view['account']['cash'])==n('909.10')
    events[-1]['payload']['fee']='0.80'
    with pytest.raises(ValueError):engine.reduce(req,events)


@pytest.mark.parametrize('updates',[
    {'quantity':'100'}, {'quantity':'0'}, {'quantity':'-1'}, {'quantity':'NaN'}, {'quantity':'Infinity'},
    {'quantity':'0.0001'}, {'price':'100.001'}, {'rate':'1.1'}, {'rate':'-0.01'},
    {'base':{**BASE,'cash':'1'}}, {'side':'SELL'}, {'side':'SHORT'},
    {'created_at':'2026-01-01T00:00:00'}, {'base':{**BASE,'cost_basis':'1'}},
    {'quantity':'1e1000'}, {'base':{**BASE,'cash':1000}},
])
def test_invalid_unfunded_or_off_grid_requests(updates):
    with pytest.raises((ValueError,ArithmeticError)):request(**updates)


@pytest.mark.parametrize('updates',[
    {'size':'5'}, {'size':'0'}, {'size':'0.0001'}, {'price':'100.01','fee':'1'},
    {'price':'90.001'}, {'fee':'1'}, {'fee':'-1'}, {'executed_seconds':-1}, {'executed_seconds':3},
])
def test_invalid_partial_fills_fail_without_mutating_input(updates):
    req=request();events=[event(req,0,'SUBMIT'),fill(req,1,**updates)];original=deepcopy(events)
    with pytest.raises((ValueError,ArithmeticError)):engine.reduce(req,events)
    assert events==original


def test_bad_sequence_receipt_and_seal_rejected():
    req=request()
    for events in [[fill(req,0)], [event(req,0,'SUBMIT'),event(req,2,'ACK')],
                   [event(req,0,'SUBMIT'),event(req,1,'CANCEL_ACK')],
                   [event(req,0,'SUBMIT'),fill(req,1),cumulative(req,2,fees='0')],
                   [event(req,0,'SUBMIT'),cumulative(req,1,'0','0','0',seal=True)]]:
        with pytest.raises(ValueError):engine.reduce(req,events)
    events=[event(req,0,'SUBMIT'),event(req,1,'CANCEL_REQUEST'),event(req,2,'CANCEL_ACK'),cumulative(req,3,'0','0','0',seal=True)]
    events[-1]['payload']['last_sequence']=1
    with pytest.raises(ValueError):engine.reduce(req,events)


def test_conflicting_identity_and_regressing_clocks_rejected():
    req=request()
    first=event(req,0,'SUBMIT');changed=deepcopy(first);changed['received_at']=(STAMP+timedelta(seconds=1)).isoformat()
    with pytest.raises(ValueError):engine.reduce(req,[first,changed])
    other=deepcopy(first);other['event_id']='different'
    with pytest.raises(ValueError):engine.reduce(req,[first,other])
    late=event(req,1,'ACK');late['received_at']=(STAMP-timedelta(seconds=1)).isoformat()
    with pytest.raises(ValueError):engine.reduce(req,[first,late])


def test_sale_cost_basis_dust_removed_on_final_fill_and_next_request_is_valid():
    req=request('SELL',quantity='3',price='12',rate='0',base={**BASE,'cash':'0','quantity':'3','cost_basis':'1'})
    events=[event(req,0,'SUBMIT'),fill(req,1,price='12',fee='0'),fill(req,2,price='12',fee='0',identity='f2'),fill(req,3,price='12',fee='0',identity='f3')]
    view=engine.reduce(req,events)
    assert n(view['account']['quantity'])==n(view['account']['cost_basis'])==0
    assert n(view['account']['realized_pnl'])==35
    next_request=request(quantity='1',price='12',base=view['account'],client_request_id='next')
    assert n(engine.reduce(next_request,[])['funding']['reserved_cash'])==n('12.12')


def test_export_reproduction_type_tampering_rehashed_summary_and_cli(tmp_path):
    req=request();events=[event(req,0,'SUBMIT'),fill(req,1)]
    exported=engine.capture(req,events)
    assert engine.verify(exported)==exported['summary']
    altered=deepcopy(exported);altered['summary']['unique_fills']=1.0
    altered['sha256']=sha({key:value for key,value in altered.items() if key!='sha256'})
    with pytest.raises(ValueError):engine.verify(altered)
    altered=deepcopy(req);altered['external_submission_allowed']=0
    with pytest.raises(ValueError):engine.reduce(altered,[])
    path=tmp_path/'fixture.json';path.write_text(json.dumps(exported))
    result=subprocess.run([sys.executable,'-m','scripts.verify_requested_execution',str(path)],capture_output=True,text=True,timeout=10)
    assert result.returncode==0 and 'Verified requested Paper execution' in result.stdout
    altered=deepcopy(exported);altered['summary']['account']['cash']='1000'
    path.write_text(json.dumps(altered))
    assert subprocess.run([sys.executable,'-m','scripts.verify_requested_execution',str(path)],capture_output=True,timeout=10).returncode!=0


def test_transcript_capacity_and_boolean_seal_watermark():
    req=request()
    with pytest.raises(ValueError):engine.reduce(req,[event(req,0,'SUBMIT')]*1001)
    events=[event(req,0,'SUBMIT'),event(req,1,'CANCEL_REQUEST'),event(req,2,'CANCEL_ACK'),cumulative(req,3,'0','0','0',seal=True)]
    events[-1]['payload']['last_sequence']=True
    with pytest.raises(ValueError):engine.reduce(req,events)


def test_small_component_fill_is_valid_even_below_initial_order_minimum():
    req=request(quantity='1',rules={**RULES,'min_quantity':'1','min_notional':'100'})
    view=engine.reduce(req,[event(req,0,'SUBMIT'),fill(req,1,size='0.001',price='90',fee='0')])
    assert n(view['filled_quantity'])==n('0.001')
    assert n(view['account']['cash'])==n('999.910')
    assert n(view['funding']['reserved_cash'])==n('100.899')


def test_sell_protective_floor_and_rejection_disallow_invalid_economic_facts():
    req=request('SELL',price='12',base={**BASE,'quantity':'4','cost_basis':'40'})
    with pytest.raises(ValueError):
        engine.reduce(req,[event(req,0,'SUBMIT'),fill(req,1,price='11',fee='0')])
    with pytest.raises(ValueError):
        engine.reduce(req,[event(req,0,'SUBMIT'),event(req,1,'REJECT'),fill(req,2,price='12',fee='0')])
    with pytest.raises(ValueError):
        engine.reduce(req,[event(req,0,'SUBMIT'),fill(req,1,price='12',fee='0'),event(req,2,'REJECT')])


def test_buy_adds_to_existing_basis_without_changing_realized_pnl():
    req=request(base={**BASE,'quantity':'2','cost_basis':'150','realized_pnl':'-5','fees':'3'})
    view=engine.reduce(req,[event(req,0,'SUBMIT'),fill(req,1)])
    assert n(view['account']['quantity'])==3
    assert n(view['account']['cost_basis'])==n('240.90')
    assert n(view['account']['realized_pnl'])==-5
    assert n(view['account']['fees'])==n('3.90')
