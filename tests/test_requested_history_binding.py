from copy import deepcopy
import pytest
from core.paper import requested_attempts as attempts,requested_dispatch as dispatch,requested_ownership as own,requested_sources as sources
from tests.test_integration import database
from tests.test_requested_dispatch import submitted
from tests.test_requested_attempts import command
from tests.test_requested_sources import reviewed
from tests.test_requested_ownership import deliver
from tests.test_requested_execution import event,fill
from tests.test_requested_controls import command as control


def test_old_assessment_binds_after_later_fills_controls_and_takeover(database,monkeypatch):
    engine,_,_=database;req,lease,_,_=submitted(engine);cmd=command(engine,req);receipt=attempts.record(engine,cmd)
    value,_=reviewed(engine,req,event(req,1,'UNKNOWN_SUBMISSION'));deliver(engine,value,'first',1)
    control(engine,'stop','STOP',2)
    value,_=reviewed(engine,req,fill(req,2));deliver(engine,value,'first',1)
    monkeypatch.setattr(own,'clock',lambda db:lease['expires_us'])
    own.claim(engine,'account',req['request_id'],'second',60)
    before=sources.capture(engine,'account');report=attempts.export(engine,'account',req['request_id'])
    assert attempts.verify_export(report)==report and report['attempts']==[receipt]
    original=receipt['assessment']['query_evidence']['selected']
    assert original['funding']['reserved_cash'].startswith('404.')
    assert report['dispatch_evidence']['recovery'][0]['funding']['reserved_cash'].startswith('303.')
    assert sources.capture(engine,'account')==before


def test_valid_alternative_control_history_cannot_be_spliced(database):
    engine,_,_=database;req,_,_,_=submitted(engine)
    control(engine,'pause','PAUSE',2)
    receipt=attempts.record(engine,command(engine,req))
    current=dispatch.capture(engine,'account');owned=current['ownership_evidence'];source=owned['source_evidence'];financial=source['journal']['journal']
    from core.paper import requested_controls as controls,requested_ownership as ownership
    from core.paper.fault_adapter import sha
    alternate=deepcopy(source);records=alternate['controls']['records'];records[-1]['command_id']='alternative-pause'
    alternate['controls']=controls.replay(controls.VERSION,alternate['controls']['policy'],records,alternate['controls']['gates'],financial)
    alternate['sha256']=sha({k:v for k,v in alternate.items() if k!='sha256'})
    changed_owned=ownership.evaluate(alternate,owned['claims'],owned['event_tokens'],owned['observed_us'])
    changed=dispatch.evaluate(changed_owned,current['dispatches'])
    assert dispatch.verify(changed)==changed
    assert changed['dispatches']==current['dispatches']
    with pytest.raises(ValueError):attempts.evaluate_export(changed,req['request_id'],[receipt])


def test_independently_valid_future_observation_is_not_a_historical_prefix(database):
    from core.paper import requested_dispatch_query as query,requested_transport_boundary as boundary
    from core.paper.fault_adapter import sha
    engine,_,_=database;req,lease,_,_=submitted(engine);receipt=attempts.record(engine,command(engine,req))
    current=dispatch.capture(engine,'account');owned=current['ownership_evidence']
    changed_owned=own.evaluate(owned['source_evidence'],owned['claims'],owned['event_tokens'],lease['expires_us']-1)
    changed=dispatch.evaluate(changed_owned,current['dispatches'])
    cmd=receipt['command'];q=query.evaluate(changed,req['request_id'],cmd['client_id'],'first',1)
    forged={**receipt,'assessment':boundary.evaluate(q,cmd['failure'])};forged['sha256']=sha({k:v for k,v in forged.items() if k!='sha256'})
    assert attempts.verify(forged)==forged
    with pytest.raises(ValueError):attempts.evaluate_export(current,req['request_id'],[forged])


def test_separately_trusted_receipt_reference_detects_missing_tail(database,tmp_path):
    import json,subprocess,sys
    from core.paper.fault_adapter import sha
    engine,_,_=database;req,_,_,_=submitted(engine);cmd=command(engine,req)
    attempts.record(engine,cmd);attempts.record(engine,{**cmd,'attempt_id':'second'})
    report=attempts.export(engine,'account',req['request_id']);reference=[value['sha256'] for value in report['attempts']]
    assert attempts.verify_export(report,expected_receipt_sha256=reference)==report
    truncated=deepcopy(report);truncated['attempts']=truncated['attempts'][:1];truncated['sha256']=sha({k:v for k,v in truncated.items() if k!='sha256'})
    assert attempts.verify_export(truncated)==truncated
    with pytest.raises(ValueError):attempts.verify_export(truncated,expected_receipt_sha256=reference)
    for invalid in [True,['g'*64],['0'],['0'*64]*17]:
        with pytest.raises(ValueError):attempts.verify_export(report,expected_receipt_sha256=invalid)
    path=tmp_path/'export.json';path.write_text(json.dumps(report));trusted=tmp_path/'trusted.json';trusted.write_text(json.dumps(reference))
    result=subprocess.run([sys.executable,'-m','scripts.verify_requested_attempts_export',str(path),'--expected-receipts',str(trusted)],capture_output=True,text=True,timeout=10)
    assert result.returncode==0,result.stderr
    path.write_text(json.dumps(truncated))
    assert subprocess.run([sys.executable,'-m','scripts.verify_requested_attempts_export',str(path),'--expected-receipts',str(trusted)],capture_output=True,timeout=10).returncode!=0


def test_replay_valid_alternative_event_history_cannot_be_spliced(database):
    from core.paper import requested_inspection as inspection,requested_execution as execution
    from core.paper.fault_adapter import sha
    engine,_,_=database;req,_,_,_=submitted(engine)
    value,_=reviewed(engine,req,event(req,1,'UNKNOWN_SUBMISSION'));deliver(engine,value,'first',1)
    receipt=attempts.record(engine,command(engine,req));current=dispatch.capture(engine,'account');owned=current['ownership_evidence'];source=deepcopy(owned['source_evidence'])
    financial=source['journal']['journal'];history=deepcopy(financial['requests']);history[0]['events'][1]['event_id']='alternate-unknown-event'
    history[0]['summary']=execution.reduce(history[0]['request'],history[0]['events'])
    altered=inspection.replay(financial['opening'],history,version=financial['version']);source['journal']=inspection.seal(altered)
    slot=source['sources'][1];slot['event_id']='alternate-unknown-event'
    forecast=sources.reconstruct(altered,source['controls'],0,1,slot['receipt']['source']);slot['receipt']=sources.receipt(forecast)
    source['sha256']=sha({k:v for k,v in source.items() if k!='sha256'})
    new_owned=own.evaluate(source,owned['claims'],owned['event_tokens'],owned['observed_us'])
    changed=dispatch.evaluate(new_owned,current['dispatches'])
    assert dispatch.verify(changed)==changed and changed['dispatches']==current['dispatches']
    with pytest.raises(ValueError):attempts.evaluate_export(changed,req['request_id'],[receipt])
