from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from uuid import uuid4
import pytest
from sqlalchemy import text,select,func
from sqlalchemy.orm import Session
from sqlalchemy.exc import DBAPIError
from fastapi.testclient import TestClient
from apps.api.main import create_app
from core.portfolio import history
from core.portfolio.valuation import capture,MissingAccount
from core.storage.models import PortfolioScenarioRecord as Scenario,PortfolioSnapshotRecord as Snapshot
from scripts.verify_portfolio_history import verify
from tests.test_integration import database
from tests.test_portfolio import fixture


def setup(engine,monkeypatch):
    ids,_,cutoff=fixture(engine)
    monkeypatch.setattr(history,'capture',lambda engine,ids,limits:capture(engine,ids,limits,as_of=cutoff))
    definition=history.Definition(name='Frozen portfolio',session_ids=ids)
    scenario=history.create(engine,str(uuid4()),definition)
    return definition,scenario


def test_frozen_history_replays_and_offline_verifies(database,monkeypatch):
    engine,_,_=database;definition,scenario=setup(engine,monkeypatch)
    key=str(uuid4());snapshot=history.save(engine,scenario['scenario_id'],key)
    assert snapshot['report']['status']=='COMPLETE'
    assert verify(snapshot)['accounts']==2
    monkeypatch.setattr(history,'capture',lambda *args:(_ for _ in ()).throw(AssertionError('Must never recapture')))
    assert history.save(engine,scenario['scenario_id'],key)==snapshot
    assert history.saved(engine,scenario['scenario_id'],snapshot['snapshot_id'])==snapshot
    assert history.read(engine,scenario['scenario_id'])==scenario
    assert history.listing(engine)['items']==[scenario]
    assert history.snapshots(engine,scenario['scenario_id'],status='UNAVAILABLE')['items']==[]
    assert history.snapshots(engine,scenario['scenario_id'])['items'][0]['totals']==snapshot['report']['totals']
    broken=deepcopy(snapshot);broken['report']['totals']['equity']='0'
    with pytest.raises(ValueError):verify(broken)
    broken=deepcopy(snapshot);broken['scenario_id']=str(uuid4())
    with pytest.raises(ValueError):verify(broken)


def test_creation_idempotency_conflict_capacity(database,monkeypatch):
    engine,_,_=database;definition,scenario=setup(engine,monkeypatch)
    assert history.create(engine,scenario['request_id'],definition)==scenario
    with pytest.raises(history.Conflict):history.create(engine,scenario['request_id'],definition.model_copy(update={'name':'Other'}))
    monkeypatch.setattr(history,'SCENARIO_LIMIT',1)
    assert history.create(engine,scenario['request_id'],definition)==scenario
    with pytest.raises(history.Capacity):history.create(engine,str(uuid4()),definition)


def test_concurrent_snapshot_and_capacity(database,monkeypatch):
    engine,_,_=database;_,scenario=setup(engine,monkeypatch);key=str(uuid4())
    with ThreadPoolExecutor(max_workers=2) as pool:
        values=list(pool.map(lambda _:history.save(engine,scenario['scenario_id'],key),range(2)))
    assert values[0]==values[1]
    monkeypatch.setattr(history,'SNAPSHOT_LIMIT',1)
    assert history.save(engine,scenario['scenario_id'],key)==values[0]
    with pytest.raises(history.Capacity):history.save(engine,scenario['scenario_id'],str(uuid4()))
    with Session(engine) as db:assert db.scalar(select(func.count()).select_from(Snapshot))==1


@pytest.mark.parametrize('table',['portfolio_scenarios','portfolio_snapshots'])
@pytest.mark.parametrize('operation',['UPDATE','DELETE'])
def test_database_immutability(database,monkeypatch,table,operation):
    engine,_,_=database;_,scenario=setup(engine,monkeypatch);history.save(engine,scenario['scenario_id'],str(uuid4()))
    query=f'UPDATE {table} SET request_id=request_id' if operation=='UPDATE' else f'DELETE FROM {table}'
    with pytest.raises(DBAPIError),engine.begin() as connection:connection.execute(text(query))


def test_capture_failure_rolls_back_and_same_key_recovers(database,monkeypatch):
    engine,_,_=database;_,scenario=setup(engine,monkeypatch);original=history.capture;key=str(uuid4())
    monkeypatch.setattr(history,'capture',lambda *args:(_ for _ in ()).throw(ValueError('Invalid source')))
    with pytest.raises(ValueError):history.save(engine,scenario['scenario_id'],key)
    with Session(engine) as db:assert db.scalar(select(func.count()).select_from(Snapshot))==0
    monkeypatch.setattr(history,'capture',original)
    assert history.save(engine,scenario['scenario_id'],key)['request_id']==key


def test_api_validation_pagination_and_read(database,monkeypatch):
    engine,_,settings=database;definition,scenario=setup(engine,monkeypatch)
    with TestClient(create_app(settings)) as client:
        root='/api/v1/portfolio/scenarios'
        assert client.get(root+'?limit=0').status_code==422
        assert client.get(root+'?cursor=bad').status_code==409
        assert client.get(root+'/'+str(uuid4())).status_code==404
        assert client.post(root,json={'request_id':str(uuid4()),'definition':{**definition.model_dump(mode='json'),'as_of':'2026'}}).status_code==422
        for name in ['','\n','bad\x00name']:
            assert client.post(root,json={'request_id':str(uuid4()),'definition':{**definition.model_dump(mode='json'),'name':name}}).status_code==422
        other=history.create(engine,str(uuid4()),definition)
        first=client.get(root+'?limit=1').json();assert first['items'][0]['scenario_id']==other['scenario_id']
        second=client.get(root,params={'limit':1,'cursor':first['next_cursor']}).json();assert second['items']==[scenario]
        path=root+'/'+scenario['scenario_id']+'/snapshots';key=str(uuid4())
        snapshot=client.post(path,json={'request_id':key}).json()
        assert client.post(path,json={'request_id':key}).json()==snapshot
        assert client.get(path+'/'+snapshot['snapshot_id']).json()==snapshot
        assert client.post(path,json={'request_id':str(uuid4()),'as_of':'2026'}).status_code==422
        assert client.get(path+'?status=BAD').status_code==409


def test_concurrent_creation_one_definition(database,monkeypatch):
    engine,_,_=database;definition,_=setup(engine,monkeypatch);key=str(uuid4())
    with ThreadPoolExecutor(max_workers=2) as pool:
        values=list(pool.map(lambda _:history.create(engine,key,definition),range(2)))
    assert values[0]==values[1]


def test_snapshot_pages_and_unavailable_preserve_null(database,monkeypatch):
    engine,_,_=database;_,scenario=setup(engine,monkeypatch);id=scenario['scenario_id']
    first=history.save(engine,id,str(uuid4()))
    from core.portfolio.valuation import evaluate
    source=deepcopy(first['report']['inputs'])
    for account in source['accounts']:account['quote']=None
    monkeypatch.setattr(history,'capture',lambda *args:evaluate(source))
    second=history.save(engine,id,str(uuid4()))
    assert verify(second)['status']=='UNAVAILABLE' and second['report']['totals'] is None
    page=history.snapshots(engine,id,limit=1)
    assert page['items'][0]['snapshot_id']==second['snapshot_id']
    assert history.snapshots(engine,id,limit=1,cursor=page['next_cursor'])['items'][0]['snapshot_id']==first['snapshot_id']
    assert history.snapshots(engine,id,status='UNAVAILABLE')['items'][0]['totals'] is None


def test_corrupted_stored_report_rejected(database,monkeypatch):
    engine,_,_=database;_,scenario=setup(engine,monkeypatch);saved=history.save(engine,scenario['scenario_id'],str(uuid4()))
    # Only isolated test DB: simulate administrator storage damage.
    with engine.begin() as connection:
        connection.execute(text('ALTER TABLE portfolio_snapshots DISABLE TRIGGER portfolio_snapshots_immutable'))
        connection.execute(text("UPDATE portfolio_snapshots SET report_sha256='broken'"))
        connection.execute(text('ALTER TABLE portfolio_snapshots ENABLE TRIGGER portfolio_snapshots_immutable'))
    with pytest.raises(ValueError):history.saved(engine,scenario['scenario_id'],saved['snapshot_id'])
    with pytest.raises(ValueError):history.snapshots(engine,scenario['scenario_id'])
