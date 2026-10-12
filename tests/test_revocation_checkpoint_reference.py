from copy import deepcopy
import json,os,subprocess,sys
import pytest
from sqlalchemy import text
from core.paper import revocation_checkpoint as checkpoint,segmented_revocations as revocations
from core.paper.fault_adapter import sha
from scripts.operator_revocation_checkpoint import read,write
from tests.test_integration import database
from tests.test_segmented_operator_api import TOKEN,ID,READ


def empty():
    body=dict(version=checkpoint.VERSION,scope=checkpoint.SCOPE,revision=0,root_sha256=None,head_sha256=None,
              consistency_only=True,external_submission_allowed=False)
    return {**body,'sha256':sha(body)}


@pytest.mark.parametrize('change',[
    {'revision':True},{'revision':-1},{'revision':1025},{'root_sha256':'0'*64},
    {'head_sha256':'0'*64},{'external_submission_allowed':True},{'consistency_only':False},
    {'scope':'LATEST_AUTHENTICATED_GRANT'},{'unexpected':'extra'},
    {'revision':1,'root_sha256':'0'*64,'head_sha256':'1'*64},
])
def test_resealed_invalid_reference_is_rejected(change):
    value={**empty(),**change};value['sha256']=sha({key:item for key,item in value.items() if key!='sha256'})
    with pytest.raises(ValueError):checkpoint.verify(value)


def test_reference_file_is_private_exclusive_and_symlink_safe(tmp_path):
    path=tmp_path/'reference.json';value=empty();write(path,value)
    assert path.stat().st_mode&0o777==0o600 and read(path)==value
    with pytest.raises(FileExistsError):write(path,value)
    link=tmp_path/'link';link.symlink_to(path)
    with pytest.raises(OSError):write(link,value)
    with pytest.raises(OSError):read(link)
    assert path.read_text()==json.dumps(value,sort_keys=True,separators=(',',':'))


@pytest.mark.parametrize('raw',['{"version":1,"version":2}','{"revision":NaN}','x'*4097])
def test_malformed_duplicate_nonfinite_and_oversize_files_fail(tmp_path,raw):
    path=tmp_path/'reference.json';path.write_text(raw)
    with pytest.raises(ValueError):read(path)


def test_fifo_is_rejected_without_waiting_for_a_writer(tmp_path):
    path=tmp_path/'fifo';os.mkfifo(path)
    with pytest.raises(ValueError,match='Regular'):read(path)


def test_capture_and_comparison_are_read_only_and_reject_stale_expected_state(database):
    engine,_,_=database;assert checkpoint.capture(engine)==empty()
    revocations.revoke(engine,TOKEN,'first')
    selected=checkpoint.capture(engine)
    result=checkpoint.check(engine,selected)
    assert result['matches_selected_checkpoint'] and not result['currentness_independently_verified']
    revocations.revoke(engine,TOKEN,'second',ledger_id=ID,action=READ)
    with pytest.raises(ValueError,match='selected trusted'):checkpoint.check(engine,selected)
    actual=checkpoint.capture(engine);assert actual['revision']==2
    assert checkpoint.check(engine,actual)['revision']==2
    with engine.connect() as db:assert db.scalar(text('SELECT count(*) FROM segmented_operator_revocations'))==2


def test_cli_capture_and_check_do_not_emit_credentials(database,tmp_path):
    engine,_,_=database;revocations.revoke(engine,TOKEN,'first')
    path=tmp_path/'reference.json';base=[sys.executable,'-m','scripts.operator_revocation_checkpoint']
    capture=subprocess.run([*base,'capture','--output',str(path)],capture_output=True,text=True,timeout=10)
    assert capture.returncode==0 and json.loads(capture.stdout)['revision']==1
    checked=subprocess.run([*base,'check','--expected',str(path)],capture_output=True,text=True,timeout=10)
    assert checked.returncode==0 and json.loads(checked.stdout)['matches_selected_checkpoint']
    assert TOKEN not in path.read_text()+capture.stdout+capture.stderr+checked.stdout+checked.stderr
    revocations.revoke(engine,TOKEN,'second',ledger_id=ID,action=READ)
    failed=subprocess.run([*base,'check','--expected',str(path)],capture_output=True,text=True,timeout=10)
    assert failed.returncode==1 and TOKEN not in failed.stderr and not failed.stdout


def test_complete_older_restoration_is_rejected_by_newer_selected_checkpoint(database,tmp_path):
    from scripts.backup_drill import drill
    engine,_,_=database;revocations.revoke(engine,TOKEN,'first');old=checkpoint.capture(engine)
    def verify(restored):
        assert checkpoint.check(restored,old)['matches_selected_checkpoint']
        # This source is an isolated test DB, never the product main database.
        revocations.revoke(engine,TOKEN,'second',ledger_id=ID,action=READ)
        newer=checkpoint.capture(engine)
        assert newer['revision']==2 and checkpoint.capture(restored)['revision']==1
        with pytest.raises(ValueError,match='selected trusted'):checkpoint.check(restored,newer)
        return {'older_complete_restore_rejected':True,'selected_revision':2,'restored_revision':1}
    result=drill(tmp_path/'restore',verify_restored=verify,expected_revocation_checkpoint=old)
    assert result['version']=='fc666-local-backup-drill-v2'
    assert result['selected_revocation_checkpoint_verification']['selected_checkpoint_sha256']==old['sha256']
    assert result['additional_verification']['older_complete_restore_rejected']
    assert result['selected_revocation_checkpoint_verification']['revision']==1
    assert not result['selected_revocation_checkpoint_verification']['currentness_independently_verified']


def test_restore_checkpoint_mismatch_fails_and_cleans_generated_target(database,tmp_path):
    from scripts.backup_drill import drill
    engine,_,_=database;old=checkpoint.capture(engine);revocations.revoke(engine,TOKEN,'first')
    with pytest.raises(ValueError,match='selected trusted'):drill(tmp_path/'mismatch',expected_revocation_checkpoint=old)
    targets=[path.stem for path in (tmp_path/'mismatch').glob('*.dump')]
    assert len(targets)==1
    with engine.connect() as db:
        for target in targets:
            assert db.scalar(text('SELECT count(*) FROM pg_database WHERE datname=:target'),{'target':target})==0
        assert db.scalar(text('SELECT count(*) FROM segmented_operator_revocations'))==1
    assert not list((tmp_path/'mismatch').glob('*.json'))


def test_invalid_expected_reference_fails_before_restore_directory_creation(database,tmp_path):
    from scripts.backup_drill import drill
    value=empty();value['sha256']='0'*64
    path=tmp_path/'not-created'
    with pytest.raises(ValueError):drill(path,expected_revocation_checkpoint=value)
    assert not path.exists()
