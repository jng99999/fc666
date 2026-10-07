"""Independently scoped local ownership writes; isolated PostgreSQL only."""
from copy import deepcopy
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from apps.api.main import create_app
from apps.api.settings import Settings
from core.paper import requested_ownership as own, requested_journal as journal, requested_sources as sources
from tests.test_integration import database
from tests.test_requested_sources import prepared, reviewed
from tests.test_requested_commands import configured, HEADERS
from tests.test_requested_execution import request,event,fill
from tests.test_requested_controls import command

CLAIM='/api/v1/paper-requested/ownership-commands'
DELIVER='/api/v1/paper-requested/owned-source-commands'


def claim_body(req,**updates):
    return {'account_id':'account','request_id':req['request_id'],'owner':'first','ttl_seconds':60,
            'expected_financial_revision':1,'expected_control_revision':2,'expected_token':0,**updates}


def send(client,path,value):return client.post(path,json=value,headers=HEADERS)


def test_independent_actions_and_account_authentication_precede_storage(monkeypatch):
    settings=Settings(database_url='postgresql+psycopg://invalid:invalid@127.0.0.1:1/no_db');req=request();claim=claim_body(req)
    source={'account_id':'account','request_id':req['request_id'],'owner':'first','ownership_token':1,
            'expected_financial_revision':1,'expected_control_revision':2,'event':event(req,0,'SUBMIT'),
            'source':{'kind':'LOCAL_PAPER_OPERATOR_INPUT','source_id':'fixture'},'preview_sha256':'0'*64}
    def forbidden(*args,**kwargs):raise AssertionError('Denied call reached storage')
    monkeypatch.setattr(own,'claim',forbidden);monkeypatch.setattr(own,'deliver',forbidden)
    with TestClient(create_app(settings)) as client:
        for path,value in [(CLAIM,claim),(DELIVER,source)]:assert send(client,path,value).status_code==503
    for grants,path,value in [(['DELIVER_OWNED_EVENT'],CLAIM,claim),(['CLAIM_OWNERSHIP'],DELIVER,source),(['INGEST_EVENT','PREVIEW_EVENT'],CLAIM,claim),(['INGEST_EVENT'],DELIVER,source)]:
        with TestClient(create_app(configured(settings,actions=grants))) as client:
            assert client.post(path,json=value).status_code==401
            assert send(client,path,value).status_code==403
    with TestClient(create_app(configured(settings,actions=['CLAIM_OWNERSHIP','DELIVER_OWNED_EVENT']))) as client:
        for path,value in [(CLAIM,claim),(DELIVER,source)]:
            response=send(client,path,{**value,'account_id':'other'});assert response.status_code==403 and response.headers['cache-control']=='no-store'
        for update in [{'expected_token':True},{'ttl_seconds':61},{'expected_financial_revision':'1'},{'owner':''},{'unexpected':1}]:
            assert send(client,CLAIM,{**claim,**update}).status_code==422
        for update in [{'ownership_token':True},{'ownership_token':0},{'preview_sha256':'bad'},{'owner':''},{'source':{'kind':'EXCHANGE_VERIFIED','source_id':'fixture'}}]:
            assert send(client,DELIVER,{**source,**update}).status_code==422


def test_claim_and_delivery_retry_original_evidence_after_later_activity(database):
    engine,_,settings=database;req=prepared(engine);before=journal.read(engine,'account')
    with TestClient(create_app(configured(settings,actions=['CLAIM_OWNERSHIP','DELIVER_OWNED_EVENT','INGEST_EVENT']))) as client:
        body=claim_body(req);response=send(client,CLAIM,body);assert response.status_code==200 and response.headers['cache-control']=='no-store'
        original=response.json();assert original['accepted_claim']['token']==1 and journal.read(engine,'account')==before
        assert send(client,CLAIM,body).json()==original
        proposal,_=reviewed(engine,req,event(req,0,'SUBMIT'));value={**proposal,'owner':'first','ownership_token':1}
        assert send(client,'/api/v1/paper-requested/source-commands',proposal).status_code==409
        one=send(client,DELIVER,value);assert one.status_code==200 and one.headers['cache-control']=='no-store'
        unknown,_=reviewed(engine,req,event(req,1,'UNKNOWN_SUBMISSION'));assert send(client,DELIVER,{**unknown,'owner':'first','ownership_token':1}).status_code==200
        assert send(client,CLAIM,body).json()==original
        assert send(client,DELIVER,value).json()==one.json()
        assert len(own.capture(engine,'account')['claims'])==1 and journal.read(engine,'account')['revision']==3
        for update in [{'ttl_seconds':30},{'owner':'changed'},{'expected_control_revision':1},{'expected_token':2}]:
            assert send(client,CLAIM,{**body,**update}).status_code==409


def test_fresh_claim_stale_dual_versions_and_token_have_no_effect(database):
    engine,_,settings=database;req=prepared(engine)
    with TestClient(create_app(configured(settings,actions=['CLAIM_OWNERSHIP']))) as client:
        for update in [{'expected_financial_revision':2},{'expected_control_revision':1},{'expected_token':1}]:
            assert send(client,CLAIM,claim_body(req,**update)).status_code==409
        assert own.capture(engine,'account')['claims']==[]
        assert send(client,CLAIM,claim_body(req)).status_code==200


def test_expired_takeover_requires_current_checkpoint_and_rejects_old_owners(database,monkeypatch):
    engine,_,settings=database;req=prepared(engine)
    with TestClient(create_app(configured(settings,actions=['CLAIM_OWNERSHIP','DELIVER_OWNED_EVENT']))) as client:
        first=send(client,CLAIM,claim_body(req)).json()['accepted_claim']
        value,_=reviewed(engine,req,event(req,0,'SUBMIT'));old={**value,'owner':'first','ownership_token':1}
        one=send(client,DELIVER,old);assert one.status_code==200
        monkeypatch.setattr(own,'clock',lambda db:first['expires_us'])
        assert send(client,CLAIM,claim_body(req)).status_code==409
        second=send(client,CLAIM,claim_body(req,owner='second',expected_token=1,expected_financial_revision=2))
        assert second.status_code==200 and second.json()['accepted_claim']['token']==2
        assert send(client,DELIVER,old).status_code==409
        assert send(client,DELIVER,{**old,'owner':'second','ownership_token':2}).json()==one.json()
        command(engine,'stop','STOP',2)
        late,_=reviewed(engine,req,fill(req,1));assert send(client,DELIVER,{**late,'owner':'second','ownership_token':2}).status_code==200
        report=own.capture(engine,'account');assert own.verify(report)==report and report['source_evidence']['controls']['state']=='STOPPED'


@pytest.mark.parametrize('same',[False,True])
def test_concurrent_api_claims_persist_once(database,same):
    engine,_,settings=database;req=prepared(engine);one=claim_body(req);two=one if same else claim_body(req,owner='other');barrier=Barrier(2)
    with TestClient(create_app(configured(settings,actions=['CLAIM_OWNERSHIP']))) as client:
        def attempt(value):barrier.wait(timeout=5);return send(client,CLAIM,value).status_code
        with ThreadPoolExecutor(2) as pool:results=list(pool.map(attempt,[one,two]))
    assert sorted(results)==([200,200] if same else [200,409])
    assert len(own.capture(engine,'account')['claims'])==1


def test_expiry_in_http_delivery_rolls_back_source_gate_and_money(database,monkeypatch):
    engine,_,settings=database;req=prepared(engine)
    with TestClient(create_app(configured(settings,actions=['CLAIM_OWNERSHIP','DELIVER_OWNED_EVENT']))) as client:
        lease=send(client,CLAIM,claim_body(req)).json()['accepted_claim'];value,_=reviewed(engine,req,event(req,0,'SUBMIT'));before=sources.capture(engine,'account');count=0
        def now(db):
            nonlocal count
            count+=1;return lease['acquired_us']+1 if count<=2 else lease['expires_us']
        monkeypatch.setattr(own,'clock',now)
        assert send(client,DELIVER,{**value,'owner':'first','ownership_token':1}).status_code==409
        assert sources.capture(engine,'account')==before


def test_missing_unavailable_and_locked_commands_are_generic(database):
    engine,_,settings=database;req=prepared(engine)
    with TestClient(create_app(configured(settings,accounts=['missing','account'],actions=['CLAIM_OWNERSHIP','DELIVER_OWNED_EVENT']))) as client:
        response=send(client,CLAIM,claim_body(req,account_id='missing'));assert response.status_code==404 and response.headers['cache-control']=='no-store'
        with engine.connect() as conn:
            tx=conn.begin();conn.execute(text("SELECT account_id FROM requested_paper_accounts WHERE account_id='account' FOR UPDATE"))
            response=send(client,CLAIM,claim_body(req));assert response.status_code==503 and set(response.json())=={'detail'};tx.rollback()
    bad=Settings(database_url='postgresql+psycopg://invalid:invalid@127.0.0.1:1/no_db')
    with TestClient(create_app(configured(bad,actions=['CLAIM_OWNERSHIP']))) as client:
        response=send(client,CLAIM,claim_body(req));assert response.status_code==503 and response.headers['cache-control']=='no-store'


def test_denied_delivery_cannot_enroll_release_hold_or_change_source(database):
    engine,_,settings=database;req=prepared(engine);value,_=reviewed(engine,req,event(req,0,'SUBMIT'));owned={**value,'owner':'first','ownership_token':1}
    with TestClient(create_app(configured(settings,actions=['CLAIM_OWNERSHIP','DELIVER_OWNED_EVENT']))) as client:
        before=sources.capture(engine,'account')
        assert send(client,DELIVER,owned).status_code==409
        assert own.capture(engine,'account')['claims']==[] and sources.capture(engine,'account')==before
        assert send(client,CLAIM,claim_body(req)).status_code==200
        before=sources.capture(engine,'account')
        for update in [{'owner':'wrong'},{'ownership_token':2},{'preview_sha256':'0'*64},{'expected_financial_revision':2}]:
            response=send(client,DELIVER,{**owned,**update});assert response.status_code==409 and set(response.json())=={'detail'}
        assert sources.capture(engine,'account')==before
        assert send(client,DELIVER,owned).status_code==200
        before=sources.capture(engine,'account');changed=deepcopy(owned);changed['source']['source_id']='changed'
        assert send(client,DELIVER,changed).status_code==409 and sources.capture(engine,'account')==before


def test_delivery_missing_unavailable_and_lock_timeout_do_not_leak_partial_evidence(database):
    engine,_,settings=database;req=prepared(engine);value,_=reviewed(engine,req,event(req,0,'SUBMIT'));body={**value,'owner':'first','ownership_token':1}
    own.claim(engine,'account',req['request_id'],'first',60)
    with TestClient(create_app(configured(settings,accounts=['account','missing'],actions=['DELIVER_OWNED_EVENT']))) as client:
        assert send(client,DELIVER,{**body,'account_id':'missing'}).status_code==404
        with engine.connect() as conn:
            tx=conn.begin();conn.execute(text("SELECT account_id FROM requested_paper_accounts WHERE account_id='account' FOR UPDATE"))
            response=send(client,DELIVER,body);assert response.status_code==503 and response.headers['cache-control']=='no-store' and set(response.json())=={'detail'};tx.rollback()
    bad=Settings(database_url='postgresql+psycopg://invalid:invalid@127.0.0.1:1/no_db')
    with TestClient(create_app(configured(bad,actions=['DELIVER_OWNED_EVENT']))) as client:
        response=send(client,DELIVER,body);assert response.status_code==503 and set(response.json())=={'detail'}
    assert sources.capture(engine,'account')['sources']==[]


def test_partial_or_boolean_library_fences_fail_before_storage():
    req=request()
    for kwargs in [{'expected_token':0},{'expected_financial_revision':True,'expected_control_revision':2,'expected_token':0},{'expected_financial_revision':1,'expected_control_revision':2,'expected_token':65}]:
        with pytest.raises(ValueError):own.claim(None,'account',req['request_id'],'first',30,**kwargs)
