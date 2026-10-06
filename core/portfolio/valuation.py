"""Read-only, reproducible valuation of explicitly selected independent Paper balances."""
from datetime import datetime, timezone
from decimal import Decimal, localcontext
from types import SimpleNamespace
from uuid import UUID
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import select, func
from sqlalchemy.orm import Session
from core.models import Candle, Instrument
from core.backtest.spot import digest
from core.paper import streams
from core.storage.models import PaperStreamRecord, PaperWorkerRecord, CandleRecord, InstrumentRecord

VERSION = 'paper-portfolio-closed-minute-v1'
SECONDS = {'1m':60,'5m':300,'15m':900,'1h':3600,'4h':14400,'1d':86400}

class MissingAccount(KeyError):
    pass


class Limits(BaseModel):
    model_config = ConfigDict(frozen=True, extra='forbid')
    max_gross_weight: Decimal = Field(default=Decimal('.8'), ge=0, le=1, max_digits=38, decimal_places=18)
    max_asset_weight: Decimal = Field(default=Decimal('.6'), ge=0, le=1, max_digits=38, decimal_places=18)

    @field_validator('max_gross_weight', 'max_asset_weight', mode='before')
    @classmethod
    def exact(cls, value):
        if isinstance(value, (float, bool)): raise ValueError('Use exact decimal strings')
        return value


def selected(ids):
    if not isinstance(ids, list) or not 1 <= len(ids) <= 8: raise ValueError('Select 1..8 realtime accounts')
    values = [str(UUID(value)) for value in ids]
    if len(set(values)) != len(values): raise ValueError('Duplicate accounts cannot be counted twice')
    return values


def stamp(value):
    result = datetime.fromisoformat(value)
    if result.tzinfo is None: raise ValueError('Timezone required')
    return result.astimezone(timezone.utc)


def evaluate(snapshot):
    if set(snapshot) != {'version','as_of','price_as_of','selected_ids','limits','accounts','worker_heartbeat'} or snapshot['version'] != VERSION:
        raise ValueError('Invalid portfolio snapshot')
    ids = selected(snapshot['selected_ids']); cutoff = stamp(snapshot['as_of']); mark = stamp(snapshot['price_as_of'])
    if mark != cutoff.replace(second=0, microsecond=0): raise ValueError('Common closed-minute mark required')
    limits = Limits.model_validate(snapshot['limits'])
    heartbeat = stamp(snapshot['worker_heartbeat']) if snapshot['worker_heartbeat'] is not None else None
    if heartbeat is not None and heartbeat > cutoff: raise ValueError('Future worker heartbeat')
    online = heartbeat is not None and (cutoff-heartbeat).total_seconds() <= 10
    if len(snapshot['accounts']) != len(ids): raise ValueError('Selected account count mismatch')
    rows=[]; alerts=[]; shared_quotes={}
    with localcontext() as ctx:
        ctx.prec=120
        for id, entry in zip(ids,snapshot['accounts']):
            if set(entry) != {'record','instrument','accepted','quote'}: raise ValueError('Invalid account input')
            raw=entry['record']
            if set(raw) != {'session_id','snapshot','observations','ledger','revision','status','halt_at','feed'} or raw['session_id']!=id:
                raise ValueError('Account identity mismatch')
            if isinstance(raw['revision'],bool) or not isinstance(raw['revision'],int) or raw['revision']<0 or raw['status'] not in ['RUNNING','PAUSED','STOPPED','BLOCKED','LIMIT_REACHED']:
                raise ValueError('Invalid account state')
            # Even a damaged ledger must not cause future source bars to be exported.
            frozen=Instrument.model_validate(raw['snapshot']['instrument'])
            if stamp(raw['snapshot']['request']['as_of'])>cutoff:raise ValueError('Future account activation')
            if any(stamp(item['observed_at'])>cutoff for item in raw['observations']):raise ValueError('Future observation')
            source_bars=[*raw['snapshot']['dataset'],*[item['candle'] for item in raw['observations']],*entry['accepted']]
            if any(Candle.model_validate(bar).close_time>mark for bar in source_bars):raise ValueError('Future account data')
            supplied_quote=Candle.model_validate(entry['quote']) if entry['quote'] is not None else None
            if supplied_quote is not None and (supplied_quote.instrument_id!=frozen.instrument_id or supplied_quote.timeframe!='1m' or not supplied_quote.is_closed or supplied_quote.close_time>mark):raise ValueError('Invalid or future quote')
            if frozen.instrument_id in shared_quotes and shared_quotes[frozen.instrument_id]!=supplied_quote:raise ValueError('Accounts must share the same instrument quote')
            shared_quotes[frozen.instrument_id]=supplied_quote
            record=SimpleNamespace(**raw); reason=None; visible=None
            try: visible=streams.visible(record)
            except (ValueError,ArithmeticError):reason='ACCOUNT_INTEGRITY'
            row={'session_id':id,'revision':raw['revision'],'status':raw['status'],'reason':reason,'ledger_clock':None,'symbol':None,
                 'base':None,'cash':None,'quantity':None,'initial_cash':None,'cost_basis':None,'realized_pnl':None,'fees':None,
                 'quote':None,'market_value':None,'equity':None,'unrealized_pnl':None,'net_pnl':None,'entry_halted':None}
            if visible is not None:
                seed,observations,instrument,config,_=streams.inputs(record)
                accepted=[*seed,*[Candle.model_validate(item['candle']) for item in observations]]
                clock=accepted[-1].close_time
                if clock>mark or stamp(record.snapshot['request']['as_of'])>cutoff: raise ValueError('Future account data cannot be exported')
                row.update(ledger_clock=clock.isoformat(),symbol=instrument.native_symbol,base=instrument.base,
                    initial_cash=str(config.initial_cash),entry_halted=visible['risk']['entry_halted'],
                    **{key:visible['account'][key] for key in ['cash','quantity','cost_basis','realized_pnl','fees']})
                if instrument.exchange!='binance' or instrument.market_type!='SPOT' or instrument.quote!='USDT' or instrument.base not in ['BTC','ETH'] or instrument.native_symbol!=instrument.base+'USDT' or instrument.instrument_id!=f'binance:spot:{instrument.base}-USDT': reason='UNSUPPORTED_INSTRUMENT'
                elif raw['status']=='BLOCKED': reason='ACCOUNT_BLOCKED'
                elif entry['instrument'] is None or Instrument.model_validate(entry['instrument'])!=instrument: reason='RULES_CHANGED'
                elif [Candle.model_validate(item) for item in entry['accepted']]!=accepted: reason='ACCEPTED_DATA_CHANGED'
                elif raw['status'] in ['RUNNING','PAUSED'] and (mark-clock).total_seconds()>=SECONDS[accepted[-1].timeframe]: reason='ACCOUNT_LAGGING'
                quote=supplied_quote
                if quote is not None:
                    if quote.instrument_id!=instrument.instrument_id or quote.timeframe!='1m' or not quote.is_closed or quote.close_time>mark:
                        raise ValueError('Invalid or future valuation quote')
                    row['quote']=quote.model_dump(mode='json')
                if reason is None:
                    if quote is None:reason='QUOTE_MISSING'
                    elif quote.close_time!=mark:reason='QUOTE_STALE'
                if reason is None:
                    value=Decimal(row['quantity'])*quote.close
                    equity=Decimal(row['cash'])+value
                    row.update(market_value=str(value),equity=str(equity),unrealized_pnl=str(value-Decimal(row['cost_basis'])),net_pnl=str(equity-config.initial_cash))
                if raw['status'] in ['RUNNING','PAUSED'] and not online: alerts.append({'code':'WORKER_UNAVAILABLE','session_id':id})
                if visible['risk']['entry_halted']: alerts.append({'code':'ENTRY_HALTED','session_id':id})
            row['reason']=reason;rows.append(row)
        total=None;exposures=[]
        if all(row['reason'] is None for row in rows):
            keys=['cash','initial_cash','market_value','equity','realized_pnl','unrealized_pnl','net_pnl','fees']
            sums={key:sum((Decimal(row[key]) for row in rows),Decimal(0)) for key in keys}
            if abs(sums['net_pnl']-sums['realized_pnl']-sums['unrealized_pnl'])>Decimal('1e-38'):
                raise ValueError('Portfolio PnL conservation failed')
            gross=sums['market_value']/sums['equity'] if sums['equity']>0 else None
            total={**{key:str(value) for key,value in sums.items()},'gross_weight':str(gross) if gross is not None else None}
            if gross is not None and gross>limits.max_gross_weight:alerts.append({'code':'GROSS_WEIGHT','value':str(gross),'limit':str(limits.max_gross_weight)})
            for base in sorted({row['base'] for row in rows}):
                members=[row for row in rows if row['base']==base]
                value=sum((Decimal(row['market_value']) for row in members),Decimal(0))
                weight=value/sums['equity'] if sums['equity']>0 else None
                exposures.append({'base':base,'quantity':str(sum((Decimal(row['quantity']) for row in members),Decimal(0))),'market_value':str(value),'weight':str(weight) if weight is not None else None})
                if weight is not None and weight>limits.max_asset_weight:alerts.append({'code':'ASSET_WEIGHT','base':base,'value':str(weight),'limit':str(limits.max_asset_weight)})
        else:
            alerts.extend({'code':row['reason'],'session_id':row['session_id']} for row in rows if row['reason'] is not None)
    return {'version':VERSION,'snapshot_sha256':digest(snapshot),'as_of':snapshot['as_of'],'price_as_of':snapshot['price_as_of'],
        'status':'COMPLETE' if total is not None else 'UNAVAILABLE','rows':rows,'totals':total,'exposures':exposures,'alerts':alerts,
        'trading_enabled':False,'mode':'INDEPENDENT_PAPER_SCENARIO','inputs':snapshot}


def capture(engine, ids, limits, *, as_of=None):
    ids=selected(ids)
    # Read accounts, rules, accepted data and prices from a single PG snapshot.
    with engine.connect().execution_options(isolation_level='REPEATABLE READ') as conn, conn.begin(), Session(bind=conn) as db:
        records={row.session_id:row for row in db.scalars(select(PaperStreamRecord).where(PaperStreamRecord.session_id.in_(ids)))}
        cutoff=as_of or datetime.now(timezone.utc)
        if cutoff.tzinfo is None:raise ValueError('Timezone required')
        cutoff=cutoff.astimezone(timezone.utc);mark=cutoff.replace(second=0,microsecond=0)
        if len(records)!=len(ids):raise MissingAccount('Realtime Paper account not found; historical accounts are excluded')
        entries=[]
        for id in ids:
            row=records[id];raw={key:getattr(row,key) for key in ['session_id','snapshot','observations','ledger','revision','status','halt_at','feed']}
            instrument=Instrument.model_validate(row.snapshot['instrument'])
            accepted=[*row.snapshot['dataset'],*[item['candle'] for item in row.observations]]
            current=db.get(InstrumentRecord,instrument.instrument_id)
            query=select(CandleRecord).where(CandleRecord.instrument_id==instrument.instrument_id,CandleRecord.timeframe==accepted[0]['timeframe'],CandleRecord.open_time>=stamp(accepted[0]['open_time']),CandleRecord.open_time<=stamp(accepted[-1]['open_time'])).order_by(CandleRecord.open_time)
            data=[Candle.model_validate(item,from_attributes=True).model_dump(mode='json') for item in db.scalars(query)]
            quote=db.scalar(select(CandleRecord).where(CandleRecord.instrument_id==instrument.instrument_id,CandleRecord.timeframe=='1m',CandleRecord.is_closed.is_(True),CandleRecord.close_time<=mark).order_by(CandleRecord.open_time.desc()).limit(1))
            entries.append({'record':raw,'instrument':Instrument.model_validate(current,from_attributes=True).model_dump(mode='json') if current else None,'accepted':data,'quote':Candle.model_validate(quote,from_attributes=True).model_dump(mode='json') if quote else None})
        heartbeat=db.scalar(select(func.max(PaperWorkerRecord.heartbeat_at)).where(PaperWorkerRecord.heartbeat_at<=cutoff))
        snapshot={'version':VERSION,'as_of':cutoff.isoformat(),'price_as_of':mark.isoformat(),'selected_ids':ids,'limits':limits.model_dump(mode='json'),
                  'accounts':entries,'worker_heartbeat':heartbeat.isoformat() if heartbeat else None}
        return evaluate(snapshot)
