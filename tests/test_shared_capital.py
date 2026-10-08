from copy import deepcopy
from decimal import localcontext
import pytest
from core.paper import shared_capital as capital,requested_inspection as inspection,requested_journal as journal,requested_execution as execution
from core.paper.fault_adapter import sha
from tests.test_requested_execution import request,event,fill,BASE,STAMP


def evidence(account,events=()):
    req=request(account_id=account)
    values=[factory(req,index) for index,factory in enumerate(events)]
    seed=journal.opening(account,'BTCUSDT',BASE,STAMP.isoformat())
    entries=[dict(request=req,events=values,summary=execution.reduce(req,values))] if events else []
    return inspection.seal(inspection.replay(seed,entries))


def pool(maximum='700',cash='2500'):
    return dict(pool_id='pool',quote_asset='USDT',opening_quote_cash=cash,max_reserved_quote=maximum,
                allocations=[dict(account_id=name,instrument_id='BTCUSDT',opening_quote_cash='1000') for name in ['a','b']])


def proposal(size='2'):
    req=request(account_id='b')
    result={key:req[key] for key in capital.CANDIDATE_KEYS}
    result['requested_quantity']=size
    return result


def test_aggregate_forecast_does_not_double_count_allocations():
    journals=[evidence('a',[lambda req,seq:event(req,seq,'SUBMIT')]),evidence('b')]
    report=capital.evaluate(pool(),journals,proposal())
    assert report['current_quote_cash']=='2500' and report['unallocated_opening_quote']=='500'
    assert report['reserved_quote'].startswith('404.') and report['available_quote_cash'].startswith('2096.')
    assert report['forecast']['projected_reserved_quote'].startswith('606.') and report['forecast']['capital_forecast_allowed']
    denied=capital.evaluate(pool(),journals,proposal('4'))
    assert not denied['forecast']['capital_forecast_allowed'] and denied['forecast']['reason']=='QUOTE_RESERVATION_CAP_EXCEEDED'
    assert capital.verify(report)==report and not report['submission_allowed'] and not report['shared_reservation_committed']


def test_unknown_and_partial_fills_preserve_aggregate_holds():
    seq=[lambda req,n:event(req,n,'SUBMIT'),lambda req,n:event(req,n,'UNKNOWN_SUBMISSION')]
    unknown=capital.evaluate(pool(),[evidence('a',seq),evidence('b')])
    assert unknown['reserved_quote'].startswith('404.')
    seq.append(lambda req,n:fill(req,n))
    partial=capital.evaluate(pool(),[evidence('a',seq),evidence('b')])
    assert partial['reserved_quote'].startswith('303.') and partial['current_quote_cash'].startswith('2409.10')
    assert partial['available_quote_cash'].startswith('2106.10')
    assert partial['accounts'][0]['available_quote_cash'].startswith('606.10')


@pytest.mark.parametrize('update',[{'opening_quote_cash':'1999'},{'max_reserved_quote':'2501'},{'opening_quote_cash':'NaN'},{'quote_asset':'usd/t'},{'pool_id':''}])
def test_invalid_pool_bounds(update):
    with pytest.raises((ValueError,ArithmeticError)):capital.evaluate({**pool(),**update},[evidence('a'),evidence('b')])


@pytest.mark.parametrize('mode',['missing','duplicate','swapped','different_cash','different_instrument'])
def test_membership_and_fixed_opening_binding(mode):
    definition=pool();journals=[evidence('a'),evidence('b')]
    if mode=='missing':journals.pop()
    elif mode=='duplicate':definition['allocations'][1]['account_id']='a'
    elif mode=='swapped':journals.reverse()
    elif mode=='different_cash':definition['allocations'][0]['opening_quote_cash']='999'
    else:definition['allocations'][0]['instrument_id']='ETHUSDT'
    with pytest.raises(ValueError):capital.evaluate(definition,journals)


def test_local_cash_cannot_borrow_unallocated_pool_money():
    candidate=proposal('10')
    with pytest.raises(ValueError):capital.evaluate(pool(maximum='2000'),[evidence('a'),evidence('b')],candidate)


def test_resealed_aggregate_or_permission_tampering_rejected():
    report=capital.evaluate(pool(),[evidence('a'),evidence('b')],proposal())
    for field,value in [('reserved_quote','1'),('submission_allowed',True),('coherent_capture_supported',True),('current_quote_cash','4500')]:
        forged={**deepcopy(report),field:value};forged['sha256']=sha({k:v for k,v in forged.items() if k!='sha256'})
        with pytest.raises(ValueError):capital.verify(forged)


def test_decimal_arithmetic_independent_of_ambient_precision():
    journals=[evidence('a',[lambda req,n:event(req,n,'SUBMIT')]),evidence('b')]
    expected=capital.evaluate(pool(),journals,proposal())
    with localcontext() as context:
        context.prec=6
        assert capital.evaluate(pool(),journals,proposal())==expected


def test_existing_cross_account_breach_is_reported_without_releasing_holds():
    journals=[evidence(name,[lambda req,n:event(req,n,'SUBMIT')]) for name in ['a','b']]
    report=capital.evaluate(pool(),journals)
    assert report['reservation_cap_breached'] and report['reserved_quote'].startswith('808.')
    assert report['available_quote_cash'].startswith('1692.')
    assert not report['transfers_supported'] and not report['exclusive_pool_membership_supported']


def test_cancel_ack_does_not_release_pool_holds_without_local_seal():
    seq=[lambda req,n:event(req,n,'SUBMIT'),lambda req,n:event(req,n,'CANCEL_REQUEST'),lambda req,n:event(req,n,'CANCEL_ACK')]
    report=capital.evaluate(pool(),[evidence('a',seq),evidence('b')])
    assert report['reserved_quote'].startswith('404.')


def test_sell_shared_inventory_is_explicitly_unsupported():
    from tests.test_requested_execution import cumulative
    seq=[lambda req,n:event(req,n,'SUBMIT'),lambda req,n:fill(req,n,size='4',fee='3.60'),lambda req,n:cumulative(req,n,size='4',fees='3.60',notional='360',seal=True)]
    candidate=proposal('1');candidate['side']='SELL';candidate['created_at']='2026-01-01T00:00:03+00:00';candidate['client_request_id']='sell-new'
    report=capital.evaluate(pool(),[evidence('a'),evidence('b',seq)],candidate)
    assert not report['forecast']['capital_forecast_allowed']
    assert report['forecast']['reason']=='SELL_SHARED_INVENTORY_NOT_IMPLEMENTED'
    assert capital.verify(report)==report


@pytest.mark.parametrize('active',[False,True])
def test_candidate_account_active_or_unknown_identity_denied(active):
    seq=[lambda req,n:event(req,n,'SUBMIT')]
    journals=[evidence('a'),evidence('b',seq)]
    candidate=proposal()
    if not active:
        candidate['account_id']='missing'
    with pytest.raises(ValueError):capital.evaluate(pool(),journals,candidate)


def test_input_and_output_byte_limits(monkeypatch):
    from core.paper.fault_adapter import encoded
    journals=[evidence('a'),evidence('b')];definition=pool();candidate=proposal()
    size=len(encoded({'pool':definition,'journals':journals,'candidate':candidate}).encode())
    monkeypatch.setattr(execution,'MAX_BYTES',size-1)
    with pytest.raises(ValueError,match='input exceeds'):capital.evaluate(definition,journals,candidate)
    monkeypatch.setattr(execution,'MAX_BYTES',size+1)
    with pytest.raises(ValueError,match='report exceeds'):capital.evaluate(definition,journals,candidate)


def test_offline_cli_and_duplicate_keys_without_database(tmp_path,monkeypatch):
    import json,subprocess,sys
    report=capital.evaluate(pool(),[evidence('a'),evidence('b')],proposal())
    path=tmp_path/'capital.json';path.write_text(json.dumps(report))
    monkeypatch.setenv('DATABASE_URL','postgresql+psycopg://invalid:invalid@127.0.0.1:1/no_db')
    result=subprocess.run([sys.executable,'-m','scripts.verify_shared_capital',str(path)],capture_output=True,text=True,timeout=10)
    assert result.returncode==0,result.stderr
    path.write_text('{"version":"a","version":"b"}')
    assert subprocess.run([sys.executable,'-m','scripts.verify_shared_capital',str(path)],capture_output=True,timeout=10).returncode!=0


@pytest.mark.parametrize('maximum,allowed',[('605.999999999999999999999999999999999999',False),('606',True),('606.000000000000000000000000000000000001',True)])
def test_exact_decimal_pool_cap_boundary(maximum,allowed):
    report=capital.evaluate(pool(maximum=maximum),[evidence('a',[lambda req,n:event(req,n,'SUBMIT')]),evidence('b')],proposal())
    assert report['forecast']['capital_forecast_allowed'] is allowed


def test_resealed_account_cash_corruption_is_not_capital_evidence():
    report=evidence('a');report['journal']['account']['cash']='2000';report['sha256']=sha({k:v for k,v in report.items() if k!='sha256'})
    with pytest.raises(ValueError):capital.evaluate(pool(),[report,evidence('b')])
