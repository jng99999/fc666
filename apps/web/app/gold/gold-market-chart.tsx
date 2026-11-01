'use client';
import {useEffect,useRef,useState} from 'react';
import {AreaSeries,CandlestickSeries,ColorType,createChart,type UTCTimestamp} from 'lightweight-charts';
import type {GoldMarketType} from '../../lib/gold';

export default function GoldMarketChart({bars,ready}:{bars:GoldMarketType['bars'];ready:boolean}){
 const host=useRef<HTMLDivElement>(null);
 const [mode,setMode]=useState<'candles'|'line'>('candles');
 const [failed,setFailed]=useState(false);
 useEffect(()=>{
  if(!host.current||bars.length<2)return;
  const valid=bars.every((bar,index)=>[bar.open_time,bar.close_time,Number(bar.open),Number(bar.high),Number(bar.low),Number(bar.close)].every(Number.isFinite)&&bar.close_time>bar.open_time&&Number(bar.low)>0&&Number(bar.high)>=Math.max(Number(bar.open),Number(bar.close),Number(bar.low))&&Number(bar.low)<=Math.min(Number(bar.open),Number(bar.close))&&(index===0||bar.open_time>bars[index-1].open_time));
  if(!valid){setFailed(true);return;}
  const element=host.current;
  const chart=createChart(element,{width:Math.max(1,element.clientWidth),height:element.clientHeight||252,layout:{background:{type:ColorType.Solid,color:'transparent'},textColor:'#8296b3',fontSize:11,fontFamily:'-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif',attributionLogo:true},grid:{vertLines:{color:'#f1f5fa'},horzLines:{color:'#edf2f8',style:2}},rightPriceScale:{borderVisible:false,minimumWidth:58,scaleMargins:{top:.12,bottom:.1}},timeScale:{borderVisible:false,timeVisible:true,secondsVisible:false,rightOffset:2,minBarSpacing:.1},localization:{locale:'zh-CN',timeFormatter:(value:UTCTimestamp)=>new Date(Number(value)*1000).toISOString().slice(5,16).replace('T',' ')+' UTC'},crosshair:{vertLine:{color:'#9cacc0',labelBackgroundColor:'#617fa5'},horzLine:{color:'#9cacc0',labelBackgroundColor:'#617fa5'}},handleScroll:{mouseWheel:false,horzTouchDrag:true,vertTouchDrag:false,pressedMouseMove:true},handleScale:{pinch:true,mouseWheel:true,axisPressedMouseMove:true}});
  const candles=mode==='candles'?chart.addSeries(CandlestickSeries,{upColor:'#08bc76',downColor:'#f14c68',wickUpColor:'#08bc76',wickDownColor:'#f14c68',borderVisible:false,priceFormat:{type:'price',precision:3,minMove:.001}}):null;
  const line=mode==='line'?chart.addSeries(AreaSeries,{lineColor:'#267aff',topColor:'#267aff20',bottomColor:'#267aff01',lineWidth:2,priceFormat:{type:'price',precision:3,minMove:.001}}):null;
  if(candles)candles.setData(bars.map(bar=>({time:bar.open_time/1000 as UTCTimestamp,open:Number(bar.open),high:Number(bar.high),low:Number(bar.low),close:Number(bar.close)})));
  if(line)line.setData(bars.map(bar=>({time:bar.open_time/1000 as UTCTimestamp,value:Number(bar.close)})));
  chart.timeScale().fitContent();setFailed(false);
  const observer=new ResizeObserver(()=>{chart.resize(Math.max(1,element.clientWidth),Math.max(80,element.clientHeight));chart.timeScale().fitContent();});observer.observe(element);
  return()=>{observer.disconnect();chart.remove();};
 },[bars,mode]);
 return <><div className="gold-chart-toolbar"><span className="gold-timeframe">1m · 闭合采样</span><div className="gold-segmented" role="group" aria-label="黄金图表类型"><button type="button" disabled={!ready} aria-pressed={mode==='candles'} onClick={()=>setMode('candles')}>K线</button><button type="button" disabled={!ready} aria-pressed={mode==='line'} onClick={()=>setMode('line')}>走势</button></div></div><div className="gold-chart-wrap"><div ref={host} className="gold-sampled-chart" data-testid="gold-market-chart" role="img" aria-label={mode==='candles'?'黄金真实闭合采样K线':'黄金真实闭合采样收盘走势'}/>{bars.length<2||failed?<div className="gold-chart-empty" role="status"><strong>{failed?'采样数据无法绘制':'等待完整采样柱'}</strong><p>{failed?'时间或价格未确认，不绘制错误曲线。':'正在采集真实报价，形成完整柱后显示。'}</p></div>:null}</div><div className="gold-chart-caption"><span>UTC · {bars.length} 根采样柱</span><span>中间价 · USD/oz</span></div></>;
}
