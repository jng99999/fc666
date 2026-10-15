import type {Candle,IndicatorRow,Interval} from './market';

export const intervalMs:Record<Interval,number>={'1m':60000,'5m':300000,'15m':900000,'1h':3600000,'4h':14400000,'1d':86400000};
export function summarizeMarket(bars:Candle[],row:IndicatorRow,timeframe:Interval,now:number,expectedInstrumentId:string){
 const step=intervalMs[timeframe];
 const closed=bars.filter(bar=>bar.is_closed&&Date.parse(bar.close_time)<=now);
 if(closed.length<21)throw new Error('已闭合历史不足 21 根，暂不生成分析。');
 if(closed.some(bar=>bar.instrument_id!==expectedInstrumentId||bar.source!==`${expectedInstrumentId.split(':')[0]}.public`))throw new Error('行情响应的交易对或来源不一致，暂不生成分析。');
 if(closed.some((bar,index)=>bar.timeframe!==timeframe||Date.parse(bar.close_time)-Date.parse(bar.open_time)!==step||(index>0&&Date.parse(bar.open_time)-Date.parse(closed[index-1].open_time)!==step)))throw new Error('历史 K 线不连续，暂不生成分析。');
 const last=closed[closed.length-1],mark=Date.parse(last.close_time);
 if(now-mark>=step+15000)throw new Error('最新闭合 K 线已过期，请稍后刷新。');
 if(Date.parse(row.available_at)!==mark||Date.parse(row.open_time)!==Date.parse(last.open_time)||Date.parse(row.regime.available_at)!==mark)throw new Error('指标与 K 线时点不同，请刷新后重试。');
 if(row.regime.label==='UNAVAILABLE')throw new Error('指标尚未完成预热，暂不生成分析。');
 const previous=closed.slice(-21,-1),window=closed.slice(-20);
 const meanVolume=previous.reduce((sum,bar)=>sum+Number(bar.volume),0)/previous.length;
 return {last,windowStart:window[0].open_time,high:Math.max(...window.map(bar=>Number(bar.high))),low:Math.min(...window.map(bar=>Number(bar.low))),
  change:(Number(last.close)/Number(window[0].open)-1)*100,volumeRatio:meanVolume>0?Number(last.volume)/meanVolume:null,
  row,closedCount:closed.length};
}
