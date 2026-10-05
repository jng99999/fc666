from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from datetime import datetime
from uuid import uuid4
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from apps.api.main import create_app
from core.replay import sessions
from core.models import Candle
from core.indicators.engine import calculate
from core.storage.models import ReplaySessionRecord as Replay,InstrumentRecord
from core.market_data.storage import save_candles
from tests.test_integration import database
from tests.test_market_integration import seed
from tests.test_backtest import liquid,rules


def request():return {'symbol':'BTCUSDT','timeframe':'1m','period':2,'limit':7,'as_of':'2026-02-01T00:00:00+00:00'}


def test_prefix_clock_causality_reset_and_no_future_response(database):
    engine,_,_=database;bars=liquid([10,12,14,16,12,11,20])
    created=sessions.create(engine,request(),bars,rules());session_id=created['session_id']
    assert created['cursor']==0 and created['revision']==0 and created['candles']==created['indicators']==[]
    assert created['clock']==bars[0].open_time
    for cursor in range(1,len(bars)+1):
        observed=sessions.command(engine,session_id,cursor-1,'step')
        assert observed['cursor']==observed['revision']==cursor
        assert observed['candles']==[bar.model_dump(mode='json') for bar in bars[:cursor]]
        assert observed['clock']==bars[cursor-1].close_time
        assert observed['indicators']==calculate(bars[:cursor],as_of=observed['clock'],period=2)
        assert all(datetime.fromisoformat(row['available_at'])<=observed['clock'] for row in observed['indicators'])
        assert 'dataset' not in observed and 'snapshot' not in observed and 'instrument' not in observed
    assert observed['status']=='ENDED'
    assert sessions.command(engine,session_id,len(bars),'step')==observed
    reset=sessions.command(engine,session_id,len(bars),'reset')
    assert reset['cursor']==0 and reset['revision']==len(bars)+1
    assert reset['indicators']==reset['candles']==[]
    stepped=sessions.command(engine,session_id,reset['revision'],'step',2)
    assert stepped['indicators']==calculate(bars[:2],as_of=bars[1].close_time,period=2)


def test_future_prices_cannot_change_visible_prefix(database):
    engine,_,_=database
    first=sessions.create(engine,request(),liquid([10,12,14,16,12,11,20]),rules())
    second=sessions.create(engine,request(),liquid([10,12,14,16,120,110,200]),rules())
    a=sessions.command(engine,first['session_id'],0,'step',4)
    b=sessions.command(engine,second['session_id'],0,'step',4)
    assert a['candles']==b['candles'] and a['indicators']==b['indicators'] and a['clock']==b['clock']
    assert a['manifest']['snapshot_sha256']!=b['manifest']['snapshot_sha256']


def test_revision_fences_concurrent_and_duplicate_commands(database):
    engine,_,_=database;created=sessions.create(engine,request(),liquid([10,12,14,16]),rules())
    def advance(_):
        try:return sessions.command(engine,created['session_id'],0,'step')['cursor']
        except sessions.Conflict:return 'conflict'
    with ThreadPoolExecutor(max_workers=4) as pool:results=list(pool.map(advance,range(4)))
    assert results.count(1)==1 and results.count('conflict')==3
    assert sessions.read(engine,created['session_id'])['cursor']==1
    with pytest.raises(sessions.Conflict):sessions.command(engine,created['session_id'],0,'reset')


def test_api_snapshot_survives_history_rule_changes_and_new_client(database):
    engine,_,settings=database;seed(engine);bars=liquid([10,12,14,16,12,11,20]);save_candles(engine,bars)
    with TestClient(create_app(settings)) as client:
        response=client.post('/api/v1/replay/sessions',json=request());assert response.status_code==201
        created=response.json();session_id=created['session_id']
        url=f'/api/v1/replay/sessions/{session_id}'
        stepped=client.post(url+'/command',json={'expected_revision':0,'action':'step','count':3}).json()
    save_candles(engine,liquid([10,12,14,16,12,11,20,99]))
    with Session(engine) as session,session.begin():session.get(InstrumentRecord,rules().instrument_id).tick_size='10'
    with TestClient(create_app(settings)) as client:
        assert client.get(url).json()==stepped
        assert client.post(url+'/command',json={'expected_revision':0,'action':'step'}).status_code==409
        complete=client.post(url+'/command',json={'expected_revision':1,'action':'step','count':10}).json()
        assert complete['status']=='ENDED' and complete['total']==7
        assert complete['manifest']['snapshot_sha256']==created['manifest']['snapshot_sha256']
        assert [Candle.model_validate(bar) for bar in complete['candles']]==bars
        assert client.get(f'/api/v1/replay/sessions/{uuid4()}').status_code==404
        assert client.get('/api/v1/replay/sessions/bad').status_code==422
        for body in [{'expected_revision':True,'action':'step'},{'expected_revision':1,'action':'reset','count':2},{'expected_revision':1,'action':'step','count':11},{'expected_revision':1,'action':'seek'}]:
            assert client.post(url+'/command',json=body).status_code==422


def test_corrupt_snapshot_rejected_without_cursor_change(database):
    engine,_,_=database;created=sessions.create(engine,request(),liquid([10,12,14]),rules())
    with Session(engine) as session,session.begin():
        record=session.get(Replay,created['session_id']);snapshot=deepcopy(record.snapshot)
        snapshot['dataset'][2]['close']='99';record.snapshot=snapshot
    with pytest.raises(ValueError,match='integrity'):sessions.command(engine,created['session_id'],0,'step')
    with Session(engine) as session:
        record=session.get(Replay,created['session_id']);assert record.cursor==record.revision==0


def test_database_cursor_constraint_and_invalid_history(database):
    engine,_,_=database;created=sessions.create(engine,request(),liquid([10,12,14]),rules())
    with engine.connect() as connection:
        with pytest.raises(IntegrityError):connection.execute(text('UPDATE replay_sessions SET cursor=4 WHERE session_id=:id'),{'id':created['session_id']})
    for bars in [liquid([10]),liquid([10,12,14])[::2],list(reversed(liquid([10,12,14])))]:
        with pytest.raises(ValueError):sessions.create(engine,request(),bars,rules())
