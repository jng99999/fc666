from copy import deepcopy
from datetime import datetime, timedelta
from decimal import Decimal, localcontext
from uuid import uuid4
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select, event
from sqlalchemy.orm import Session
from apps.api.main import create_app
from core.portfolio.valuation import capture, evaluate, Limits
from core.paper import streams
from core.market_data.storage import save_candles, save_instruments
from core.storage.models import CandleRecord, PaperStreamRecord, PaperWorkerRecord, InstrumentRecord
from scripts.verify_portfolio import verify
from tests.test_integration import database
from tests.test_paper_streams import setup, arrive
from tests.test_paper_accounts import historical
from tests.test_backtest import liquid, rules
from tests.test_paper_streams import request as stream_request


def fixture(engine, *, count=2, stop=True):
    ids=[]
    for _ in range(count):
        account,bars=setup(engine,[10,12,14,20,21])
        state=arrive(engine,account,bars,2)
        if stop:streams.command(engine,account['session_id'],state['revision'],'stop')
        ids.append(account['session_id'])
    save_candles(engine,[bars[3]])
    cutoff=bars[3].close_time+timedelta(seconds=5)
    with Session(engine) as db,db.begin():
        db.add(PaperWorkerRecord(worker_id=str(uuid4()),heartbeat_at=cutoff-timedelta(seconds=1)))
    return ids,bars,cutoff


def test_independent_hand_valuation_duplicate_assets_and_risk(database):
    engine,_,_=database;ids,bars,cutoff=fixture(engine)
    report=capture(engine,ids,Limits(max_asset_weight='.4',max_gross_weight='.5'),as_of=cutoff)
    assert report['status']=='COMPLETE' and report['price_as_of']==bars[3].close_time.isoformat()
    totals=report['totals']
    assert Decimal(totals['cash'])==Decimal('.016')
    assert Decimal(totals['market_value'])==Decimal('2857.120')
    assert Decimal(totals['equity'])==Decimal('2857.136')
    assert Decimal(totals['net_pnl'])==Decimal('857.136')
    assert Decimal(totals['unrealized_pnl'])==Decimal('857.136') and Decimal(totals['realized_pnl'])==0
    assert len(report['exposures'])==1 and report['exposures'][0]['base']=='BTC'
    assert Decimal(report['exposures'][0]['quantity'])==Decimal('142.856')
    assert {alert['code'] for alert in report['alerts']}=={'ASSET_WEIGHT','GROSS_WEIGHT'}
    assert all(row['ledger_clock']==bars[2].close_time.isoformat() for row in report['rows'])
    assert verify(report)=={'status':'COMPLETE','accounts':2,'alerts':2}


def test_no_mutation_of_ledger_orders_revision_controls(database):
    engine,_,_=database;ids,_,cutoff=fixture(engine)
    before=[streams.read(engine,id) for id in ids]
    from core.paper.accounts import controls
    events=[controls(engine,'streams',id) for id in ids]
    capture(engine,ids,Limits(),as_of=cutoff)
    assert [streams.read(engine,id) for id in ids]==before
    assert [controls(engine,'streams',id) for id in ids]==events


@pytest.mark.parametrize('failure,reason',[('stale','QUOTE_STALE'),('missing','QUOTE_MISSING'),('rules','RULES_CHANGED'),('accepted','ACCEPTED_DATA_CHANGED'),('ledger','ACCOUNT_INTEGRITY'),('blocked','ACCOUNT_BLOCKED')])
def test_fail_closed_without_partial_totals(database,failure,reason):
    engine,_,_=database;ids,bars,cutoff=fixture(engine)
    with Session(engine) as db,db.begin():
        if failure in ['stale','missing']:
            query=select(CandleRecord).where(CandleRecord.instrument_id==bars[0].instrument_id)
            for row in db.scalars(query):
                if failure=='missing' or row.open_time==bars[3].open_time:db.delete(row)
        elif failure=='rules':db.get(InstrumentRecord,bars[0].instrument_id).tick_size=Decimal('.02')
        elif failure=='accepted':db.get(CandleRecord,(bars[0].instrument_id,'1m',bars[0].open_time)).volume=Decimal('1')
        elif failure=='ledger':
            record=db.get(PaperStreamRecord,ids[0]);record.ledger={**record.ledger,'account':{**record.ledger['account'],'cash':'999'}}
        elif failure=='blocked':db.get(PaperStreamRecord,ids[0]).status='BLOCKED'
    report=capture(engine,ids,Limits(),as_of=cutoff)
    assert report['status']=='UNAVAILABLE' and report['totals'] is None and report['exposures']==[]
    # Missing accepted data is a stronger veto than missing pricing.
    if failure=='missing':reason='ACCEPTED_DATA_CHANGED'
    assert report['rows'][0]['reason']==reason
    assert verify(report)['status']=='UNAVAILABLE'


def test_missing_quote_without_missing_account_history(database):
    engine,_,_=database;ids,bars,cutoff=fixture(engine)
    report=capture(engine,ids,Limits(),as_of=cutoff)
    source=deepcopy(report['inputs'])
    for item in source['accounts']:item['quote']=None
    result=evaluate(source)
    assert result['rows'][0]['reason']=='QUOTE_MISSING' and result['totals'] is None


def test_active_lagging_blocks_and_caught_up_account_marks(database):
    engine,_,_=database;ids,bars,cutoff=fixture(engine,count=1,stop=False)
    report=capture(engine,ids,Limits(),as_of=cutoff)
    assert report['rows'][0]['reason']=='ACCOUNT_LAGGING' and report['totals'] is None
    streams.advance(engine,ids[0],observed_at=cutoff)
    complete=capture(engine,ids,Limits(),as_of=cutoff)
    assert complete['status']=='COMPLETE' and complete['rows'][0]['ledger_clock']==bars[3].close_time.isoformat()


def test_worker_offline_is_warning_and_cash_is_not_zeroed(database):
    engine,_,_=database;ids,bars,cutoff=fixture(engine,count=1,stop=False)
    streams.advance(engine,ids[0],observed_at=cutoff)
    report=capture(engine,ids,Limits(),as_of=cutoff+timedelta(seconds=11))
    assert report['status']=='COMPLETE'
    assert 'WORKER_UNAVAILABLE' in {alert['code'] for alert in report['alerts']}
    assert Decimal(report['totals']['equity'])>0


def test_future_quotes_excluded_and_future_export_rejected(database):
    engine,_,_=database;ids,bars,cutoff=fixture(engine)
    save_candles(engine,[bars[4]])
    report=capture(engine,ids,Limits(),as_of=cutoff)
    assert all(datetime.fromisoformat(row['quote']['close_time'])==bars[3].close_time for row in report['rows'])
    source=deepcopy(report['inputs']);source['accounts'][0]['quote']=bars[4].model_dump(mode='json')
    with pytest.raises(ValueError):evaluate(source)
    source=deepcopy(report['inputs']);source['as_of']=(bars[2].close_time-timedelta(seconds=1)).isoformat();source['price_as_of']=(bars[1].close_time).isoformat()
    with pytest.raises(ValueError):evaluate(source)


def test_snapshot_tamper_detection_and_clock_constraints(database):
    engine,_,_=database;ids,_,cutoff=fixture(engine)
    report=capture(engine,ids,Limits(),as_of=cutoff)
    for mutate in [lambda value:value['totals'].update(cash='1'),lambda value:value.update(snapshot_sha256='0'*64),lambda value:value['rows'].reverse(),lambda value:value['inputs']['accounts'][0]['record']['ledger']['account'].update(quantity='1')]:
        changed=deepcopy(report);mutate(changed)
        with pytest.raises(ValueError):verify(changed)
    changed=deepcopy(report['inputs']);changed['price_as_of']=cutoff.isoformat()
    with pytest.raises(ValueError):evaluate(changed)


def test_pnl_exact_with_fees_and_exposure_ratio(database):
    engine,_,_=database
    account,bars=setup(engine,[10,12,14,20],config={'initial_cash':'1000','period':2,'fee_rate':'.01','slippage_rate':'.001','allocation':'.5','participation':'.1'})
    state=arrive(engine,account,bars,2);streams.command(engine,account['session_id'],state['revision'],'stop');save_candles(engine,[bars[3]])
    report=capture(engine,[account['session_id']],Limits(),as_of=bars[3].close_time+timedelta(seconds=5))
    with localcontext() as ctx:
        ctx.prec=120
        row=report['rows'][0];total=report['totals']
        assert Decimal(total['equity'])==Decimal(row['cash'])+Decimal(row['quantity'])*20
        assert Decimal(total['net_pnl'])==Decimal(total['realized_pnl'])+Decimal(total['unrealized_pnl'])
        assert Decimal(total['fees'])>0
        assert Decimal(total['gross_weight'])==Decimal(total['market_value'])/Decimal(total['equity'])


def test_api_boundaries_historical_exclusion_and_zero_selection(database):
    engine,_,settings=database;old=historical(engine)
    with TestClient(create_app(settings)) as client:
        for body in [{'session_ids':[]},{'session_ids':[str(uuid4())]*2},{'session_ids':[str(uuid4()) for _ in range(9)]},{'session_ids':[str(uuid4())],'as_of':'2026-01-01T00:00:00Z'},{'session_ids':[str(uuid4())],'limits':{'max_asset_weight':.2}},{'session_ids':[str(uuid4())],'limits':{'max_gross_weight':'1.1'}}]:
            assert client.post('/api/v1/portfolio/valuation',json=body).status_code==422
        assert client.post('/api/v1/portfolio/valuation',json={'session_ids':[old['session_id']]}).status_code==404
        assert client.post('/api/v1/portfolio/valuation',json={'session_ids':[str(uuid4())]}).status_code==404


def test_slow_account_period_is_not_mistaken_for_lag_and_missing_price(database):
    engine,_,_=database
    source=liquid([10,12,14]);base=source[0].open_time
    bars=[bar.model_copy(update={'timeframe':'1h','open_time':base+timedelta(hours=index),'close_time':base+timedelta(hours=index+1)}) for index,bar in enumerate(source)]
    save_instruments(engine,[rules()]);save_candles(engine,bars[:2])
    account=streams.create(engine,stream_request(bars,timeframe='1h'),bars[:2],rules())
    mark=bars[1].close_time+timedelta(minutes=20);cutoff=mark+timedelta(seconds=5)
    missing=capture(engine,[account['session_id']],Limits(),as_of=cutoff)
    assert missing['rows'][0]['reason']=='QUOTE_MISSING'
    quote=source[-1].model_copy(update={'open_time':mark-timedelta(minutes=1),'close_time':mark})
    save_candles(engine,[quote])
    complete=capture(engine,[account['session_id']],Limits(),as_of=cutoff)
    assert complete['status']=='COMPLETE' and Decimal(complete['totals']['equity'])==1000


def test_multiple_assets_mark_together_without_summing_quantities(database):
    engine,_,_=database;ids,bars,cutoff=fixture(engine,count=1)
    eth=rules().model_copy(update={'instrument_id':'binance:spot:ETH-USDT','native_symbol':'ETHUSDT','base':'ETH'})
    eth_bars=[bar.model_copy(update={'instrument_id':eth.instrument_id}) for bar in bars]
    save_instruments(engine,[eth]);save_candles(engine,eth_bars[:3])
    account=streams.create(engine,stream_request(eth_bars,symbol='ETHUSDT'),eth_bars[:2],eth)
    streams.advance(engine,account['session_id'],observed_at=eth_bars[2].close_time+timedelta(seconds=1))
    state=streams.read(engine,account['session_id']);streams.command(engine,account['session_id'],state['revision'],'stop')
    save_candles(engine,[eth_bars[3]])
    report=capture(engine,[*ids,account['session_id']],Limits(),as_of=cutoff)
    assert report['status']=='COMPLETE' and [item['base'] for item in report['exposures']]==['BTC','ETH']
    assert all(Decimal(item['quantity'])==Decimal('71.428') for item in report['exposures'])
    assert all(Decimal(item['market_value'])==Decimal('1428.56') for item in report['exposures'])


def test_repeatable_read_prevents_mixed_rule_and_price_versions(database):
    engine,_,_=database;ids,bars,cutoff=fixture(engine)
    changed=[]
    def revise(connection,cursor,statement,parameters,context,executemany):
        if changed or not statement.lstrip().startswith('SELECT') or 'FROM paper_streams' not in statement:return
        changed.append(True)
        with Session(engine) as db,db.begin():
            db.get(InstrumentRecord,bars[0].instrument_id).tick_size=Decimal('.02')
            quote=db.get(CandleRecord,(bars[0].instrument_id,'1m',bars[3].open_time));quote.close=Decimal('21')
    event.listen(engine,'after_cursor_execute',revise)
    try:report=capture(engine,ids,Limits(),as_of=cutoff)
    finally:event.remove(engine,'after_cursor_execute',revise)
    assert changed and report['status']=='COMPLETE'
    assert Decimal(report['totals']['equity'])==Decimal('2857.136')
    later=capture(engine,ids,Limits(),as_of=cutoff)
    assert later['status']=='UNAVAILABLE' and later['rows'][0]['reason']=='RULES_CHANGED'


def test_duplicate_asset_cannot_have_different_quotes_or_future_observation(database):
    engine,_,_=database;ids,_,cutoff=fixture(engine)
    report=capture(engine,ids,Limits(),as_of=cutoff)
    inputs=deepcopy(report['inputs']);inputs['accounts'][0]['quote']['close']='19'
    with pytest.raises(ValueError):evaluate(inputs)
    inputs=deepcopy(report['inputs']);inputs['accounts'][0]['record']['observations'][0]['observed_at']=(cutoff+timedelta(seconds=1)).isoformat()
    with pytest.raises(ValueError):evaluate(inputs)


def test_damaged_existing_snapshot_is_not_reported_as_missing_account(database):
    engine,_,settings=database;ids,_,_=fixture(engine,count=1)
    with Session(engine) as db,db.begin():
        row=db.get(PaperStreamRecord,ids[0]);row.snapshot={key:value for key,value in row.snapshot.items() if key!='request'}
    with TestClient(create_app(settings)) as client:
        response=client.post('/api/v1/portfolio/valuation',json={'session_ids':ids})
        assert response.status_code==409 and 'cannot be verified' in response.json()['detail']
