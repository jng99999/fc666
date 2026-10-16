'use client';
import {useEffect,useRef} from 'react';
import {createChart,CandlestickSeries,HistogramSeries,LineSeries,ColorType,type UTCTimestamp} from 'lightweight-charts';
import type {Candle,IndicatorRow} from '../../lib/market';
import type {TechnicalRow} from '../../lib/technical-analysis';
export type MainIndicator='MA'|'EMA'|'BOLL'|'VWAP'|'NONE';
export type SubIndicator='MACD'|'RSI'|'STOCH'|'CCI'|'WR'|'ROC'|'OBV'|'ATR';
type ChartData={bars:Candle[];rows:IndicatorRow[];technical:TechnicalRow[]};
export default function AnalysisChart({bars,rows,technical,main,sub}:ChartData&{main:MainIndicator;sub:SubIndicator}){
 const host=useRef<HTMLDivElement>(null),renderer=useRef<{update:(data:ChartData)=>void}|null>(null);
 useEffect(()=>{
  if(!host.current)return;
  const chart=createChart(host.current,{autoSize:true,layout:{background:{type:ColorType.Solid,color:'#fff'},textColor:'#535b67',fontSize:10,attributionLogo:true},grid:{vertLines:{visible:false},horzLines:{color:'#f1f3f5'}},rightPriceScale:{borderVisible:false},timeScale:{borderVisible:false,timeVisible:true},localization:{locale:'zh-CN'}});
  const price=chart.addSeries(CandlestickSeries,{upColor:'#087f76',downColor:'#c5384c',wickUpColor:'#087f76',wickDownColor:'#c5384c',borderVisible:false});
  const volume=chart.addSeries(HistogramSeries,{priceFormat:{type:'volume'},lastValueVisible:false,priceLineVisible:false},1);
  chart.panes()[1].setHeight(58);
  const definitions: {key:string;color:string;pane:number;hist?:boolean}[]=[];
  if(main==='MA')definitions.push({key:'ma5',color:'#a26800',pane:0},{key:'ma10',color:'#784bc0',pane:0},{key:'ma60',color:'#087f76',pane:0});
  if(main==='EMA')definitions.push({key:'ema',color:'#2262c6',pane:0});
  if(main==='BOLL')definitions.push({key:'sma',color:'#a26800',pane:0},{key:'bollinger_upper',color:'#784bc0',pane:0},{key:'bollinger_lower',color:'#2262c6',pane:0});
  if(main==='VWAP')definitions.push({key:'vwap',color:'#784bc0',pane:0});
  const subKeys:Record<SubIndicator,string[]>={MACD:['macd_histogram','macd','macd_signal'],RSI:['rsi'],STOCH:['stochK','stochD'],CCI:['cci'],WR:['wr'],ROC:['roc'],OBV:['obv'],ATR:['atr']};
  subKeys[sub].forEach((key,i)=>definitions.push({key,color:['#087f76','#2262c6','#a26800'][i],pane:2,hist:key==='macd_histogram'}));
  const lines=definitions.map(def=>({...def,series:def.hist?chart.addSeries(HistogramSeries,{color:def.color,lastValueVisible:false,priceLineVisible:false},def.pane):chart.addSeries(LineSeries,{color:def.color,lineWidth:1,lastValueVisible:false,priceLineVisible:false},def.pane)}));
  const guides:Partial<Record<SubIndicator,number[]>>={RSI:[30,70],STOCH:[20,80],WR:[-80,-20],CCI:[-100,100],ROC:[0],MACD:[0]};
  const reference=lines.find(line=>line.pane===2&&!line.hist);
  for(const value of guides[sub]??[])reference?.series.createPriceLine({price:value,color:'#849099',lineWidth:1,lineStyle:2,axisLabelVisible:true,title:''});
  chart.panes()[2].setHeight(85);
  let initial=true;
  renderer.current={update(data){
   const time=(stamp:string)=>(Date.parse(stamp)/1000) as UTCTimestamp;
   const server=new Map(data.rows.map(row=>[Date.parse(row.open_time),row.values]));
   const extra=new Map(data.technical.map(row=>[Date.parse(row.open_time),row]));
   const precision=Math.max(2,...data.bars.flatMap(b=>[b.open,b.high,b.low,b.close].map(value=>(Number(value).toFixed(8).replace(/0+$/,'').split('.')[1]??'').length)));
   const priceFormat={type:'price' as const,precision,minMove:10**-precision};
   price.applyOptions({priceFormat});
   for(const line of lines){const digits=line.pane===0?precision:Math.min(8,Math.max(4,precision+2));line.series.applyOptions({priceFormat:line.key==='obv'?{type:'volume'}:line.pane===0?priceFormat:{type:'price',precision:digits,minMove:10**-digits}});}
   price.setData(data.bars.map(b=>({time:time(b.open_time),open:Number(b.open),high:Number(b.high),low:Number(b.low),close:Number(b.close)})));
   volume.setData(data.bars.map(b=>({time:time(b.open_time),value:Number(b.volume),color:Number(b.close)>=Number(b.open)?'#087f7677':'#c5384c77'})));
   for(const line of lines)line.series.setData(data.bars.map(b=>{const stamp=Date.parse(b.open_time),values={...server.get(stamp),...extra.get(stamp)} as Record<string,unknown>,value=values[line.key];return typeof value==='number'&&Number.isFinite(value)?{time:time(b.open_time),value,...(line.hist?{color:value>=0?'#087f76':'#c5384c'}:{})}:{time:time(b.open_time)};}));
   if(initial){chart.timeScale().setVisibleLogicalRange({from:Math.max(0,data.bars.length-60),to:data.bars.length+2});initial=false;}
  }};
  return()=>{renderer.current=null;chart.remove();};
 },[main,sub]);
 useEffect(()=>{renderer.current?.update({bars,rows,technical});},[bars,rows,technical,main,sub]);
 return <div ref={host} className="analysis-chart" data-testid="analysis-chart" aria-label={`闭合K线，主图${main}，成交量与${sub}副图`}/>;
}
