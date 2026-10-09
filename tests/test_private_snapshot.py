from copy import deepcopy
import json,os,subprocess,sys
import httpx,pytest
from pydantic import SecretStr
from core.exchange.private_spot import Credentials,ReadOnlySpot,PrivateQueryError
from core.exchange import private_snapshot as snap
from core.paper.fault_adapter import sha

KEY='FixtureAPIKey123456';SECRET='FixtureSecret123456'


def client(balance='100'):
    def serve(request):
        assert request.method=='GET' and request.url.host=='api.binance.com'
        assert request.headers['X-MBX-APIKEY']==KEY
        if request.url.path.endswith('/account'):return httpx.Response(200,json={'accountType':'SPOT','balances':[{'asset':'USDT','free':balance,'locked':'0'}]})
        return httpx.Response(200,json=[])
    return ReadOnlySpot(Credentials(SecretStr(KEY),SecretStr(SECRET)),transport=httpx.MockTransport(serve),clock_ms=lambda:1000)


def test_capture_and_difference_replay_no_authority(tmp_path):
    before=snap.capture(client(),'fixture','BTCUSDT');after=snap.capture(client('90'),'fixture','BTCUSDT')
    assert snap.verify(before)==before
    report=snap.compare(before,after);assert len(report['differences'])==1 and report['verdict']=='DIFFERENCES_FOUND'
    assert not report['identity_verified'] and not report['reconciled'] and not report['submission_allowed']
    assert KEY not in json.dumps(report) and SECRET not in json.dumps(report)
    same=snap.capture(client(),'fixture','BTCUSDT');assert snap.compare(before,same)['verdict']=='NO_DIFFERENCES_IN_OBSERVED_SCOPE'
    one=tmp_path/'before.json';two=tmp_path/'after.json';output=tmp_path/'diff.json'
    snap.write(one,before);snap.write(two,after)
    result=subprocess.run([sys.executable,'-m','scripts.private_snapshot','compare','--before',str(one),'--after',str(two),'--output',str(output)],capture_output=True,text=True)
    assert result.returncode==0 and snap.read(output)==report and output.stat().st_mode&0o777==0o600
    with pytest.raises(FileExistsError):snap.write(one,after)
    assert snap.read(one)==before


@pytest.mark.parametrize('change',[{'atomic_snapshot':True},{'identity_verified':True},{'from_id':True},{'scope':'invalid/scope'},{'submission_allowed':True}])
def test_rehashed_false_claims_rejected(change):
    value=snap.capture(client(),'fixture','BTCUSDT');value.update(change);value['sha256']=sha({k:v for k,v in value.items() if k!='sha256'})
    with pytest.raises(ValueError):snap.verify(value)


def test_credential_file_owner_permissions_symlink_duplicates(tmp_path):
    path=tmp_path/'keys.json';path.write_text(json.dumps(dict(api_key=KEY,api_secret=SECRET)));path.chmod(0o600)
    assert repr(snap.credentials_file(path))=='Credentials(<redacted>)'
    path.chmod(0o644)
    with pytest.raises(PrivateQueryError):snap.credentials_file(path)
    path.chmod(0o600);link=tmp_path/'link';link.symlink_to(path)
    with pytest.raises(PrivateQueryError):snap.credentials_file(link)
    path.write_text('{"api_key":"a","api_key":"b","api_secret":"c"}')
    with pytest.raises(PrivateQueryError):snap.credentials_file(path)
    fifo=tmp_path/'fifo';os.mkfifo(fifo,0o600)
    with pytest.raises(PrivateQueryError):snap.credentials_file(fifo)


def test_identity_clock_and_data_bounds_fail_closed(tmp_path,monkeypatch):
    before=snap.capture(client(),'fixture','BTCUSDT');after=snap.capture(client(),'other','BTCUSDT')
    with pytest.raises(ValueError):snap.compare(before,after)
    with pytest.raises(ValueError):snap.compare(before,before)
    altered=deepcopy(before);altered['account']['balances'][0]['free']='-1';altered['sha256']=sha({k:v for k,v in altered.items() if k!='sha256'})
    with pytest.raises(ValueError):snap.verify(altered)
    path=tmp_path/'duplicate.json';path.write_text('{"version":1,"version":2}')
    with pytest.raises(ValueError):snap.read(path)
    monkeypatch.setattr(snap,'MAX_BYTES',64)
    with pytest.raises(ValueError):snap.verify(before)


def test_cli_failure_sanitizes_paths_and_secrets(tmp_path):
    keys=tmp_path/(KEY+'.json');keys.write_text(SECRET);keys.chmod(0o600)
    out=tmp_path/'out.json'
    run=subprocess.run([sys.executable,'-m','scripts.private_snapshot','capture','--credentials-file',str(keys),'--scope','fixture','--symbol','BTCUSDT','--output',str(out)],capture_output=True,text=True)
    assert run.returncode==1 and not out.exists()
    assert KEY not in run.stderr and SECRET not in run.stderr and 'Traceback' not in run.stderr
