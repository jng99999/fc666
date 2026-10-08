from copy import deepcopy
import json, subprocess, sys
import pytest
from core.paper import requested_transport_boundary as boundary, requested_dispatch_query as query, requested_dispatch as dispatch, requested_sources as sources
from core.paper.fault_adapter import sha
from tests.test_integration import database
from tests.test_requested_dispatch import submitted
from tests.test_requested_sources import reviewed
from tests.test_requested_ownership import deliver
from tests.test_requested_execution import event, fill


def test_failure_matrix_preserves_unknown_partial_holds_and_original_identity(database,tmp_path):
    engine,_,_=database;req,_,_,_=submitted(engine)
    client_id=dispatch.capture(engine,'account')['dispatches'][0]['dispatch']['client_id']
    for seq,ev in enumerate([None,event(req,1,'UNKNOWN_SUBMISSION'),event(req,2,'ACK'),fill(req,3)]):
        if ev is not None:
            value,_=reviewed(engine,req,ev);deliver(engine,value,'first',1)
        evidence=query.capture(engine,'account',req['request_id'],client_id,'first',1)
        before=sources.capture(engine,'account')
        for failure in boundary.FAILURES:
            report=boundary.evaluate(evidence,failure)
            assert boundary.verify(report)==report
            assert report['client_id']==client_id
            assert report['funding']==evidence['selected']['funding']
            assert not any(report[key] for key in ['resubmission_allowed','release_funding_allowed','remote_absence_proven','observation_committed','transport_available'])
            for field in ['funding','resubmission_allowed','remote_absence_proven']:
                forged=deepcopy(report);forged[field]={} if field=='funding' else True
                forged['sha256']=sha({k:v for k,v in forged.items() if k!='sha256'})
                with pytest.raises(ValueError):boundary.verify(forged)
        assert sources.capture(engine,'account')==before
    assert report['funding']['reserved_cash'].startswith('303.')
    for invalid in ['SUCCESS','NOT_FOUND',True,None,{},'']:
        with pytest.raises(ValueError):boundary.evaluate(evidence,invalid)
    path=tmp_path/'boundary.json';path.write_text(json.dumps(report))
    result=subprocess.run([sys.executable,'-m','scripts.verify_requested_transport_boundary',str(path)],capture_output=True,text=True,timeout=10)
    assert result.returncode==0,result.stderr
    path.write_text('{"version":"a","version":"b"}')
    assert subprocess.run([sys.executable,'-m','scripts.verify_requested_transport_boundary',str(path)],capture_output=True,timeout=10).returncode!=0
