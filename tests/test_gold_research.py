from dataclasses import replace
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from decimal import Decimal
import gzip
import json
import sqlite3
from uuid import uuid4
import zipfile

from fastapi.testclient import TestClient
import pytest

from apps.api.gold_service import create_app
from core.gold.backtest import Costs, compare, simulate
from core.gold.history import Bar, Catalogue, HOUR, VERSION, aggregate, canonical, sha
from core.gold.ledger import Conflict, Store
from core.gold.research import Research
from core.strategy.registry import DEFINITIONS
from scripts.import_gold_history import parse_archive

START=int(datetime(2024,1,1,tzinfo=timezone.utc).timestamp()*1000)
COSTS={'initial_cash':'10000','allocation':'.5','commission':'.10','slippage':'.05','spread_multiplier':'1'}
VARIANT={'strategy':'ema_long_flat_v1','parameters':{'period':20}}


def bars(count=400):
    values=[]
    for i in range(count):
        price=Decimal(2000)+Decimal(i%80 if i//80%2==0 else 80-i%80)
        t=START+i*HOUR
        values.append(Bar(t,t+1000,t+HOUR-1000,price,price+1,price+Decimal('.1'),price+Decimal('1.1'),price+2,price-1,100,60000))
    return values


def catalogue(tmp_path,count=400):
    directory=tmp_path/'history';directory.mkdir()
    data={'version':VERSION,'symbol':'XAU/USD','source':'test fixture, not a market result','start':START,
          'end':START+count*HOUR,'quality':{'ticks':count*100},'sources':[],'bars':[b.row() for b in bars(count)]}
    identity=sha(data)
    with gzip.open(directory/(identity+'.json.gz'),'wb') as f:f.write(canonical(data))
    return Catalogue(directory),identity


def test_history_digest_bar_validation_and_no_fill(tmp_path):
    cat,key=catalogue(tmp_path)
    data,rows=cat.load(key)
    assert data['identity']==key and len(rows)==400
    assert len(aggregate(rows,4))==100
    assert len(aggregate(rows[:1]+rows[2:],4))==99
    for invalid in (replace(rows[0],ask_open=Decimal('1')),replace(rows[0],last=rows[0].start+1000),replace(rows[0],max_gap=300001)):
        with pytest.raises(ValueError):Bar.parse(invalid.row())
    with pytest.raises(ValueError):cat.load('../bad')
    # A file name is not trusted as a digest, and corruption must fail visibly.
    wrong='0'*64
    (tmp_path/'history'/(wrong+'.json.gz')).write_bytes((tmp_path/'history'/(key+'.json.gz')).read_bytes())
    with pytest.raises(ValueError):cat.load(wrong)


def test_timestamp_clock_rollback_quarantines_hour_without_rewriting(tmp_path):
    p=tmp_path/'ticks.zip'
    with zipfile.ZipFile(p,'w') as z:
        z.writestr('DAT_ASCII_XAUUSD_T_202401.csv',
                   '20240102 000000000,2000,2001,0\n20240102 005959000,2001,2002,0\n'
                   '20240102 000001000,2000,2001,0\n20240102 010000000,2000,2001,0\n'
                   '20240102 015959000,2001,2002,0\n')
    result,quality=parse_archive(p,2024,1)
    assert result==[]  # first hour ambiguous, second hour lacks internal coverage
    assert quality['backwards_tick_events']==1 and quality['ambiguous_hours_excluded']==1


def test_fixed_est_conversion_and_coverage(tmp_path):
    p=tmp_path/'ticks.zip'
    lines=[f'20240102 00{i:02}00000,2000,2001,0' for i in range(60)]
    with zipfile.ZipFile(p,'w') as z:z.writestr('DAT_ASCII_XAUUSD_T_202401.csv','\n'.join(lines)+'\n')
    result,quality=parse_archive(p,2024,1)
    expected=int(datetime(2024,1,2,5,tzinfo=timezone.utc).timestamp()*1000)
    assert len(result)==1 and result[0].start==expected
    assert result[0].bid_open==Decimal(2000) and quality['ticks']==60


def test_next_quote_actual_spread_costs_and_conservation():
    result=simulate(bars(),1,START,START+400*HOUR,Costs(**COSTS),VARIANT)
    assert result['fills']
    for fill in result['fills']:
        assert fill['execution_at']>fill['decision_at']
        reference=Decimal(fill['ask']) if fill['side']=='BUY' else Decimal(fill['bid'])
        expected=reference+Decimal('.05') if fill['side']=='BUY' else reference-Decimal('.05')
        assert Decimal(fill['price'])==expected
        assert Decimal(fill['spread_cost'])==Decimal(fill['ounces'])/2
    metrics=result['metrics']
    assert abs(Decimal(metrics['net_pnl'])-Decimal(metrics['realized_pnl'])-Decimal(metrics['unrealized_pnl']))<Decimal('1e-25')
    assert result['pending_final_signal'] is not None
    assert not any(f['execution_at']>=START+400*HOUR for f in result['fills'])


def test_future_prefix_does_not_change_past_fills():
    short=simulate(bars(200),1,START,START+200*HOUR,Costs(**COSTS),VARIANT)
    long=simulate(bars(400),1,START,START+400*HOUR,Costs(**COSTS),VARIANT)
    assert short['fills']==[f for f in long['fills'] if f['execution_at']<START+200*HOUR]


def test_gap_never_backfills_old_intent():
    rows=bars(250)
    # The existing price rule wants LONG here. Remove the next hour entirely.
    rows=rows[:102]+rows[103:]
    result=simulate(rows,1,START,START+250*HOUR,Costs(**COSTS),VARIANT)
    assert not any(f['decision_at']==START+102*HOUR and f['execution_at']>=START+103*HOUR for f in result['fills'])
    assert result['metrics']['expired_gap_intents']>0


def test_all_eight_strategies_cost_stress_and_determinism(tmp_path):
    cat,key=catalogue(tmp_path,900)
    data,series=cat.load(key)
    request={'dataset_id':key,'start':START,'end':START+900*HOUR,'timeframes':['1h','4h'],
             'variants':[{'strategy':name,'parameters':{}} for name in DEFINITIONS], 'costs':COSTS}
    result=compare(data,series,request)
    assert len(result['results'])==72  # eight strategies + passive benchmark, two periods, four cases
    assert result['run_id']==compare(data,series,request)['run_id']
    for strategy in DEFINITIONS:
        rows=[r for r in result['results'] if r['strategy']==strategy and r['timeframe']=='1h' and r['window']=='full']
        assert Decimal(rows[0]['metrics']['fees'])==0
        assert Decimal(rows[1]['metrics']['fees'])>=0
        assert Decimal(rows[2]['metrics']['spread_cost'])>=0
    with pytest.raises(ValueError):Costs(**{**COSTS,'commission':0.1})


def test_queue_idempotency_owner_restart_and_no_account_mutation(tmp_path):
    cat,key=catalogue(tmp_path,2400)
    store=Store(tmp_path/'paper.db');research=Research(store,cat)
    request={'dataset_id':key,'start':START,'end':START+2400*HOUR,'timeframes':['1h'], 'variants':[VARIANT],'costs':COSTS}
    request_id=str(uuid4());job=research.enqueue('owner',request_id,request)
    assert research.enqueue('owner',request_id,request)['id']==job['id']
    with pytest.raises(Conflict):research.enqueue('owner',request_id,{**request,'end':request['end']-HOUR})
    with pytest.raises(KeyError):research.read(job['id'],'stranger')
    with store.connect() as db:
        db.execute("UPDATE gold_research_jobs SET status='RUNNING' WHERE id=?",(job['id'],))
    restarted=Research(Store(store.path),cat);restarted.recover()
    assert restarted.read(job['id'],'owner')['status']=='QUEUED'
    assert restarted.execute_one()
    done=restarted.read(job['id'],'owner')
    assert done['status']=='SUCCEEDED' and done['summary']
    assert restarted.result(job['id'],'owner',0)['fills']
    with store.connect() as db:
        assert db.execute('SELECT count(*) FROM accounts').fetchone()[0]==0
        assert db.execute('SELECT count(*) FROM events').fetchone()[0]==0
    with pytest.raises(KeyError):restarted.result(job['id'],'owner',999)


def test_api_freeze_ownership_bad_range_and_read_only_results(tmp_path):
    cat,key=catalogue(tmp_path,2400)
    app=create_app(tmp_path/'paper.db',history_path=cat.directory,background=False)
    body={'request_id':str(uuid4()),'dataset_id':key,'start':'2024-01-01','end':'2024-04-10',
          'timeframes':['1h'],'variants':[VARIANT],'costs':COSTS}
    with TestClient(app,base_url='https://testserver') as client,TestClient(app,base_url='https://testserver') as other:
        assert client.get('/api/v1/gold/research/catalogue').json()['datasets'][0]['identity']==key
        client.get('/api/v1/gold/research/runs')
        response=client.post('/api/v1/gold/research/runs',json=body)
        assert response.status_code==200,response.text
        job=response.json()
        assert client.post('/api/v1/gold/research/runs',json=body).json()['id']==job['id']
        other.get('/api/v1/gold/research/runs')
        assert other.get('/api/v1/gold/research/runs/'+job['id']).status_code==404
        assert client.post('/api/v1/gold/research/runs',json={**body,'end':'2025-01-01'}).status_code==422
        app.state.research.execute_one()
        assert client.get('/api/v1/gold/research/runs/'+job['id']).json()['status']=='SUCCEEDED'
        assert client.get('/api/v1/gold/research/runs/'+job['id']+'/manifest').json()['trading_enabled'] is False
        assert client.get('/api/v1/gold/research/runs/'+job['id']+'/members/0').json()['fills']


def test_concurrent_research_retries_and_failed_job_visibility(tmp_path):
    cat,key=catalogue(tmp_path)
    store=Store(tmp_path/'paper.db');research=Research(store,cat)
    request={'dataset_id':key,'start':START,'end':START+400*HOUR,'timeframes':['1h'],'variants':[VARIANT],'costs':COSTS}
    identity=str(uuid4())
    with ThreadPoolExecutor(max_workers=4) as pool:
        results=list(pool.map(lambda _:research.enqueue('owner',identity,request),range(4)))
    assert len(set(r['id'] for r in results))==1
    # Corrupted historical input cannot be turned into a successful empty result.
    cat._cache.clear()
    (cat.directory/(key+'.json.gz')).write_bytes(b'invalid snapshot')
    assert research.execute_one()
    assert research.read(results[0]['id'],'owner')['status']=='FAILED'
    with pytest.raises(Conflict):research.result(results[0]['id'],'owner')
