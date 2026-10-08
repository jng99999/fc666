"""Read-only selected allocated requested-account exposure; not execution permission."""
from copy import deepcopy
from decimal import Decimal,localcontext
from datetime import timedelta
from sqlalchemy import select,text
from core.models import Candle,Instrument
from core.storage.models import InstrumentRecord,CandleRecord
from core.market_data.quality import SYMBOLS
from core.paper import requested_journal as journal,requested_inspection as inspection,requested_execution as execution
from core.paper.fault_adapter import sha,encoded

VERSION='requested-selected-exposure-v1'


def evaluate(journals,quotes,observed_at):
    if not isinstance(journals,list) or not 1<=len(journals)<=8:raise ValueError('Select1..8 accounts')
    if len(encoded({'journals':journals,'quotes':quotes}).encode())>execution.MAX_BYTES:raise ValueError('Exposure input exceeds32MiB')
    financials=[inspection.verify(report) for report in journals];now=execution.clock(observed_at)
    identities=[f['opening']['account_id'] for f in financials]
    if identities!=sorted(set(identities)):raise ValueError('Unique sorted accounts required')
    if not isinstance(quotes,list):raise ValueError('Complete quote evidence required')
    prices={};times=set()
    for item in quotes:
        if not isinstance(item,dict) or set(item)!={'instrument','candle'}:raise ValueError('Invalid quote evidence')
        instrument=Instrument.model_validate(item['instrument']);bar=Candle.model_validate(item['candle'])
        if (instrument.instrument_id not in SYMBOLS.values() or instrument.market_type!='SPOT' or instrument.quote!='USDT'
            or instrument.exchange!='binance' or instrument.instrument_id!=f'binance:spot:{instrument.base}-USDT'
            or instrument.native_symbol!=instrument.base+'USDT' or instrument.base not in ['BTC','ETH']
            or bar.instrument_id!=instrument.instrument_id or bar.timeframe!='1m' or not bar.is_closed or bar.source!='binance.public'
            or instrument.instrument_id in prices or not timedelta(0)<=now-bar.close_time<=timedelta(seconds=75)):
            raise ValueError('Unsupported/stale/noncanonical quote evidence')
        prices[instrument.instrument_id]=(instrument,bar.close);times.add(bar.close_time)
    if len(times)!=1 or set(prices)!={f['opening']['instrument_id'] for f in financials}:raise ValueError('Complete common-minute pricing required')
    accounts=[];assets={};cash_total=Decimal(0);equity_total=Decimal(0);cash_hold_total=Decimal(0)
    with localcontext() as ctx:
        ctx.prec=260
        for f in financials:
            identity=f['opening']['instrument_id'];instrument,price=prices[identity];state=f['account']
            cash=execution.decimal(state['cash']);quantity=execution.decimal(state['quantity'])
            active=next((entry for entry in f['requests'] if entry['request']['request_id']==f['active_request_id']),None)
            cash_hold=inventory_hold=pending_buy=Decimal(0)
            if active is not None:
                funding=active['summary']['funding'];cash_hold=execution.decimal(funding['reserved_cash']);inventory_hold=execution.decimal(funding['reserved_quantity'])
                if active['request']['side']=='BUY':pending_buy=execution.decimal(active['summary']['active_unfilled_quantity'])
            equity=cash+quantity*price;cash_total+=cash;equity_total+=equity;cash_hold_total+=cash_hold
            entry=assets.setdefault(instrument.base,{'quantity':Decimal(0),'market_value':Decimal(0),'pending_buy_quantity':Decimal(0),'worst_quantity':Decimal(0),'worst_market_value':Decimal(0)})
            for key,value in [('quantity',quantity),('market_value',quantity*price),('pending_buy_quantity',pending_buy),('worst_quantity',quantity+pending_buy),('worst_market_value',(quantity+pending_buy)*price)]:entry[key]+=value
            accounts.append({'account_id':f['opening']['account_id'],'revision':f['revision'],'instrument_id':identity,'cash':str(cash),
                             'quantity':str(quantity),'mark_price':str(price),'equity':str(equity),'reserved_cash':str(cash_hold),
                             'available_cash':str(cash-cash_hold),'reserved_quantity':str(inventory_hold),'available_quantity':str(quantity-inventory_hold),
                             'pending_buy_quantity':str(pending_buy),'active_request_id':f['active_request_id']})
        body={'version':VERSION,'scope':'SELECTED_ALLOCATED_REQUESTED_ACCOUNTS','observed_at':observed_at,
              'quote_at':next(iter(times)).isoformat(),'journals':deepcopy(journals),'quotes':deepcopy(quotes),'accounts':accounts,
              'assets':{key:{k:str(v) for k,v in value.items()} for key,value in sorted(assets.items())},
              'cash':str(cash_total),'equity':str(equity_total),'reserved_cash':str(cash_hold_total),'available_cash':str(cash_total-cash_hold_total),
              'unallocated_pool_cash_included':False,'pending_sells_reduce_exposure':False,'read_only':True,'submission_allowed':False}
    result={**body,'sha256':sha(body)}
    if len(encoded(result).encode())>execution.MAX_BYTES:raise ValueError('Exposure export exceeds32MiB')
    return result


def capture(engine,accounts):
    if (not isinstance(accounts,list) or not 1<=len(accounts)<=8 or len(set(accounts))!=len(accounts)
        or any(not isinstance(a,str) or not 1<=len(a)<=128 for a in accounts)):raise ValueError('Unique bounded account selection required')
    with journal.transaction(engine) as db:
        db.execute(text('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ'))
        db.execute(text("SET LOCAL lock_timeout='2000ms'"))
        observed=db.scalar(text('SELECT clock_timestamp()'))
        financials=[journal.audit(db,journal.lock(db,a)) for a in sorted(accounts)]
        instruments=[];latest=[]
        for identity in sorted({f['opening']['instrument_id'] for f in financials}):
            instrument=db.get(InstrumentRecord,identity)
            bar=db.scalar(select(CandleRecord).where(CandleRecord.instrument_id==identity,CandleRecord.timeframe=='1m',CandleRecord.is_closed.is_(True),CandleRecord.close_time<=observed).order_by(CandleRecord.close_time.desc()).limit(1))
            if instrument is None or bar is None:raise ValueError('Missing requested quote')
            instruments.append(instrument);latest.append(bar.close_time)
        common=min(latest);quotes=[]
        for instrument in instruments:
            bar=db.scalar(select(CandleRecord).where(CandleRecord.instrument_id==instrument.instrument_id,CandleRecord.timeframe=='1m',CandleRecord.close_time==common,CandleRecord.is_closed.is_(True)))
            if bar is None:raise ValueError('Missing common quote minute')
            quotes.append({'instrument':Instrument.model_validate(instrument,from_attributes=True).model_dump(mode='json'),
                           'candle':Candle.model_validate(bar,from_attributes=True).model_dump(mode='json')})
        return evaluate([inspection.seal(f) for f in financials],quotes,observed.isoformat())


def verify(report):
    if not isinstance(report,dict) or len(encoded(report).encode())>execution.MAX_BYTES:raise ValueError('Invalid exposure export')
    expected=evaluate(report['journals'],report['quotes'],report['observed_at'])
    if encoded(report)!=encoded(expected):raise ValueError('Exposure differs from complete replay')
    return expected
