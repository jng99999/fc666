"""Scoped enrollment and read-only preview use isolated fixture state."""
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from apps.api.main import create_app
from apps.api.settings import Settings
from core.paper import requested_controls as controls,requested_preview as preview,requested_journal as journal
from tests.test_integration import database
from tests.test_requested_commands import configured,HEADERS
from tests.test_requested_controls import LIMITS,setup,command
from tests.test_requested_execution import BASE,STAMP,request
from tests.test_requested_finalization import time
from tests.test_requested_preview import proposal

ENROLL='/api/v1/paper-requested/enrollment-commands'
PREVIEW='/api/v1/paper-requested/preparation-previews'


def enrollment(**updates):return {'account_id':'account','command_id':'enroll','expected_financial_revision':0,'created_at':STAMP.isoformat(),'limits':LIMITS,**updates}


def test_disabled_authentication_and_independent_grants_precede_storage(monkeypatch):
    settings=Settings(database_url='postgresql+psycopg://invalid:invalid@127.0.0.1:1/no_db')
    def forbidden(*args,**kwargs):raise AssertionError('Denied access reached storage')
    monkeypatch.setattr(controls,'enroll',forbidden);monkeypatch.setattr(preview,'capture',forbidden)
    with TestClient(create_app(settings)) as client:
        for path,value in [(ENROLL,enrollment()),(PREVIEW,proposal())]:assert client.post(path,json=value).status_code==503
    with TestClient(create_app(configured(settings))) as client:
        for path,value in [(ENROLL,enrollment()),(PREVIEW,proposal())]:
            assert client.post(path,json=value).status_code==401
            assert client.post(path,json=value,headers=HEADERS).status_code==403
    for action,path,value in [('ENROLL',ENROLL,enrollment()),('PREVIEW_PREPARE',PREVIEW,proposal())]:
        with TestClient(create_app(configured(settings,actions=[action]))) as client:
            assert client.post(path,json={**value,'account_id':'other'},headers=HEADERS).status_code==403
            assert client.post(path,json={**value,'expected_financial_revision':True},headers=HEADERS).status_code==422
            assert client.post(path,json={**value,'base':BASE},headers=HEADERS).status_code==422


def test_enroll_pauses_preview_denies_until_explicit_resume_and_never_reserves(database,tmp_path):
    engine,_,settings=database;journal.create(engine,'account','fixture:spot',BASE,time(0))
    with TestClient(create_app(configured(settings,actions=['ENROLL','RESUME','PREVIEW_PREPARE']))) as client:
        response=client.post(ENROLL,json=enrollment(),headers=HEADERS)
        assert response.status_code==200 and response.json()['controls']['state']=='PAUSED'
        assert client.post(PREVIEW,json=proposal(expected_control_revision=1),headers=HEADERS).status_code==409
        command(engine,'resume','RESUME');before=controls.capture(engine,'account')
        one=client.post(PREVIEW,json=proposal(),headers=HEADERS)
        two=client.post(PREVIEW,json=proposal(),headers=HEADERS)
        assert one.status_code==two.status_code==200 and one.json()==two.json() and one.headers['cache-control']=='no-store'
        report=preview.verify(one.json());assert report['request']['base']==BASE and report['reservation_created'] is False
        assert controls.capture(engine,'account')==before
        # The preview agrees with actual internal preparation, but has not admitted anything itself.
        assert journal.prepare(engine,report['request'])==report['projected_summary']


def test_enrollment_retry_uses_original_financial_checkpoint_after_later_activity(database):
    engine,_,settings=database;journal.create(engine,'account','fixture:spot',BASE,time(0))
    with TestClient(create_app(configured(settings,actions=['ENROLL']))) as client:
        accepted=client.post(ENROLL,json=enrollment(),headers=HEADERS).json()['accepted_enrollment']
        command(engine,'resume','RESUME');journal.prepare(engine,request());before=controls.capture(engine,'account')
        again=client.post(ENROLL,json=enrollment(),headers=HEADERS)
        assert again.status_code==200 and again.json()['accepted_enrollment']==accepted and again.json()['controls']['state']=='ACTIVE'
        for value in [enrollment(expected_financial_revision=1),enrollment(limits={**LIMITS,'max_order_quantity':'9'}),enrollment(command_id='changed')]:
            assert client.post(ENROLL,json=value,headers=HEADERS).status_code==409
        assert controls.capture(engine,'account')==before


def test_preview_fences_control_changes_and_pending_requests(database):
    engine,_,settings=database;setup(engine);before=controls.capture(engine,'account')
    with TestClient(create_app(configured(settings,actions=['PREVIEW_PREPARE']))) as client:
        for value in [proposal(expected_control_revision=1),proposal(expected_financial_revision=1),proposal(requested_quantity='11')]:
            assert client.post(PREVIEW,json=value,headers=HEADERS).status_code==409
        assert controls.capture(engine,'account')==before
        command(engine,'pause','PAUSE')
        assert client.post(PREVIEW,json=proposal(expected_control_revision=3),headers=HEADERS).status_code==409
        command(engine,'again','RESUME');journal.prepare(engine,request())
        assert client.post(PREVIEW,json=proposal(expected_control_revision=4,expected_financial_revision=1),headers=HEADERS).status_code==409


def test_enrollment_vs_prepare_race_has_one_checkpoint_winner(database):
    engine,_,settings=database;journal.create(engine,'account','fixture:spot',BASE,time(0));barrier=Barrier(2)
    with TestClient(create_app(configured(settings,actions=['ENROLL']))) as client:
        def enroll():barrier.wait(timeout=5);return client.post(ENROLL,json=enrollment(),headers=HEADERS).status_code
        def prepare():
            barrier.wait(timeout=5)
            try:journal.prepare(engine,request());return True
            except ValueError:return False
        with ThreadPoolExecutor(2) as pool:
            one=pool.submit(enroll);two=pool.submit(prepare);results=(one.result(),two.result())
    assert results in [(200,False),(409,True)]
    controls.capture(engine,'account')


def test_preview_uses_locked_single_snapshot_under_concurrent_pause(database):
    engine,_,settings=database;setup(engine);barrier=Barrier(2)
    with TestClient(create_app(configured(settings,actions=['PREVIEW_PREPARE']))) as client:
        def inspect():barrier.wait(timeout=5);return client.post(PREVIEW,json=proposal(),headers=HEADERS)
        def pause():barrier.wait(timeout=5);return command(engine,'pause','PAUSE')
        with ThreadPoolExecutor(2) as pool:
            one=pool.submit(inspect);two=pool.submit(pause);response=one.result();two.result()
    assert response.status_code in [200,409]
    if response.status_code==200:assert preview.verify(response.json())['controls']['state']=='ACTIVE'
    assert journal.read(engine,'account')['revision']==0


def test_lock_timeout_and_missing_accounts_are_generic(database):
    engine,_,settings=database;setup(engine)
    with TestClient(create_app(configured(settings,accounts=['account','missing'],actions=['ENROLL','PREVIEW_PREPARE']))) as client:
        for path,value in [(ENROLL,enrollment(account_id='missing')),(PREVIEW,proposal(account_id='missing'))]:
            assert client.post(path,json=value,headers=HEADERS).status_code==404
        with engine.begin() as conn:
            conn.execute(text("SELECT account_id FROM requested_paper_accounts WHERE account_id='account' FOR UPDATE"))
            for path,value in [(ENROLL,enrollment()),(PREVIEW,proposal())]:
                response=client.post(path,json=value,headers=HEADERS)
                assert response.status_code==503 and set(response.json())=={'detail'} and response.headers['cache-control']=='no-store'


def test_storage_unavailable_reports_no_partial_preview():
    settings=Settings(database_url='postgresql+psycopg://invalid:invalid@127.0.0.1:1/no_db')
    with TestClient(create_app(configured(settings,actions=['ENROLL','PREVIEW_PREPARE']))) as client:
        for path,value in [(ENROLL,enrollment()),(PREVIEW,proposal())]:
            response=client.post(path,json=value,headers=HEADERS);assert response.status_code==503 and set(response.json())=={'detail'}
