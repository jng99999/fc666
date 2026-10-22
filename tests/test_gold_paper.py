from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal
import json
import sqlite3
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from apps.api.gold_service import create_app
from core.gold.ledger import Conflict, Store, canonical, digest
from core.gold.market import Quote, SOURCE, analysis, parse, sampled_bars

BASE=1792500000000//60000*60000
CONFIG={'mode':'MANUAL','initial_cash':'10000','allocation':'0.1','max_spread':'2', 'slippage':'0.05','commission':'0.10'}


def quote(stamp=BASE, bid='2000', ask='2000.5'):
    return Quote(stamp,Decimal(bid),Decimal(ask))


@pytest.fixture
def store(tmp_path):
    return Store(tmp_path/'gold.sqlite3')


def create(store, owner='owner', mode='MANUAL'):
    return store.create(owner,{**CONFIG,'mode':mode},str(uuid4()),BASE-1000)


def body(state,action='BUY',ounces='0.1'):
    return {'request_id':str(uuid4()),'expected_revision':state['revision'],'action':action,'ounces':ounces}


def test_real_bid_ask_contract_and_profile():
    source=[{'topo':{'platform':'AT','server':'AT'},'ts':BASE,
             'spreadProfilePrices':[{'spreadProfile':'standard','bid':2000.0,'ask':2000.5}]}]
    assert parse(source,BASE).json()['source']==SOURCE
    for invalid in ([{**source[0],'topo':{'platform':'Other','server':'AT'}}],source*2):
        with pytest.raises(ValueError):parse(invalid,BASE)


@pytest.mark.parametrize('q,now',[ (quote(BASE-60001),BASE),(quote(BASE+5001),BASE),
                                 (quote(bid='NaN'),BASE),(quote(bid='2001'),BASE)])
def test_stale_future_nonfinite_crossed_quotes_fail(q,now):
    with pytest.raises(ValueError):q.validate(now)


def test_conflicting_timestamp_rejected(store):
    store.put_quote(quote(),BASE)
    store.put_quote(quote(),BASE)
    with pytest.raises(ValueError):store.put_quote(quote(ask='2000.6'),BASE)
    with pytest.raises(ValueError):store.put_quote(quote(BASE-1000),BASE)


def test_sampled_bar_coverage_and_no_fabricated_gap():
    quotes=[quote(BASE+i*10000) for i in range(6)]
    bars=sampled_bars(quotes,BASE+60000)
    assert len(bars)==1 and 'volume' not in bars[0]
    assert sampled_bars(quotes,BASE+59999)==[]
    assert sampled_bars(quotes[2:],BASE+60000)[0]['samples']==4
    assert sampled_bars(quotes[3:],BASE+60000)==[]
    assert analysis(bars)['status']=='WARMUP'


def test_buy_sell_conservation_spread_and_costs(store):
    a=create(store)
    buy=store.command(a['id'],'owner',body(a),BASE,quote())
    assert Decimal(buy['quantity'])==Decimal('.1')
    assert Decimal(buy['cash'])==Decimal('9799.935')
    sold=store.command(a['id'],'owner',body(buy,'SELL'),BASE,quote())
    assert Decimal(sold['quantity'])==0
    assert Decimal(sold['cash'])==Decimal('9999.92')
    assert Decimal(sold['realized'])==Decimal('-.08')
    assert Decimal(sold['fees'])==Decimal('.02')


def test_oversell_cash_and_spread_rollback(store):
    a=create(store)
    for command,q in ((body(a,'SELL'),quote()),(body(a,ounces='10'),quote()),(body(a),quote(ask='2005'))):
        with pytest.raises(ValueError):store.command(a['id'],'owner',command,BASE,q)
    assert store.read(a['id'],'owner')['revision']==1


def test_ownership_revision_and_idempotency(store):
    a=create(store);command=body(a)
    with pytest.raises(KeyError):store.read(a['id'],'another-owner')
    with pytest.raises(KeyError):store.command(a['id'],'another-owner',command,BASE,quote())
    first=store.command(a['id'],'owner',command,BASE,quote())
    assert store.command(a['id'],'owner',command,BASE,quote())==first
    with pytest.raises(Conflict):store.command(a['id'],'owner',{**command,'ounces':'.2'},BASE,quote())
    with pytest.raises(Conflict):store.command(a['id'],'owner',body(a),BASE,quote())


def test_concurrent_same_request_only_one_fill(store):
    a=create(store);command=body(a)
    with ThreadPoolExecutor(max_workers=4) as pool:
        results=list(pool.map(lambda _:store.command(a['id'],'owner',command,BASE,quote()),range(4)))
    assert all(r['revision']==2 for r in results)
    assert len(store.read(a['id'],'owner')['events'])==2


def test_create_idempotency_and_pause_preserves_ledger(store):
    key=str(uuid4());a=store.create('owner',CONFIG,key,BASE)
    assert store.create('owner',CONFIG,key,BASE)['id']==a['id']
    bought=store.command(a['id'],'owner',body(a),BASE,quote())
    paused=store.command(a['id'],'owner',body(bought,'PAUSE',None),BASE)
    assert paused['quantity']==bought['quantity'] and paused['cash']==bought['cash']
    with pytest.raises(Conflict):store.command(a['id'],'owner',body(paused),BASE,quote())
    reopened=Store(store.path)
    assert reopened.read(a['id'],'owner')['status']=='PAUSED'
    with reopened.connect() as db:
        with pytest.raises(sqlite3.IntegrityError):db.execute('DELETE FROM events WHERE account_id=?',(a['id'],))


def test_automatic_waits_for_later_quote_and_survives_restart(store):
    a=create(store,mode='SMA20')
    for minute in range(20):
        for sample in range(6):
            stamp=BASE+minute*60000+sample*10000
            store.put_quote(quote(stamp,str(2000+minute),str(2000.5+minute)),stamp)
    observed=quote(BASE+20*60000+5000,'2020','2020.5')
    store.put_quote(observed,observed.ts);store.advance(observed,observed.ts)
    pending=store.read(a['id'],'owner')
    assert pending['intent'] and pending['quantity']=='0'
    reopened=Store(store.path);reopened.advance(observed,observed.ts)
    assert reopened.read(a['id'],'owner')['quantity']=='0'
    later=quote(observed.ts+10000,'2021','2021.5')
    reopened.put_quote(later,later.ts);reopened.advance(later,later.ts)
    filled=reopened.read(a['id'],'owner')
    assert Decimal(filled['quantity'])>0 and filled['intent'] is None
    event=next(e for e in filled['events'] if e['kind']=='AUTO_RESULT')
    assert event['details']['quote_ts']==later.ts
    reopened.advance(later,later.ts)
    assert reopened.read(a['id'],'owner')['revision']==filled['revision']


def test_expired_automatic_intent_not_backfilled(store):
    a=create(store,mode='SMA20')
    with store.connect() as db:
        state=store._read(db,a['id'])
        state['intent']={'target':'LONG','observed_at':BASE-60001,'bar':BASE-60000,'budget':'1000'}
        store._write(db,state,'setup',digest('setup'),'DECISION',BASE,{})
    store.advance(quote(),BASE)
    state=store.read(a['id'],'owner')
    assert state['quantity']=='0' and state['intent'] is None
    assert state['events'][0]['details']['reason'].startswith('expired')


def test_api_cookie_ownership_and_validation(tmp_path):
    app=create_app(tmp_path/'api.db',background=False,secure=True,clock=lambda:BASE)
    with TestClient(app,base_url='https://testserver') as first, TestClient(app,base_url='https://testserver') as second:
        response=first.get('/api/v1/gold/accounts')
        assert 'HttpOnly' in response.headers['set-cookie'] and 'Secure' in response.headers['set-cookie']
        created=first.post('/api/v1/gold/accounts',json={**CONFIG,'request_id':str(uuid4())})
        assert created.status_code==200
        a=created.json();app.state.store.put_quote(quote(),BASE)
        assert second.get(f"/api/v1/gold/accounts/{a['id']}").status_code==401
        second.get('/api/v1/gold/accounts')
        assert second.get(f"/api/v1/gold/accounts/{a['id']}").status_code==404
        assert first.post(f"/api/v1/gold/accounts/{a['id']}/command",json=body(a)).status_code==200
        for value in (None,'NaN','abc','0','-1','0.001'):
            current=first.get(f"/api/v1/gold/accounts/{a['id']}").json()
            assert first.post(f"/api/v1/gold/accounts/{a['id']}/command",json=body(current,ounces=value)).status_code==422
        assert first.post('/api/v1/gold/accounts',json={**CONFIG,'request_id':str(uuid4()),'live_trading':True}).status_code==422


def test_incremental_gold_account_continues_beyond_1000_bars(store):
    a=create(store,mode='SMA20')
    for minute in range(1022):
        for offset in (5000,25000,50000):
            stamp=BASE+minute*60000+offset
            q=quote(stamp,str(2000+minute/100),str(2000.5+minute/100))
            store.put_quote(q,stamp)
        stamp=BASE+(minute+1)*60000+5000
        q=quote(stamp,str(2000+(minute+1)/100),str(2000.5+(minute+1)/100))
        store.put_quote(q,stamp)
        store.advance(q,stamp)
    result=store.read(a['id'],'owner')
    assert result['status']=='ACTIVE'
    assert result['last_bar']==BASE+1022*60000
    assert result['revision']>1000
    assert len(result['events'])==100
    assert store.read(a['id'],'owner',result['events'][-1]['seq'])['events']
