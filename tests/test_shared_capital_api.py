from copy import deepcopy
import json,subprocess,sys
import pytest
from pydantic import ValidationError
from fastapi.testclient import TestClient
from apps.api.settings import Settings
from apps.api.main import create_app
from core.paper import shared_capital_pool as pools,shared_capital_admission as admission,requested_journal as journal
from core.paper.fault_adapter import sha
from tests.test_integration import database
from tests.test_shared_capital_admission import ready,proposed
from tests.test_shared_capital import pool
from tests.test_requested_commands import TOKEN,HEADERS

PREVIEW='/api/v1/paper-requested/pool-preparation-previews'
PREPARE='/api/v1/paper-requested/pool-preparation-commands'
READ='/api/v1/paper-requested/pool-captures'
ACTIONS=['PREVIEW_POOL_PREPARE','POOL_PREPARE','READ_POOL']


def configured(settings,actions=ACTIONS,accounts=None,pool_ids=None):
    return Settings(database_url=settings.database_url,paper_operator_token=TOKEN,paper_operator_accounts=accounts or ['a','b'],paper_operator_actions=actions,paper_operator_pool_ids=pool_ids or ['pool'])


def test_pool_grants_default_disabled_independent_and_before_storage(monkeypatch):
    from tests.test_shared_capital import proposal
    settings=Settings(database_url='postgresql+psycopg://invalid:invalid@127.0.0.1:1/no_db')
    value={**proposal(),'pool_id':'pool','expected_financial_revision':0,'expected_control_revision':2};values=[(PREVIEW,value),(PREPARE,{**value,'local_preview_sha256':'0'*64}),(READ,{'pool_id':'pool'})]
    def forbidden(*args,**kwargs):raise AssertionError('Denied request accessed financial storage')
    monkeypatch.setattr(admission,'capture_preview',forbidden);monkeypatch.setattr(admission,'prepare',forbidden);monkeypatch.setattr(pools,'capture',forbidden)
    with TestClient(create_app(settings)) as client:
        for path,body in values:assert client.post(path,json=body,headers=HEADERS).status_code==503
    for index,action in enumerate(ACTIONS):
        with TestClient(create_app(configured(settings,actions=[action]))) as client:
            for other,(path,body) in enumerate(values):
                assert client.post(path,json=body).status_code==401
                if other!=index:assert client.post(path,json=body,headers=HEADERS).status_code==403
                assert client.post(path,json={**body,'pool_id':'other'},headers=HEADERS).status_code==403
    with TestClient(create_app(configured(settings,accounts=['a']))) as client:
        for path,body in values[:2]:assert client.post(path,json=body,headers=HEADERS).status_code==403


def test_settings_require_explicit_pool_scope_and_preserve_legacy():
    base=dict(database_url='postgresql+psycopg://invalid:invalid@127.0.0.1:1/no_db',paper_operator_token=TOKEN,paper_operator_accounts=['a'],paper_operator_actions=['POOL_PREPARE'])
    for update in [{},{'paper_operator_pool_ids':['pool','pool']},{'paper_operator_pool_ids':['']},{'paper_operator_pool_ids':['x'*129]}]:
        with pytest.raises(ValidationError):Settings(**{**base,**update})
    assert Settings(**{**base,'paper_operator_actions':['PREPARE']}).paper_operator_pool_ids==[]


def test_whole_member_scope_denied_before_account_locks_or_journal_reads(database,monkeypatch):
    engine,_,settings=database;ready(engine);a,digest=proposed(engine,'a')
    def forbidden(*args,**kwargs):raise AssertionError('Denied whole-pool scope reached account capture')
    monkeypatch.setattr(pools,'journals',forbidden)
    with TestClient(create_app(configured(settings,accounts=['a']))) as client:
        for path,value in [(PREVIEW,{**a,'pool_id':'pool'}),(PREPARE,{**a,'pool_id':'pool','local_preview_sha256':digest}),(READ,{'pool_id':'pool'})]:
            response=client.post(path,json=value,headers=HEADERS)
            assert response.status_code==403 and response.headers['cache-control']=='no-store'
            assert response.json()=={'detail':'Explicit Paper pool scope denied'}


def test_pool_preview_prepare_retry_cap_denial_and_offline_verifier(database,tmp_path):
    engine,_,settings=database;ready(engine);a,_=proposed(engine,'a');b,_=proposed(engine,'b')
    with TestClient(create_app(configured(settings))) as client:
        response=client.post(PREVIEW,json={**a,'pool_id':'pool'},headers=HEADERS);assert response.status_code==200,response.text
        forecast=admission.verify_preview(response.json());assert forecast['capital_forecast_allowed'] and not forecast['shared_reservation_committed']
        assert journal.read(engine,'a')['revision']==0
        cmd={**a,'pool_id':'pool','local_preview_sha256':forecast['local_preview']['sha256']}
        one=client.post(PREPARE,json=cmd,headers=HEADERS);assert one.status_code==200,one.text
        assert admission.verify(one.json())==one.json()
        assert client.post(PREPARE,json=cmd,headers=HEADERS).json()==one.json()
        denied=client.post(PREVIEW,json={**b,'pool_id':'pool'},headers=HEADERS);assert denied.status_code==200,denied.text
        report=admission.verify_preview(denied.json());assert not report['capital_forecast_allowed']
        assert client.post(PREPARE,json={**b,'pool_id':'pool','local_preview_sha256':report['local_preview']['sha256']},headers=HEADERS).status_code==409
        assert journal.read(engine,'b')['revision']==0
        read=client.post(READ,json={'pool_id':'pool'},headers=HEADERS);assert read.status_code==200 and read.headers['cache-control']=='no-store'
        assert pools.verify(read.json())['preview']['reserved_quote'].startswith('404.')
        assert client.post(PREVIEW,json={**b,'pool_id':'pool','expected_financial_revision':True},headers=HEADERS).status_code==422
        forged=deepcopy(forecast);forged['submission_allowed']=True;forged['sha256']=sha({k:v for k,v in forged.items() if k!='sha256'})
        with pytest.raises(ValueError):admission.verify_preview(forged)
    path=tmp_path/'preview.json';path.write_text(json.dumps(forecast))
    result=subprocess.run([sys.executable,'-m','scripts.verify_shared_capital_preview',str(path)],capture_output=True,text=True,timeout=10)
    assert result.returncode==0,result.stderr


def test_missing_pool_generic_not_found(database):
    engine,_,settings=database
    with TestClient(create_app(configured(settings))) as client:
        response=client.post(READ,json={'pool_id':'pool'},headers=HEADERS)
        assert response.status_code==404 and response.headers['cache-control']=='no-store'


def test_pool_api_lock_timeout_generic_no_partial_financial_disclosure(database):
    from sqlalchemy import text
    engine,_,settings=database;ready(engine);a,digest=proposed(engine,'a')
    with TestClient(create_app(configured(settings))) as client:
        with engine.begin() as db:
            db.execute(text("SELECT account_id FROM requested_paper_accounts WHERE account_id='b' FOR UPDATE"))
            for path,value in [(PREVIEW,{**a,'pool_id':'pool'}),(PREPARE,{**a,'pool_id':'pool','local_preview_sha256':digest}),(READ,{'pool_id':'pool'})]:
                response=client.post(path,json=value,headers=HEADERS)
                assert response.status_code==503 and response.headers['cache-control']=='no-store'
                assert response.json()=={'detail':'Explicit Paper pool operation temporarily unavailable'}
    assert journal.read(engine,'a')['revision']==0


def test_control_change_after_pool_preview_blocks_command(database):
    from core.paper import requested_controls as controls
    from tests.test_requested_execution import STAMP
    engine,_,settings=database;ready(engine);a,_=proposed(engine,'a')
    with TestClient(create_app(configured(settings))) as client:
        forecast=client.post(PREVIEW,json={**a,'pool_id':'pool'},headers=HEADERS).json()
        controls.command(engine,'a','stop',2,'STOP',STAMP.isoformat())
        response=client.post(PREPARE,json={**a,'pool_id':'pool','local_preview_sha256':forecast['local_preview']['sha256']},headers=HEADERS)
        assert response.status_code==409
    assert journal.read(engine,'a')['revision']==0
