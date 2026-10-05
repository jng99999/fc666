"""Explicit return conventions; Decimal for trade PnL, floats only for ratios."""
from datetime import datetime
from decimal import Decimal,localcontext
import math
import statistics

SECONDS_PER_YEAR=365*24*60*60  # Crypto 365-day continuous-market convention.


def analyze(equity, fills, initial_cash:Decimal, seconds:int):
    with localcontext() as ctx:
        ctx.prec=60
        return _analyze(equity,fills,initial_cash,seconds)


def _analyze(equity, fills, initial_cash:Decimal, seconds:int):
    if len(equity)<2 or seconds<=0: raise ValueError('At least two fixed-frequency equity observations required')
    reasons={}
    values=[initial_cash,*[Decimal(row['equity']) for row in equity]]
    if any(value<=0 for value in values): raise ValueError('Positive equity required for return ratios')
    returns=[float(after/before-1) for before,after in zip(values,values[1:])]
    samples=len(returns);scale=SECONDS_PER_YEAR/seconds
    mean=statistics.fmean(returns)
    sigma=statistics.stdev(returns)
    downside=math.sqrt(statistics.fmean(min(value,0)**2 for value in returns))
    def ratio(name,denominator):
        if denominator==0:
            reasons[name]='zero return dispersion' if name=='sharpe' else 'no downside returns'
            return None
        return mean/denominator*math.sqrt(scale)
    sharpe,sortino=ratio('sharpe',sigma),ratio('sortino',downside)
    log_growth=math.log(float(values[-1]/initial_cash))*scale/samples
    if log_growth>700:
        annualized=None;reasons['annualized_return']='annualization exceeds finite numeric range'
    else: annualized=math.expm1(log_growth)
    peak=initial_cash;maximum=Decimal(0);drawdowns=[]
    for row,value in zip(equity,values[1:]):
        peak=max(peak,value);dd=(peak-value)/peak;maximum=max(maximum,dd)
        drawdowns.append({'as_of':row['as_of'],'drawdown':str(dd)})
    calmar=None if annualized is None or maximum==0 else annualized/float(maximum)
    if calmar is not None and not math.isfinite(calmar):
        calmar=None;reasons['calmar']='calmar exceeds finite numeric range'
    if maximum==0:reasons['calmar']='zero maximum drawdown'
    elif annualized is None:reasons['calmar']='annualized return unavailable'
    if samples*seconds<SECONDS_PER_YEAR:
        warnings=['Annualized ratios extrapolate less than one year of observations; not a performance forecast.']
    else: warnings=[]
    closed=[];active=None
    for fill in fills:
        if fill['side']=='BUY':
            if active is not None:raise ValueError('Long/flat trade accounting does not support pyramiding')
            active={'entry_at':fill['execution_at'],'entry_quantity':fill['quantity'],'net_pnl':Decimal(0),'fees':Decimal(fill['fee']),'exit_count':0}
        else:
            if active is None:raise ValueError('Exit without an entry')
            active['net_pnl']+=Decimal(fill['realized_pnl']);active['fees']+=Decimal(fill['fee']);active['exit_count']+=1
            if Decimal(fill['quantity_after'])==0:
                held=(datetime.fromisoformat(fill['execution_at'])-datetime.fromisoformat(active['entry_at'])).total_seconds()
                closed.append({**active,'net_pnl':str(active['net_pnl']),'fees':str(active['fees']),'exit_at':fill['execution_at'],'holding_seconds':held})
                active=None
    profits=[Decimal(trade['net_pnl']) for trade in closed];wins=[v for v in profits if v>0];losses=[v for v in profits if v<0]
    profit_sum=sum(wins,Decimal(0));loss_sum=-sum(losses,Decimal(0))
    count=len(closed)
    if not count:reasons['trade_statistics']='no fully closed round trips; open/partial exits excluded'
    if not loss_sum:reasons['profit_factor']='no losing closed round trips'
    result={'return_samples':samples,'annualized_return':annualized,'sharpe':sharpe,'sortino':sortino,'calmar':calmar,
            'closed_trade_count':count,'win_rate':None if not count else len(wins)/count,
            'profit_factor':None if not loss_sum else str(profit_sum/loss_sum),
            'expectancy':None if not count else str(sum(profits,Decimal(0))/count),
            'average_win':None if not wins else str(profit_sum/len(wins)),
            'average_loss':None if not losses else str(-loss_sum/len(losses)),
            'exposure_fraction':sum(Decimal(row['quantity'])>0 for row in equity)/len(equity),
            'conventions':{'year_seconds':SECONDS_PER_YEAR,'bar_seconds':seconds,'risk_free_rate':0,'returns':'simple close equity including initial cash -> first bar; sample stdev ddof=1; downside RMS over all bars'},
            'unavailable_reasons':reasons,'warnings':warnings,'closed_trades':closed,'drawdown':drawdowns}
    if any(isinstance(v,float) and not math.isfinite(v) for v in result.values()):raise ValueError('Non-finite analytics result')
    return result
