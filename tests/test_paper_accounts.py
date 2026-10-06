"""Real PostgreSQL discovery, control atomicity, races and append-only protection."""
from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4
import pytest
from alembic import command
from fastapi.testclient import TestClient
from sqlalchemy import select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session
from apps.api.main import create_app
from core.paper import accounts, sessions, streams
from core.storage.models import PaperSessionRecord, PaperControlRecord
from tests.test_integration import database
from tests.test_paper import request
from tests.test_backtest import liquid, rules
from tests.test_paper_streams import setup


def historical(engine):
    return sessions.create(engine, request(), liquid([10,12,14,10,9]), rules())


def test_discovery_pagination_filters_and_no_unreached_data(database):
    engine,_,_=database
    ids=[historical(engine)['session_id'] for _ in range(4)]
    # Equal timestamps exercise the deterministic UUID tie-breaker.
    with Session(engine) as db,db.begin():
        rows=list(db.scalars(select(PaperSessionRecord)))
        for row in rows:row.created_at=rows[0].created_at
    first=accounts.listing(engine,'sessions',limit=2,status='READY')
    second=accounts.listing(engine,'sessions',limit=2,status='READY',cursor=first['next_cursor'])
    assert first['next_cursor'] and second['next_cursor'] is None
    assert [item['session_id'] for item in first['items']+second['items']]==sorted(ids,reverse=True)
    for item in first['items']:
        assert item['account']['cash']=='1000' and item['position']==0
        assert item['integrity']=='VERIFIED' and not {'snapshot','dataset','candles','orders','fills'} & item.keys()
    ended=sessions.command(engine,ids[0],0,'step',10)
    assert ended['status']=='ENDED'
    assert [item['session_id'] for item in accounts.listing(engine,'sessions',status='ENDED')['items']]==[ids[0]]


def test_corrupt_account_discovered_without_confirmed_balance(database):
    engine,_,_=database;account=historical(engine)
    with Session(engine) as db,db.begin():
        row=db.get(PaperSessionRecord,account['session_id']);row.ledger={**row.ledger,'account':{**row.ledger['account'],'cash':'999'}}
    item=accounts.listing(engine,'sessions')['items'][0]
    assert item['integrity']=='INVALID' and item['account'] is None


def test_applied_noop_conflict_events_and_early_empty_history(database):
    engine,_,_=database;account=historical(engine);id=account['session_id']
    assert accounts.controls(engine,'sessions',id)['items']==[]
    sessions.command(engine,id,0,'reset')
    updated=sessions.command(engine,id,0,'step',2)
    with pytest.raises(sessions.Conflict):sessions.command(engine,id,0,'step')
    assert sessions.read(engine,id)==updated
    events=accounts.controls(engine,'sessions',id)['items']
    assert [event['outcome'] for event in events]==['CONFLICT','APPLIED','NOOP']
    assert (events[1]['before']['position'],events[1]['after']['position'])==(0,2)
    assert (events[0]['expected_revision'],events[0]['before_revision'],events[0]['after_revision'])==(0,1,1)
    assert all(event['actor']=='UNAUTHENTICATED_LOCAL' for event in events)
    first=accounts.controls(engine,'sessions',id,limit=2)
    last=accounts.controls(engine,'sessions',id,limit=2,cursor=first['next_cursor'])
    assert len(last['items'])==1 and last['next_cursor'] is None


@pytest.mark.parametrize('failure',['helper','database'])
def test_audit_failure_rolls_back_state_and_ledger(database,monkeypatch,failure):
    engine,_,_=database;account=historical(engine)
    def fail(*args):
        if failure=='helper':raise RuntimeError('audit storage unavailable')
        accounts.record_control(*args)
        for item in args[0].new:
            if isinstance(item,PaperControlRecord):item.count=0
    monkeypatch.setattr(sessions,'record_control',fail)
    with pytest.raises(RuntimeError if failure=='helper' else DBAPIError):sessions.command(engine,account['session_id'],0,'step',3)
    assert sessions.read(engine,account['session_id'])==account
    assert accounts.controls(engine,'sessions',account['session_id'])['items']==[]


def test_integrity_failure_does_not_claim_applied_control(database):
    engine,_,_=database;account=historical(engine)
    with Session(engine) as db,db.begin():
        row=db.get(PaperSessionRecord,account['session_id']);row.ledger={**row.ledger,'fills':[{'bad':True}]}
    with pytest.raises(ValueError):sessions.command(engine,account['session_id'],0,'step')
    assert accounts.controls(engine,'sessions',account['session_id'])['items']==[]


def test_racing_commands_have_one_applied_and_one_conflict(database):
    engine,_,_=database;account=historical(engine)
    def step(_):
        try:sessions.command(engine,account['session_id'],0,'step');return 'APPLIED'
        except sessions.Conflict:return 'CONFLICT'
    with ThreadPoolExecutor(max_workers=2) as pool:results=list(pool.map(step,range(2)))
    assert sorted(results)==['APPLIED','CONFLICT']
    assert sessions.read(engine,account['session_id'])['cursor']==1
    assert sorted(event['outcome'] for event in accounts.controls(engine,'sessions',account['session_id'])['items'])==['APPLIED','CONFLICT']


@pytest.mark.parametrize('operation',['UPDATE paper_controls SET action=action','DELETE FROM paper_controls'])
def test_audit_rows_reject_rewrites(database,operation):
    engine,_,_=database;account=historical(engine);sessions.command(engine,account['session_id'],0,'halt')
    with engine.begin() as conn:
        with pytest.raises(DBAPIError):conn.execute(text(operation))
    assert len(accounts.controls(engine,'sessions',account['session_id'])['items'])==1


def test_stream_status_discovery_and_terminal_controls(database):
    engine,_,_=database;account,_=setup(engine);id=account['session_id']
    paused=streams.command(engine,id,0,'pause')
    streams.command(engine,id,paused['revision'],'pause')
    assert accounts.listing(engine,'streams',status='PAUSED')['items'][0]['session_id']==id
    stopped=streams.command(engine,id,paused['revision'],'stop')
    with pytest.raises(ValueError):streams.command(engine,id,stopped['revision'],'resume')
    with pytest.raises(streams.Conflict):streams.command(engine,id,0,'stop')
    events=accounts.controls(engine,'streams',id)['items']
    assert [event['outcome'] for event in events]==['CONFLICT','APPLIED','NOOP','APPLIED']
    assert accounts.listing(engine,'streams',status='RUNNING')['items']==[]
    assert accounts.listing(engine,'streams',status='STOPPED')['items'][0]['account']==stopped['account']


def test_migration_preserves_accounts_and_does_not_invent_old_controls(database):
    engine,config,_=database;account=historical(engine)
    command.downgrade(config,'0007');command.upgrade(config,'head');command.check(config)
    assert sessions.read(engine,account['session_id'])==account
    assert accounts.controls(engine,'sessions',account['session_id'])['items']==[]


def test_api_paths_validation_missing_account_and_status_route(database):
    engine,_,settings=database;account=historical(engine)
    with TestClient(create_app(settings)) as client:
        assert client.get('/api/v1/paper/status').status_code==200
        assert client.get('/api/v1/paper/sessions').json()['items'][0]['session_id']==account['session_id']
        assert client.get(f"/api/v1/paper/sessions/{account['session_id']}/controls").json()['items']==[]
        for query in ['limit=21','limit=0','cursor=bad','status=STOPPED']:
            assert client.get('/api/v1/paper/sessions?'+query).status_code==422
        assert client.get(f'/api/v1/paper/streams/{uuid4()}/controls').status_code==404
        assert client.post(f"/api/v1/paper/sessions/{account['session_id']}/command",json={'expected_revision':0,'action':'step'}).status_code==200
        assert client.post(f"/api/v1/paper/sessions/{account['session_id']}/command",json={'expected_revision':0,'action':'step'}).status_code==409
        assert len(client.get(f"/api/v1/paper/sessions/{account['session_id']}/controls").json()['items'])==2
