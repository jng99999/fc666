from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
import json
import os
import signal
import sqlite3
import subprocess
import sys

import pytest
from core.paper.fault_adapter import FaultAdapter
from tests.test_paper_reconciliation import event, fill, receipt


@pytest.fixture
def lab(tmp_path):
    adapter = FaultAdapter(tmp_path/'faults.sqlite3')
    adapter.create('order','2')
    return adapter


def test_request_durable_before_any_events_and_stable_retry(lab):
    assert lab.inspect('order')['events'] == []
    reopened = FaultAdapter(lab.path)
    assert reopened.create('order','2') == lab.inspect('order')['request']
    assert lab.path.stat().st_mode & 0o777 == 0o600
    with pytest.raises(ValueError): reopened.create('order','3')
    with pytest.raises(ValueError): reopened.inspect('missing')


def test_reordered_duplicate_and_late_fill_evidence_survives_reopen(lab):
    events = [event(0,'SUBMIT'),event(1,'CANCEL_REQUEST'),event(2,'CANCEL_ACK')]
    lab.append('order',list(reversed(events))+events)
    before = lab.inspect('order')
    lab.append('order',[fill(3),receipt(4)])
    result = FaultAdapter(lab.path).inspect('order')
    assert result['events'][:3] == before['events']
    assert result['evidence']['summary']['state'] == 'PARTIAL_CANCELLED'
    assert result['evidence']['summary']['receipt_confirmed']
    assert lab.append('order',[receipt(4),fill(3)]) == result['evidence']['summary']
    with lab.connection() as db:
        assert db.execute('SELECT count(*) FROM lab_events').fetchone()[0] == 5
        assert db.execute('SELECT count(*) FROM lab_evidence').fetchone()[0] == 2


def test_conflict_gap_and_overfill_roll_back(lab):
    lab.append('order',[event(0,'SUBMIT')])
    before = lab.inspect('order')
    for batch in [[fill(2)], [fill(1,'3')], [event(1,'CANCEL_ACK')]]:
        with pytest.raises(ValueError): lab.append('order',batch)
        assert lab.inspect('order') == before
    bad = event(0,'ACK')
    with pytest.raises(ValueError): lab.append('order',[bad])


def test_concurrent_redelivery_has_one_effect(lab):
    events = [event(0,'SUBMIT'),fill(1),receipt(2)]
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(lambda _: lab.append('order',events),range(8)))
    assert results == [results[0]]*8
    with lab.connection() as db:
        assert db.execute('SELECT count(*) FROM lab_evidence').fetchone()[0] == 1
    assert lab.inspect('order')['evidence']['summary']['unique_fills'] == 1


@pytest.mark.parametrize('table',['lab_requests','lab_events','lab_evidence'])
@pytest.mark.parametrize('action',['UPDATE','DELETE'])
def test_immutable_storage(lab,table,action):
    lab.append('order',[event(0,'SUBMIT')])
    sql = f'DELETE FROM {table}' if action == 'DELETE' else f'UPDATE {table} SET digest=digest'
    with lab.connection() as db:
        with pytest.raises(sqlite3.IntegrityError): db.execute(sql)


def child(lab, before_commit):
    code = '''
import os,signal,sys,json
from core.paper.fault_adapter import FaultAdapter
lab = FaultAdapter(sys.argv[1])
if sys.argv[2] == 'before':
    original = lab._evidence
    def crash(request, events, previous_count=0):
        if len(events) == 3:
            os.kill(os.getpid(),signal.SIGKILL)
        return original(request,events,previous_count)
    lab._evidence = crash
lab.append('order',json.loads(sys.argv[3]))
os.kill(os.getpid(),signal.SIGKILL)
'''
    result = subprocess.run([sys.executable,'-c',code,str(lab.path),'before' if before_commit else 'after',json.dumps([event(0,'SUBMIT'),fill(1),receipt(2)])],capture_output=True,timeout=20)
    assert result.returncode == -signal.SIGKILL, result.stderr.decode()


def test_actual_process_kill_before_commit_rolls_back(lab):
    child(lab,True)
    restored = FaultAdapter(lab.path)
    assert restored.inspect('order')['events'] == []
    restored.append('order',[event(0,'SUBMIT'),fill(1),receipt(2)])
    assert restored.inspect('order')['evidence']['summary']['unique_fills'] == 1


def test_actual_process_kill_after_commit_lost_reply_retries_once(lab):
    child(lab,False)
    restored = FaultAdapter(lab.path)
    before = restored.inspect('order')
    restored.append('order',[event(0,'SUBMIT'),fill(1),receipt(2)])
    assert restored.inspect('order') == before
    assert before['evidence']['summary']['quantity'] == '1'


def test_corruption_and_missing_history_fail_closed(lab):
    lab.append('order',[event(0,'SUBMIT')])
    lab.append('order',[fill(1),receipt(2)])
    with lab.connection() as db:
        db.execute('DROP TRIGGER lab_evidence_delete')
        db.execute('DELETE FROM lab_evidence WHERE event_count=1')
    with pytest.raises(ValueError): lab.inspect('order')
    with pytest.raises(ValueError): lab.append('order',[event(3,'ACK')])


def test_unrelated_database_not_adopted(tmp_path):
    path = tmp_path/'other.sqlite3'
    with sqlite3.connect(path) as db: db.execute('CREATE TABLE accounts (id INTEGER)')
    with pytest.raises(ValueError): FaultAdapter(path)


def test_full_capacity_transcript_redelivery_is_idempotent(lab):
    events = [event(0,'SUBMIT')]+[event(index,'ACK') for index in range(1,1000)]
    lab.append('order',events)
    assert lab.append('order',events)['state'] == 'ACKNOWLEDGED'
    before = lab.inspect('order')
    with pytest.raises(ValueError): lab.append('order',[event(1000,'ACK')])
    assert lab.inspect('order') == before


@pytest.mark.parametrize('table',['lab_requests','lab_events','lab_evidence'])
def test_content_corruption_is_rejected(lab,table):
    lab.append('order',[event(0,'SUBMIT')])
    with lab.connection() as db:
        db.execute(f'DROP TRIGGER {table}_update')
        db.execute(f"UPDATE {table} SET digest='invalid'")
    with pytest.raises(ValueError): lab.inspect('order')
    with pytest.raises(ValueError): lab.append('order',[event(1,'ACK')])
