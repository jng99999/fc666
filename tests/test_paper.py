from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from decimal import Decimal
import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from apps.api.main import create_app
from apps.api.paper import PaperRequest
from core.paper.ledger import evaluate, RiskLimits
from core.paper import sessions
from core.storage.models import PaperSessionRecord as Paper, InstrumentRecord
from core.backtest.spot import simulate, BacktestConfig
from core.market_data.storage import save_candles
from tests.test_backtest import liquid, rules, cfg
from tests.test_integration import database
from tests.test_market_integration import seed
from tests.test_replay import request as replay_request


def request(**updates):
    base = replay_request()
    base.pop('period')
    return PaperRequest.model_validate({**base, 'config':cfg().model_dump(mode='json'), 'risk':{'max_order_quote':'10000','max_position_quote':'10000','max_drawdown':'1'}, **updates}).model_dump(mode='json')


def run(values, config=None, limits=None, halt_at=None):
    return evaluate(liquid(values), rules(), config or cfg(), limits or RiskLimits(max_order_quote='10000',max_position_quote='10000',max_drawdown='1'), strategy_id='ema_long_flat_v1', parameters={'period':2},snapshot_sha256='a'*64,halt_at=halt_at)


def test_hand_calculated_cash_inventory_and_pending_next_bar():
    result = run([10,12,14,10,9])
    buy, sell = result['fills']
    assert buy['quantity']=='71.428' and buy['price']=='14'
    assert sell['price']=='9' and sell['quantity']=='71.428'
    assert Decimal(result['account']['cash'])==Decimal('642.860')
    assert Decimal(result['account']['quantity'])==0
    assert Decimal(result['account']['realized_pnl'])==Decimal('-357.140')
    assert result['pending']['status']=='AWAIT_NEXT_REACHED_BAR'
    assert run([10,12])['fills']==[]
    assert run([])['account']['equity']=='1000'


def test_backtest_reference_fees_rounding_cash_and_fills():
    config=BacktestConfig(initial_cash='1000',period=2,fee_rate='.01',slippage_rate='.001',allocation='1',participation='.1')
    values=[10,12,14,10,9,15,16,9,8]
    paper=run(values,config=config)
    reference=simulate(liquid(values),rules(),config)
    assert paper['equity']==reference['equity'] and paper['signals']==reference['signals']
    assert [{key:fill[key] for key in reference['fills'][0] if key!='slippage_cost'} for fill in paper['fills']]==[{key:value for key,value in fill.items() if key!='slippage_cost'} for fill in reference['fills']]
    account=paper['account']
    assert abs(Decimal(account['net_pnl'])-Decimal(account['realized_pnl'])-Decimal(account['unrealized_pnl']))<Decimal('1e-24')
    assert Decimal(account['fees'])==sum(Decimal(fill['fee']) for fill in paper['fills'])
    assert all(Decimal(point['cash'])>=0 and Decimal(point['quantity'])>=0 for point in paper['equity'])


@pytest.mark.parametrize('limits,reason',[
    (RiskLimits(max_order_quote='100',max_position_quote='10000',max_drawdown='1'),'MAX_ORDER_QUOTE'),
    (RiskLimits(max_order_quote='10000',max_position_quote='100',max_drawdown='1'),'MAX_POSITION_QUOTE')])
def test_entry_notional_limits_reject_without_balance_changes(limits,reason):
    result=run([10,12,14],limits=limits)
    assert result['orders'][0]['reason']==reason and result['fills']==[]
    assert result['account']['cash']=='1000' and result['account']['quantity']=='0'


def test_manual_halt_blocks_pending_entry_and_allows_inventory_exit():
    before_entry=run([10,12,14,10,9],halt_at=2)
    assert before_entry['fills']==[] and before_entry['orders'][0]['reason']=='MANUAL_HALT'
    after_entry=run([10,12,14,10,9,15,16],halt_at=3)
    assert [fill['side'] for fill in after_entry['fills']]==['BUY','SELL']
    assert after_entry['risk']['entry_halted'] and after_entry['orders'][-1]['reason']=='MANUAL_HALT'


def test_drawdown_latch_blocks_reentry_but_allows_selling_and_survives_recovery():
    result=run([10,12,14,10,9,15,16],limits=RiskLimits(max_order_quote='10000',max_position_quote='10000',max_drawdown='.2'))
    assert result['risk']['drawdown_halted']
    assert result['risk']['events'][0]['reason']=='MAX_DRAWDOWN'
    assert [fill['side'] for fill in result['fills']]==['BUY','SELL']
    assert result['orders'][-1]['reason']=='MAX_DRAWDOWN'


def test_prior_volume_only_partial_ioc_and_no_same_close_fill():
    bars=liquid([10,12,14,15]);bars[1]=bars[1].model_copy(update={'volume':Decimal('1')})
    kwargs=dict(strategy_id='ema_long_flat_v1',parameters={'period':2},snapshot_sha256='a'*64)
    result=evaluate(bars,rules(),cfg(),RiskLimits(),**kwargs)
    assert result['fills'][0]['quantity']=='0.1' and result['orders'][0]['status']=='PARTIAL_CANCELLED'
    assert result['fills'][0]['execution_at']==bars[2].open_time.isoformat()
    bars[2]=bars[2].model_copy(update={'volume':Decimal('0')})
    assert evaluate(bars,rules(),cfg(),RiskLimits(),**kwargs)['fills'][0]==result['fills'][0]
    zero=[bar.model_copy(update={'volume':Decimal('0')}) for bar in bars]
    assert evaluate(zero,rules(),cfg(),RiskLimits(),**kwargs)['orders'][0]['reason']=='MARKET_RULES_OR_CAPACITY'


@pytest.mark.parametrize('updates',[
    {'max_order_quote':True},{'max_position_quote':.1},{'max_drawdown':'0'},
    {'max_drawdown':'1.01'},{'max_order_quote':'NaN'},{'unexpected':'1'},
])
def test_strict_risk_parameters(updates):
    with pytest.raises(ValueError):RiskLimits.model_validate(updates)


def test_atomic_cursor_ledger_cas_reset_and_repeated_halt(database):
    engine,_,_=database
    created=sessions.create(engine,request(),liquid([10,12,14,10,9,15,16]),rules())
    def advance(_):
        try:return sessions.command(engine,created['session_id'],0,'step',3)['cursor']
        except sessions.Conflict:return 'conflict'
    with ThreadPoolExecutor(max_workers=4) as pool:observed=list(pool.map(advance,range(4)))
    assert observed.count(3)==1 and observed.count('conflict')==3
    state=sessions.read(engine,created['session_id']);assert len(state['fills'])==1
    halted=sessions.command(engine,created['session_id'],1,'halt')
    assert halted['risk']['manual_halt_at']==3 and halted['revision']==2
    assert sessions.command(engine,created['session_id'],2,'halt')==halted
    ended=sessions.command(engine,created['session_id'],2,'step',10)
    assert ended['status']=='ENDED' and len(ended['fills'])==2
    assert ended['pending']['status']=='NO_NEXT_BAR'
    assert sessions.command(engine,created['session_id'],3,'step')==ended
    reset=sessions.command(engine,created['session_id'],3,'reset')
    assert reset['cursor']==0 and reset['fills']==[] and not reset['risk']['entry_halted']
    repeated=sessions.command(engine,created['session_id'],4,'step',3)
    assert repeated['fills']==state['fills']
    with Session(engine) as session:
        persisted=session.get(Paper,created['session_id'])
        assert persisted.ledger['account']==repeated['account'] and persisted.cursor==3


def test_step_batch_matches_incremental_and_future_does_not_change_balances(database):
    engine,_,_=database
    a=sessions.create(engine,request(),liquid([10,12,14,10,9,15,16]),rules())
    b=sessions.create(engine,request(),liquid([10,12,14,10,9,150,160]),rules())
    for revision in range(5):first=sessions.command(engine,a['session_id'],revision,'step')
    second=sessions.command(engine,b['session_id'],0,'step',5)
    assert first['account']==second['account'] and first['equity']==second['equity']
    assert [{k:v for k,v in fill.items() if k!='order_id'} for fill in first['fills']]==[{k:v for k,v in fill.items() if k!='order_id'} for fill in second['fills']]
    reset=sessions.command(engine,a['session_id'],5,'reset')
    assert sessions.command(engine,a['session_id'],reset['revision'],'step',5)['fills']==first['fills']


@pytest.mark.parametrize('field',['snapshot','ledger'])
def test_corruption_rejects_command_without_cursor_or_cash_mutation(database,field):
    engine,_,_=database;created=sessions.create(engine,request(),liquid([10,12,14]),rules())
    with Session(engine) as session,session.begin():
        record=session.get(Paper,created['session_id']);changed=deepcopy(getattr(record,field))
        if field=='snapshot':changed['dataset'][2]['close']='99'
        else:changed['account']['cash']='99999'
        setattr(record,field,changed)
    with pytest.raises(ValueError,match='integrity'):sessions.command(engine,created['session_id'],0,'step')
    with Session(engine) as session:
        record=session.get(Paper,created['session_id']);assert record.cursor==record.revision==0


def test_paper_api_persistence_frozen_rules_sma_and_input_errors(database):
    engine,_,settings=database;seed(engine);save_candles(engine,liquid([10,12,14,10,9,15,16]))
    body=request(strategy='sma_long_flat_v1',parameters={'fast':2,'slow':3})
    with TestClient(create_app(settings)) as client:
        response=client.post('/api/v1/paper/sessions',json=body);assert response.status_code==201
        state=response.json();url='/api/v1/paper/sessions/'+state['session_id']
        state=client.post(url+'/command',json={'expected_revision':0,'action':'step','count':4}).json()
        assert len(state['fills'])==1 and state['mode']=='HISTORICAL_PAPER'
        assert 'snapshot' not in state and 'dataset' not in state and state['trading_enabled'] is False
        assert all(fill['execution_at']<=state['clock'] for fill in state['fills'])
        assert client.post(url+'/command',json={'expected_revision':0,'action':'step'}).status_code==409
        for bad in [{'expected_revision':True,'action':'step'},{'expected_revision':1,'action':'halt','count':2},{'expected_revision':1,'action':'reset','count':2},{'expected_revision':1,'action':'buy'}]:
            assert client.post(url+'/command',json=bad).status_code==422
        assert client.post('/api/v1/paper/sessions',json={**body,'risk':{'max_order_quote':.1}}).status_code==422
    with Session(engine) as session,session.begin():session.get(InstrumentRecord,rules().instrument_id).tick_size='10'
    with TestClient(create_app(settings)) as client:
        assert client.get(url).json()==state
        assert client.get('/api/v1/paper/sessions/00000000-0000-0000-0000-000000000000').status_code==404
    with engine.connect() as connection:
        with pytest.raises(IntegrityError):connection.execute(text('UPDATE paper_sessions SET halt_at=5 WHERE session_id=:id'),{'id':state['session_id']})


def test_offline_paper_prefix_rejects_cash_fill_risk_and_future_tampering(database):
    from scripts.verify_paper_prefix import verify
    engine,_,_=database;created=sessions.create(engine,request(),liquid([10,12,14,10,9,15,16]),rules())
    assert verify(created)=={'bars':0,'orders':0,'fills':0}
    state=sessions.command(engine,created['session_id'],0,'step',4)
    assert verify(state)=={'bars':4,'orders':1,'fills':1}
    for field in ['cash','fill','risk','clock','candles']:
        changed=deepcopy(state)
        if field=='cash':changed['account']['cash']='9999'
        elif field=='fill':changed['fills'][0]['price']='99'
        elif field=='risk':changed['manifest']['risk']['max_order_quote']='1'
        elif field=='clock':changed['clock']=changed['candles'][0]['close_time']
        else:changed['candles'].append(changed['candles'][-1])
        with pytest.raises(ValueError):verify(changed)
    halted=sessions.command(engine,created['session_id'],1,'halt')
    assert verify(halted)=={'bars':4,'orders':1,'fills':1}
