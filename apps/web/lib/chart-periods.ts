import type {Candle,IndicatorRow,Interval} from './market';
import {calculateIndicator,indicatorCatalog,type IndicatorId} from '../app/analysis/analysis-indicators';
import {analyzePriceAction} from './price-action';

export const chartPeriods=['1m','2m','3m','5m','15m','30m','1h','2h','4h','1d'] as const;
/** Chart-only periods. Execution/backtest interval contracts remain independent. */
export function chartPeriodMs(value:string):number {
 const match=/^([1-9]\d*)(m|h|d)$/.exec(value);
 if(!match)return 0;
 const amount=Number(match[1]),unit=match[2];
 if(unit==='m'&&amount<=60)return amount*60000;
 if(unit==='h'&&amount<=24)return amount*3600000;
 return unit==='d'&&amount===1?86400000:0;
}
export function chartBasePeriod(value:string):Interval {
 const duration=chartPeriodMs(value);
 if(!duration)throw new Error('请选择 1–60 分钟、1–24 小时或 1 天。');
 return (['1d','4h','1h','15m','5m','1m'] as const).find(base=>duration%chartPeriodMs(base)===0)!;
}
type ChartCandle=Omit<Candle,'source'>&{source:string};
export function aggregateChartCandles<T extends ChartCandle>(input:T[],period:string,now:number,allowSessionGaps=false):T[] {
 const step=chartPeriodMs(period),base=input[0]?.timeframe??chartBasePeriod(period),baseStep=chartPeriodMs(base),ratio=step/baseStep;
 if(!step||!baseStep||!Number.isInteger(ratio)||ratio<1)throw new Error('基础周期不能完整合成目标周期。');
 const closed=input.filter(bar=>bar.is_closed&&Date.parse(bar.close_time)<=now);
 if(!closed.length)return [];
 const identity=closed[0].instrument_id,source=closed[0].source;
 const groups=new Map<number,T[]>();let previous=-Infinity;
 for(const bar of closed){
  const start=Date.parse(bar.open_time),end=Date.parse(bar.close_time),[o,h,l,c,v]=[bar.open,bar.high,bar.low,bar.close,bar.volume].map(Number);
  if(bar.timeframe!==base||bar.instrument_id!==identity||bar.source!==source||!Number.isFinite(start)||start<=previous||start%baseStep!==0||end-start!==baseStep||![o,h,l,c,v].every(Number.isFinite)||Math.min(o,h,l,c)<=0||v<0||h<Math.max(o,l,c)||l>Math.min(o,h,c))throw new Error('基础 K 线的来源、顺序或价格无效，未合成周期。');
  previous=start;const bucket=Math.floor(start/step)*step;
  groups.set(bucket,[...(groups.get(bucket)??[]),bar]);
 }
 const result:T[]=[],firstBucket=groups.keys().next().value,lastBucket=[...groups.keys()].at(-1);
 for(const [start,rows] of groups){
  // Omit only the leading/trailing partial bucket. Never bridge an interior gap.
  if(start+step>now)continue;
  const complete=rows.length===ratio&&rows.every((bar,index)=>Date.parse(bar.open_time)===start+index*baseStep);
  if(!complete){if(start===firstBucket||start===lastBucket||allowSessionGaps)continue;throw new Error('基础行情存在缺口，未生成跨缺口的箱体和指标。');}
  if(!allowSessionGaps&&result.length&&Date.parse(result.at(-1)!.close_time)!==start)throw new Error('历史 K 线有缺口，未合成连续周期。');
  const volume=rows.reduce((sum,bar)=>sum+Number(bar.volume),0);
  if(!Number.isFinite(volume))throw new Error('合成成交量无效。');
  result.push({...rows[0],timeframe:period,open_time:new Date(start).toISOString(),close_time:new Date(start+step).toISOString(),high:String(Math.max(...rows.map(b=>Number(b.high)))),low:String(Math.min(...rows.map(b=>Number(b.low)))),close:rows.at(-1)!.close,volume:String(volume),is_closed:true});
 }
 return result;
}

/** Recompute from the aggregated OHLCV, never relabel native-period indicators. */
export function chartIndicatorRows(bars:Candle[]):IndicatorRow[] {
 if(!bars.length)return [];
 const ids=['sma','ema','boll','vwap','rsi','atr','macd'] as const;
 const series=Object.fromEntries(ids.map(id=>[id,calculateIndicator(bars,{id,visible:true,params:indicatorCatalog[id].defaults})])) as Record<typeof ids[number],ReturnType<typeof calculateIndicator>>;
 const value=(id:IndicatorId,index:number,line=0)=>series[id as typeof ids[number]]?.[line]?.values[index]??null;
 const annotations=analyzePriceAction(bars.map(bar=>({time:Date.parse(bar.open_time),closedAt:Date.parse(bar.close_time),open:Number(bar.open),high:Number(bar.high),low:Number(bar.low),close:Number(bar.close),volume:Number(bar.volume)})),{instrumentId:bars[0].instrument_id,source:bars[0].source,timeframe:bars[0].timeframe,volumeKind:'trade'}).annotations;
 return bars.map((bar,index)=>{
  const close=Number(bar.close),window=bars.slice(Math.max(0,index-20),index+1).map(b=>Number(b.close));
  const movement=window.slice(1).reduce((sum,c,i)=>sum+Math.abs(c-window[i]),0),change=close-window[0],atr=value('atr',index),ready=index>=20&&atr!==null;
  const efficiency=ready?(movement?Math.abs(change)/movement:0):null,atrFraction=atr===null?null:atr/close;
  return {open_time:bar.open_time,available_at:bar.close_time,regime:{rule_version:'er-atr-v1',available_at:bar.close_time,label:!ready?'UNAVAILABLE':efficiency!<.35?'RANGE':change>0?'TREND_UP':'TREND_DOWN',volatility:atrFraction===null?'UNAVAILABLE':atrFraction>=.02?'HIGH':atrFraction<.005?'LOW':'NORMAL',efficiency_ratio:efficiency,atr_fraction:atrFraction},values:{sma:value('sma',index),ema:value('ema',index),bollinger_upper:value('boll',index,1),bollinger_lower:value('boll',index,2),vwap:value('vwap',index),rsi:value('rsi',index),atr,macd:value('macd',index,1),macd_signal:value('macd',index,2),macd_histogram:value('macd',index)},confirmed_swings:[],structure_breaks:annotations.filter(a=>(a.kind==='bos'||a.kind==='choch')&&a.confirmedAt===Date.parse(bar.close_time)).map(a=>({kind:a.kind==='bos'?'BOS':'CHOCH',direction:a.direction==='up'?'up':'down',level:String(a.low),available_at:bar.close_time}))};
 });
}
