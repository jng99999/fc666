import {getCandles,getIndicators,Intervals,type SymbolName} from './market';
import {aggregateChartCandles,chartBasePeriod,chartIndicatorRows,chartPeriodMs} from './chart-periods';

export async function loadChartMarket(symbol:SymbolName,period:string,signal:AbortSignal){
 const native=Intervals.find(value=>value===period);
 if(native){const [bars,indicators]=await Promise.all([getCandles(symbol,native,signal,500),getIndicators(symbol,native,signal,500,20)]);return {bars,rows:indicators.rows,base:native};}
 const base=chartBasePeriod(period),ratio=chartPeriodMs(period)/chartPeriodMs(base),required=Math.ceil(121*ratio);
 let bars:Awaited<ReturnType<typeof getCandles>>=[];
 for(let page=0;page<8&&bars.length<required;page++){
  signal.throwIfAborted();const end=bars[0]?.open_time;
  const rows=await getCandles(symbol,base,signal,Math.min(1000,required-bars.length),end);
  if(!rows.length)break;
  if(end&&Date.parse(rows.at(-1)!.open_time)>=Date.parse(end))throw new Error('历史分页没有前进，已停止读取。');
  bars=[...rows,...bars];
  if(rows.length<Math.min(1000,required-(bars.length-rows.length)))break;
 }
 const aggregated=aggregateChartCandles(bars,period,Date.now());
 return {bars:aggregated,rows:chartIndicatorRows(aggregated),base};
}
