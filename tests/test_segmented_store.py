from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal
import json
import os
import subprocess
import sys
import pytest
from core.paper import segmented_store as store
from core.paper.fault_adapter import encoded
from tests.test_requested_execution import request, event, fill


def test_store_append_reopen_retry_and_protected_files(tmp_path):
    path = tmp_path / 'store'; req = request()
    root = store.create(path, req)
    batch = [event(req,0,'SUBMIT'),fill(req,1)]
    head = store.append(path, batch, expected_node_sha256=root['node_sha256'])
    assert store.load(path)['index'] == head
    assert store.append(path, batch, expected_node_sha256=root['node_sha256']) == head
    result = store.load(path)['report']
    assert result['summary']['funding']['reserved_cash'].startswith('303')
    assert result['ledger_application_supported'] is False
    assert os.stat(path).st_mode & 0o777 == 0o700
    assert all(os.stat(p).st_mode & 0o777 == 0o600 for p in path.iterdir())
    with pytest.raises(FileExistsError):
        store.create(path, req)


def test_concurrent_different_appends_have_one_accepted_checkpoint(tmp_path):
    path = tmp_path / 'store'; req = request()
    root = store.create(path, req)
    def append(identity):
        value = event(req,0,'SUBMIT'); value['event_id'] = identity
        try:
            return store.append(path,[value],expected_node_sha256=root['node_sha256'])
        except ValueError:
            return None
    with ThreadPoolExecutor(max_workers=2) as workers:
        results = list(workers.map(append,['one','two']))
    assert sum(r is not None for r in results) == 1
    assert store.load(path)['index']['revision'] == 1


@pytest.mark.parametrize('after', [False, True])
def test_sigkill_before_and_after_index_commit_has_exact_retry(tmp_path, after):
    path = tmp_path / 'store'; req = request()
    root = store.create(path, req)
    batch = [event(req,0,'SUBMIT')]
    config = tmp_path / 'config.json'
    config.write_text(json.dumps(dict(path=str(path),batch=batch,expected=root['node_sha256'],after=after)))
    code = '''import json,os,signal,sys
from core.paper import segmented_store as store
c=json.load(open(sys.argv[1]));original=store._publish
def crash(directory,head):
    if c['after']:original(directory,head)
    os.kill(os.getpid(),signal.SIGKILL)
store._publish=crash
store.append(c['path'],c['batch'],expected_node_sha256=c['expected'])
'''
    result = subprocess.run([sys.executable,'-c',code,str(config)],capture_output=True)
    assert result.returncode == -9
    assert store.load(path)['index']['revision'] == (1 if after else 0)
    head = store.append(path,batch,expected_node_sha256=root['node_sha256'])
    assert head['revision'] == 1
    assert len(store.load(path)['report']['segments']) == 1
    assert len(list(path.glob('*.json'))) == 2


def test_missing_historical_object_refuses_read_and_append(tmp_path):
    path = tmp_path / 'store'; req = request()
    root = store.create(path,req)
    head = store.append(path,[event(req,0,'SUBMIT')],expected_node_sha256=root['node_sha256'])
    (path / (root['node_sha256']+'.json')).unlink()
    with pytest.raises(FileNotFoundError):
        store.load(path)
    with pytest.raises(FileNotFoundError):
        store.append(path,[event(req,1,'ACK')],expected_node_sha256=head['node_sha256'])


def test_tampered_object_stale_checkpoint_and_storage_budget_fail_closed(tmp_path, monkeypatch):
    path = tmp_path / 'store'; req = request()
    root = store.create(path,req)
    head = store.append(path,[event(req,0,'SUBMIT')],expected_node_sha256=root['node_sha256'])
    with pytest.raises(ValueError,match='Stale'):
        store.append(path,[event(req,1,'ACK')],expected_node_sha256=root['node_sha256'])
    with monkeypatch.context() as bounded:
        bounded.setattr(store,'STORAGE_BYTES',1)
        with pytest.raises(ValueError,match='capacity'):
            store.append(path,[event(req,1,'ACK')],expected_node_sha256=head['node_sha256'])
    assert store.load(path)['index'] == head
    node_path = path / (head['node_sha256']+'.json')
    node = json.loads(node_path.read_text()); node['report']['summary']['account']['cash']='999'
    node_path.write_text(encoded(node))
    with pytest.raises(ValueError):
        store.load(path)


def test_symlink_store_and_index_and_open_permissions_rejected(tmp_path):
    path = tmp_path / 'store'; req = request()
    store.create(path,req)
    link = tmp_path / 'link'; link.symlink_to(path,target_is_directory=True)
    with pytest.raises(OSError):
        store.load(link)
    os.chmod(path,0o755)
    with pytest.raises(ValueError,match='directory'):
        store.load(path)
    os.chmod(path,0o700)
    target = tmp_path / 'target'; (path/'HEAD').rename(target); (path/'HEAD').symlink_to(target)
    with pytest.raises(ValueError):
        store.load(path)


def test_cli_persists_and_verifies_with_database_unavailable(tmp_path):
    path=tmp_path/'store'; req=request()
    request_file=tmp_path/'request.json';request_file.write_text(encoded(req))
    batch_file=tmp_path/'batch.json';batch_file.write_text(encoded([event(req,0,'SUBMIT')]))
    base=[sys.executable,'-m','scripts.segmented_store']
    env={**os.environ,'DATABASE_URL':'postgresql+psycopg://invalid:invalid@127.0.0.1:1/unavailable'}
    result=subprocess.run([*base,'init','--directory',str(path),'--request-file',str(request_file)],env=env,capture_output=True)
    assert result.returncode==0
    checkpoint=json.loads(result.stdout)['index']['node_sha256']
    result=subprocess.run([*base,'append','--directory',str(path),'--batch-file',str(batch_file),'--expected-node-sha256',checkpoint],env=env,capture_output=True)
    assert result.returncode==0 and json.loads(result.stdout)['index']['revision']==1
    result=subprocess.run([*base,'verify','--directory',str(path)],env=env,capture_output=True)
    assert result.returncode==0 and json.loads(result.stdout)['ledger_application_supported'] is False


def test_store_persists_more_than_1000_events_without_financial_authority(tmp_path):
    from tests.test_requested_segmented_execution import long_history
    req,batches=long_history();path=tmp_path/'store'
    head=store.create(path,req)
    for batch in batches:
        head=store.append(path,batch,expected_node_sha256=head['node_sha256'])
    result=store.load(path)
    assert result['index']['revision']==len(batches)
    assert result['report']['total_events']==1003
    assert Decimal(result['report']['summary']['account']['cash'])==Decimal('636.4')
    assert result['report']['ledger_application_supported'] is False


def test_sigkill_during_object_write_preserves_index_and_retry(tmp_path):
    path=tmp_path/'store';req=request();root=store.create(path,req)
    config=tmp_path/'config.json';batch=[event(req,0,'SUBMIT')]
    config.write_text(json.dumps(dict(path=str(path),batch=batch,expected=root['node_sha256'])))
    code='''import json,os,signal,sys
from core.paper import segmented_store as store
c=json.load(open(sys.argv[1]));original=store._write
def crash(directory,name,value):
    if name.startswith('.object-'):
        fd=os.open(name,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600,dir_fd=directory)
        os.write(fd,b'{');os.fsync(fd)
        os.kill(os.getpid(),signal.SIGKILL)
    original(directory,name,value)
store._write=crash
store.append(c['path'],c['batch'],expected_node_sha256=c['expected'])
'''
    assert subprocess.run([sys.executable,'-c',code,str(config)],capture_output=True).returncode==-9
    assert store.load(path)['index']==root
    head=store.append(path,batch,expected_node_sha256=root['node_sha256'])
    assert store.load(path)['index']==head and head['revision']==1
