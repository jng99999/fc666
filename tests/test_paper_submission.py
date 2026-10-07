from concurrent.futures import ThreadPoolExecutor
import json
import signal
import sqlite3
import subprocess
import sys
import time

import pytest
from core.paper import submission
from core.paper.fault_adapter import FaultAdapter
from core.paper.submission import SubmissionAdapter
from tests.test_paper_reconciliation import event, fill, receipt


@pytest.fixture
def adapter(tmp_path):
    lab = SubmissionAdapter(tmp_path/'ownership.sqlite3')
    lab.create('order','2')
    return lab


def test_claim_retry_and_unknown_submission_do_not_resubmit(adapter):
    lease = adapter.claim('order','first')
    assert adapter.claim('order','first') == lease
    assert adapter.recover('order','first',lease['token'])['action'] == 'NOT_STARTED'
    first = adapter.submit('order','first',lease['token'])
    assert adapter.submit('order','first',lease['token']) == first
    result = adapter.recover('order','first',lease['token'])
    assert result['action'] == 'WAIT_UNKNOWN_RESULT'
    assert not result['resubmission_allowed'] and not result['external_query_supported']
    adapter.deliver('order','first',lease['token'],[event(2,'ACK'),fill(3),receipt(4)])
    assert adapter.recover('order','first',lease['token'])['action'] == 'RECONCILE_EXISTING_SUBMISSION'
    assert adapter.inspect('order')['evidence']['summary']['quantity'] == '1'


def test_expired_owner_is_fenced_and_takeover_keeps_client_identity(adapter,monkeypatch):
    stamp = [1_000_000_000_000]
    monkeypatch.setattr(submission,'clock',lambda:stamp[0])
    old = adapter.claim('order','old',1)
    dispatched = adapter.submit('order','old',old['token'])
    stamp[0] += 1_000_000_000
    for operation in [lambda:adapter.submit('order','old',old['token']),lambda:adapter.deliver('order','old',old['token'],[event(2,'ACK')]),lambda:adapter.recover('order','old',old['token'])]:
        with pytest.raises(ValueError): operation()
    new = adapter.claim('order','new',1)
    assert new['token'] == old['token']+1
    assert adapter.submit('order','new',new['token']) == dispatched
    with pytest.raises(ValueError): adapter.deliver('order','old',old['token'],[event(2,'ACK')])
    adapter.deliver('order','new',new['token'],[event(2,'ACK')])


def test_concurrent_claim_has_one_owner(adapter):
    def claim(owner):
        try: return adapter.claim('order',owner)
        except ValueError: return None
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(claim,['a','b','c','d']))
    assert sum(item is not None for item in results)==1


def test_concurrent_lost_reply_retries_one_dispatch(adapter):
    lease = adapter.claim('order','owner')
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(lambda _:adapter.submit('order','owner',lease['token']),range(8)))
    assert results == [results[0]]*8
    with adapter.connection() as db:
        assert db.execute('SELECT count(*) FROM lab_dispatches').fetchone()[0]==1
        assert db.execute('SELECT count(*) FROM lab_events').fetchone()[0]==2


def test_owned_request_rejects_unfenced_public_append(adapter):
    lease = adapter.claim('order','owner')
    with pytest.raises(ValueError): FaultAdapter(adapter.path).append('order',[event(0,'SUBMIT')])
    with pytest.raises(ValueError): adapter.deliver('order','owner',lease['token'],[event(0,'SUBMIT')])
    adapter.submit('order','owner',lease['token'])
    with pytest.raises(ValueError): adapter.deliver('order','owner',lease['token'],[event(2,'SUBMIT')])


def test_legacy_transcript_never_gains_retrospective_ownership(adapter):
    FaultAdapter(adapter.path).append('order',[event(0,'SUBMIT')])
    with pytest.raises(ValueError): adapter.claim('order','owner')
    with pytest.raises(ValueError): adapter.inspect('order')


@pytest.mark.parametrize('owner,ttl',[('',30),('owner',0),('owner',61),('owner',True)])
def test_invalid_lease(adapter,owner,ttl):
    with pytest.raises(ValueError): adapter.claim('order',owner,ttl)


def test_expiration_during_submission_rolls_back(adapter,monkeypatch):
    monkeypatch.setattr(submission,'clock',lambda:1_000_000_000)
    lease = adapter.claim('order','owner',1)
    values = iter([1_000_000_001,1_000_000_002,2_000_000_000])
    monkeypatch.setattr(submission,'clock',lambda:next(values))
    with pytest.raises(ValueError): adapter.submit('order','owner',lease['token'])
    assert adapter.inspect('order')['events']==[]
    assert adapter.inspect('order')['dispatch'] is None


@pytest.mark.parametrize('action',['UPDATE','DELETE'])
def test_dispatch_immutable(adapter,action):
    lease = adapter.claim('order','owner')
    adapter.submit('order','owner',lease['token'])
    with adapter.connection() as db:
        with pytest.raises(sqlite3.IntegrityError):
            db.execute('DELETE FROM lab_dispatches' if action=='DELETE' else 'UPDATE lab_dispatches SET digest=digest')


def test_missing_dispatch_fails_closed(adapter):
    lease = adapter.claim('order','owner')
    adapter.submit('order','owner',lease['token'])
    with adapter.connection() as db:
        db.execute('DROP TRIGGER lab_dispatches_delete')
        db.execute('DELETE FROM lab_dispatches')
    for operation in [lambda:adapter.inspect('order'),lambda:adapter.submit('order','owner',lease['token']),lambda:adapter.recover('order','owner',lease['token'])]:
        with pytest.raises(ValueError): operation()


@pytest.mark.parametrize('before',[True,False])
def test_actual_process_kill_submission_boundary(adapter,before):
    lease = adapter.claim('order','owner')
    code = '''
import os,signal,sys
from core.paper.submission import SubmissionAdapter
lab=SubmissionAdapter(sys.argv[1])
if sys.argv[3]=='before':
    original=lab._append
    def crash(*args):
        original(*args)
        os.kill(os.getpid(),signal.SIGKILL)
    lab._append=crash
lab.submit('order','owner',int(sys.argv[2]))
os.kill(os.getpid(),signal.SIGKILL)
'''
    result = subprocess.run([sys.executable,'-c',code,str(adapter.path),str(lease['token']),'before' if before else 'after'],capture_output=True,timeout=20)
    assert result.returncode == -signal.SIGKILL, result.stderr.decode()
    restored = SubmissionAdapter(adapter.path)
    first = restored.inspect('order')
    assert (first['dispatch'] is None) == before
    restored.submit('order','owner',lease['token'])
    restored.submit('order','owner',lease['token'])
    assert len(restored.inspect('order')['events'])==2
    if not before: assert restored.inspect('order')==first


def test_expiration_during_delivery_rolls_back(adapter,monkeypatch):
    monkeypatch.setattr(submission,'clock',lambda:1_000_000_000)
    lease = adapter.claim('order','owner',1)
    adapter.submit('order','owner',lease['token'])
    before = adapter.inspect('order')
    values = iter([1_000_000_001,2_000_000_000])
    monkeypatch.setattr(submission,'clock',lambda:next(values))
    with pytest.raises(ValueError): adapter.deliver('order','owner',lease['token'],[event(2,'ACK')])
    assert adapter.inspect('order')==before


def test_lease_cannot_be_deleted_or_rewind_token(adapter):
    adapter.claim('order','owner')
    with adapter.connection() as db:
        for sql in ['DELETE FROM lab_leases','UPDATE lab_leases SET token=token','UPDATE lab_leases SET token=0']:
            with pytest.raises(sqlite3.IntegrityError): db.execute(sql)


def test_corrupt_dispatch_rejects_query_and_takeover(adapter,monkeypatch):
    lease=adapter.claim('order','owner',1)
    adapter.submit('order','owner',lease['token'])
    with adapter.connection() as db:
        db.execute('DROP TRIGGER lab_dispatches_update')
        db.execute("UPDATE lab_dispatches SET digest='corrupt'")
    with pytest.raises(ValueError): adapter.recover('order','owner',lease['token'])
    monkeypatch.setattr(submission,'clock',lambda:lease['expires_ns'])
    with pytest.raises(ValueError): adapter.claim('order','next')


def test_missing_lease_does_not_enable_unfenced_delivery(adapter):
    lease=adapter.claim('order','owner')
    adapter.submit('order','owner',lease['token'])
    with adapter.connection() as db:
        db.execute('DROP TRIGGER lab_leases_delete')
        db.execute('DELETE FROM lab_leases')
    with pytest.raises(ValueError): adapter.inspect('order')
    with pytest.raises(ValueError): FaultAdapter(adapter.path).append('order',[event(2,'ACK')])
