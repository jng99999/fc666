"""Bounded deterministic Spot long/flat research simulation; no exchange execution."""
from decimal import Decimal, localcontext, ROUND_FLOOR, ROUND_CEILING
import hashlib
import json
from pydantic import BaseModel, ConfigDict, Field, field_validator
from core.models import Candle, Instrument
from core.indicators.engine import IndicatorEngine
from core.strategy.contracts import StrategyContext
from core.strategy.registry import resolve

VERSION = 'spot-next-open-v2'
from core.backtest.analytics import analyze

class BacktestConfig(BaseModel):
    model_config=ConfigDict(frozen=True,extra='forbid')
    initial_cash: Decimal=Field(default=Decimal('10000'),gt=0,le=1000000000,max_digits=38,decimal_places=18)
    fee_rate: Decimal=Field(default=Decimal('.001'),ge=0,le=Decimal('.1'),max_digits=38,decimal_places=18)
    slippage_rate: Decimal=Field(default=Decimal('.0005'),ge=0,le=Decimal('.1'),max_digits=38,decimal_places=18)
    allocation: Decimal=Field(default=Decimal('.5'),gt=0,le=1,max_digits=38,decimal_places=18)
    participation: Decimal=Field(default=Decimal('.01'),gt=0,le=Decimal('.1'),max_digits=38,decimal_places=18)
    period: int=Field(default=20,ge=2,le=500,strict=True)

    @field_validator('initial_cash','fee_rate','slippage_rate','allocation','participation',mode='before')
    @classmethod
    def exact(cls,value):
        if isinstance(value,(float,bool)): raise ValueError('Use decimal strings for research money')
        return value


def digest(value):
    return hashlib.sha256(json.dumps(value,sort_keys=True,separators=(',',':'),ensure_ascii=True).encode()).hexdigest()

def rounded(value, step, mode):
    return (value/step).to_integral_value(rounding=mode)*step


def simulate(bars: list[Candle], instrument: Instrument, config: BacktestConfig, *, checkpoint=None):
    if instrument.market_type!='SPOT': raise ValueError('Only Spot long/flat is implemented')
    if not 2<=len(bars)<=1000: raise ValueError('Require 2..1000 finalized bars')
    for i,bar in enumerate(bars):
        if not bar.is_closed or bar.instrument_id!=instrument.instrument_id: raise ValueError('Finalized matching instrument required')
        if i and (bar.timeframe!=bars[i-1].timeframe or bar.open_time!=bars[i-1].close_time): raise ValueError('Contiguous ordered series required')
    manifest={'analytics_version':'equity-trades-v1','regime_rule':'er-atr-v1','engine_version':VERSION,'strategy':'ema_long_flat_v1','config':config.model_dump(mode='json'),
              'instrument':instrument.model_dump(mode='json'),'data_sha256':digest([b.model_dump(mode='json') for b in bars]),
              'bars':len(bars),'start':bars[0].open_time.isoformat(),'end':bars[-1].close_time.isoformat(),
              'assumptions':['closed-bar decision; next-bar open execution; no same-close fill',
                             'adverse slippage plus conservative tick rounding; quote-currency fees',
                             'IOC partial fill capacity = prior finalized base volume * participation; not actual open liquidity',
                             'Spot long/flat market only; no leverage/short/limit/stop/funding; no terminal forced liquidation',
                             'current instrument rules snapshot; historical rule changes not reconstructed']}
    with localcontext() as ctx:
        ctx.prec=60
        cash=config.initial_cash;quantity=Decimal(0);cost=Decimal(0);fees=Decimal(0);realized=Decimal(0)
        peak=config.initial_cash;drawdown=Decimal(0)
        pending=None;fills=[];orders=[];equity=[];signals=[];market_states=[];indicator=IndicatorEngine(period=config.period);strategy=resolve('ema_long_flat_v1').create({'period':config.period})[0]
        for index,bar in enumerate(bars):
            if checkpoint is not None and index%25==0:checkpoint(index,len(bars))
            if pending is not None:
                side='BUY' if pending.target=='LONG' and quantity==0 else 'SELL' if pending.target=='FLAT' and quantity>0 else None
                if side:
                    if pending.available_at>bar.open_time: raise ValueError('Signal is unavailable at execution')
                    price=rounded(bar.open*(1+config.slippage_rate if side=='BUY' else 1-config.slippage_rate),instrument.tick_size,ROUND_CEILING if side=='BUY' else ROUND_FLOOR)
                    order={'side':side,'decision_at':pending.available_at.isoformat(),'execution_at':bar.open_time.isoformat()}
                    if price<=0:
                        orders.append({**order,'status':'REJECTED','reason':'nonpositive rounded price'});pending=None
                    else:
                        wanted=cash*config.allocation/(price*(1+config.fee_rate)) if side=='BUY' else quantity
                        capacity=bars[index-1].volume*config.participation
                        size=rounded(min(wanted,capacity),instrument.quantity_step,ROUND_FLOOR)
                        wanted_step=rounded(wanted,instrument.quantity_step,ROUND_FLOOR)
                        if size<instrument.min_quantity or size<=0 or size*price<instrument.min_notional:
                            orders.append({**order,'status':'REJECTED','reason':'capacity/tick/step/minimum constraints'})
                        else:
                            notional=size*price;fee=notional*config.fee_rate;fill_pnl=Decimal(0)
                            if side=='BUY':
                                if notional+fee>cash: raise ArithmeticError('Cash invariant violated')
                                cash-=notional+fee;quantity+=size;cost+=notional+fee
                            else:
                                basis=cost*size/quantity;cash+=notional-fee;quantity-=size;cost-=basis;fill_pnl=notional-fee-basis;realized+=fill_pnl
                            fees+=fee
                            if cash<0 or quantity<0: raise ArithmeticError('Negative cash or inventory')
                            fills.append({**order,'quantity':str(size),'price':str(price),'fee':str(fee),'realized_pnl':str(fill_pnl),'slippage_cost':str(size*(price-bar.open if side=='BUY' else bar.open-price)),'reference_open':str(bar.open),'cash_after':str(cash),'quantity_after':str(quantity),'simulated':True})
                            orders.append({**order,'status':'FILLED' if size>=wanted_step else 'PARTIAL_CANCELLED','quantity':str(size)})
            result=indicator.push(bar)
            market_states.append(result['regime'])
            pending=strategy.decide(StrategyContext(bar.close_time,bar.close,result['values']['ema']))
            if pending:signals.append({'target':pending.target,'available_at':pending.available_at.isoformat()})
            value=cash+quantity*bar.close;peak=max(peak,value);drawdown=max(drawdown,(peak-value)/peak)
            equity.append({'as_of':bar.close_time.isoformat(),'cash':str(cash),'quantity':str(quantity),'equity':str(value)})
        if checkpoint is not None:checkpoint(len(bars),len(bars))
        final=cash+quantity*bars[-1].close
        unrealized=quantity*bars[-1].close-cost
        if abs((final-config.initial_cash)-(realized+unrealized))>Decimal('1e-40'): raise ArithmeticError('PnL conservation violated')
        analysis=analyze(equity,fills,config.initial_cash,int((bars[0].close_time-bars[0].open_time).total_seconds()))
        return {'analysis':analysis,'market_states':market_states,'run_id':digest(manifest),'manifest':manifest,'dataset':[b.model_dump(mode='json') for b in bars],'fills':fills,'orders':orders,'signals':signals,'equity':equity,
                'metrics':{'final_equity':str(final),'net_pnl':str(final-config.initial_cash),'realized_pnl':str(realized),'unrealized_pnl':str(unrealized),'fees':str(fees),'total_return':str(final/config.initial_cash-1),'max_drawdown':str(drawdown),'fill_count':len(fills),'open_quantity':str(quantity),'slippage_cost':str(sum((Decimal(f['slippage_cost']) for f in fills),Decimal(0)))},
                'pending_final_signal':None if pending is None else {'target':pending.target,'available_at':pending.available_at.isoformat(),'status':'NO_NEXT_BAR'},'trading_enabled':False}
