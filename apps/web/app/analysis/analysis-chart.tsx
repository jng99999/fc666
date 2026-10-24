'use client';
import {useEffect,useRef} from 'react';
import {createChart,CandlestickSeries,HistogramSeries,LineSeries,AreaSeries,ColorType,CrosshairMode,type UTCTimestamp,type LineWidth} from 'lightweight-charts';
import type {Candle} from '../../lib/market';
import type {IndicatorLine} from './analysis-indicators';

type ChartData={bars:Candle[];lines:IndicatorLine[]};
export default function AnalysisChart({bars,lines,mode,resetKey=0,onInspect,showVolume=false,onPaneLayout,pricePrecision}:ChartData&{mode:'candles'|'line';resetKey?:number;onInspect?:(index:number|null)=>void;showVolume?:boolean;onPaneLayout?:(top:number)=>void;pricePrecision?:number}){
 const host=useRef<HTMLDivElement>(null),renderer=useRef<{update:(data:ChartData)=>void;reset:()=>void}|null>(null),inspect=useRef(onInspect),paneLayout=useRef(onPaneLayout);
 inspect.current=onInspect;
 paneLayout.current=onPaneLayout;
 const configuration=lines.map(line=>`${line.key}:${line.label}:${line.pane}`).join('|');
 useEffect(()=>{
  if(!host.current)return;
  const element=host.current;
  const chart=createChart(element,{width:Math.max(1,element.clientWidth),height:Math.max(80,element.clientHeight),autoSize:false,layout:{background:{type:ColorType.Solid,color:'transparent'},textColor:'#8296b3',fontFamily:'-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif',fontSize:11,attributionLogo:true,panes:{separatorColor:'#edf1f8',separatorHoverColor:'#dae6f7',enableResize:false}},grid:{vertLines:{color:'#f1f5fa'},horzLines:{color:'#edf2f8',style:2}},rightPriceScale:{borderVisible:false,minimumWidth:54,scaleMargins:{top:.12,bottom:.1}},timeScale:{borderVisible:false,timeVisible:bars[0]?.timeframe!=='1d',secondsVisible:false,rightOffset:2,barSpacing:6,minBarSpacing:3},localization:{locale:'zh-CN'},crosshair:{mode:CrosshairMode.Normal,vertLine:{color:'#9cacc0',labelBackgroundColor:'#617fa5',style:2},horzLine:{color:'#9cacc0',labelBackgroundColor:'#617fa5',style:2}},handleScroll:{mouseWheel:false,pressedMouseMove:true,vertTouchDrag:false,horzTouchDrag:true},handleScale:{pinch:true,mouseWheel:true,axisPressedMouseMove:true,axisDoubleClickReset:true}});
  const candles=mode==='candles'?chart.addSeries(CandlestickSeries,{upColor:'#08bc76',downColor:'#f14c68',wickUpColor:'#08bc76',wickDownColor:'#f14c68',borderVisible:false,priceLineStyle:2,priceLineColor:'#88c1aa'}):null;
  const trend=mode==='line'?chart.addSeries(AreaSeries,{lineColor:'#267aff',topColor:'#267aff20',bottomColor:'#267aff01',lineWidth:2,priceLineVisible:true,priceLineStyle:2,priceLineColor:'#88c1aa'}):null;
  const volume=showVolume?chart.addSeries(HistogramSeries,{priceFormat:{type:'volume'},lastValueVisible:false,priceLineVisible:false},1):null;
  const subPane=showVolume?2:1;
  // The approved source uses a fractional canvas stroke; the library's type narrows it to integer widths.
  const rsiWidth=1.5 as LineWidth;
  const series=lines.map(definition=>({...definition,series:definition.histogram?chart.addSeries(HistogramSeries,{lastValueVisible:false,priceLineVisible:false},definition.pane==='main'?0:subPane):chart.addSeries(LineSeries,{color:definition.color,lineWidth:['sma','smaSlow','ema'].includes(definition.key)?2:definition.key==='rsi'?rsiWidth:1,lastValueVisible:definition.key==='rsi',priceLineVisible:false,crosshairMarkerRadius:3,...(definition.key==='rsi'||definition.key.startsWith('stoch')?{autoscaleInfoProvider:()=>({priceRange:{minValue:0,maxValue:100}})}:{})},definition.pane==='main'?0:subPane)}));
  for(const line of series)for(const value of line.guides??[])line.series.createPriceLine({price:value,color:line.key==='rsi'?'#d8c9f2':'#acb8ca',lineWidth:1,lineStyle:2,axisLabelVisible:true,axisLabelColor:'#f3eefa',axisLabelTextColor:'#9273bc',title:''});
  if(lines.some(line=>line.key==='rsi'))chart.priceScale('right',subPane).applyOptions({scaleMargins:{top:.02,bottom:.02}});
  let layoutFrame=0;
  const resize=()=>{const width=element.clientWidth,height=element.clientHeight;if(!width||!height)return;chart.resize(width,height);if(volume)chart.panes()[1]?.setHeight(43);if(lines.some(line=>line.pane==='sub'))chart.panes()[subPane]?.setHeight(window.innerWidth<=760?68:102);cancelAnimationFrame(layoutFrame);layoutFrame=requestAnimationFrame(()=>paneLayout.current?.(chart.panes()[0].getHeight()+6));};
  const observer=new ResizeObserver(resize);observer.observe(element);resize();
  let initial=true,currentBars:Candle[]=[];
  const reset=()=>{const count=window.innerWidth<=760?65:105;chart.timeScale().setVisibleLogicalRange({from:Math.max(0,currentBars.length-count),to:currentBars.length+3});};
  chart.subscribeCrosshairMove(event=>{
   if(typeof event.time!=='number'){inspect.current?.(null);return;}
   const index=currentBars.findIndex(bar=>Date.parse(bar.open_time)/1000===event.time);
   inspect.current?.(index<0?null:index);
  });
  renderer.current={reset,update(data){
   currentBars=data.bars;
   const time=(stamp:string)=>(Date.parse(stamp)/1000) as UTCTimestamp;
   const precision=pricePrecision??Math.max(2,...data.bars.flatMap(b=>[b.open,b.high,b.low,b.close].map(value=>(Number(value).toFixed(8).replace(/0+$/,'').split('.')[1]??'').length)));
   const priceFormat={type:'price' as const,precision,minMove:10**-precision};
   candles?.applyOptions({priceFormat});trend?.applyOptions({priceFormat});
   candles?.setData(data.bars.map(b=>({time:time(b.open_time),open:Number(b.open),high:Number(b.high),low:Number(b.low),close:Number(b.close)})));
   trend?.setData(data.bars.map(b=>({time:time(b.open_time),value:Number(b.close)})));
   volume?.setData(data.bars.map(b=>({time:time(b.open_time),value:Number(b.volume),color:Number(b.close)>=Number(b.open)?'#08bc7644':'#f14c6844'})));
   for(const line of series){
    const current=data.lines.find(value=>value.key===line.key),digits=line.key==='rsi'?1:line.pane==='main'?precision:Math.min(8,Math.max(3,precision));
    line.series.applyOptions({priceFormat:line.key==='obv'?{type:'volume'}:{type:'price',precision:digits,minMove:10**-digits}});
    line.series.setData(data.bars.map((b,i)=>{const value=current?.values[i];return typeof value==='number'&&Number.isFinite(value)?{time:time(b.open_time),value,...(line.histogram?{color:value>=0?'#0bbd7980':'#f3516b80'}:{})}:{time:time(b.open_time)};}));
   }
   if(initial){reset();initial=false;}
  }};
  return()=>{renderer.current=null;cancelAnimationFrame(layoutFrame);observer.disconnect();chart.remove();};
 // Indicator configuration rebuilds the panes; a market-data update preserves the viewport.
 // eslint-disable-next-line react-hooks/exhaustive-deps
 },[configuration,mode,showVolume,pricePrecision]);
 useEffect(()=>{renderer.current?.update({bars,lines});},[bars,lines,configuration,mode,showVolume,pricePrecision]);
 useEffect(()=>{renderer.current?.reset();},[resetKey]);
 return <div ref={host} className="analysis-chart quote-chart-host" data-testid="analysis-chart" aria-label={`闭合${mode==='candles'?'K线':'收盘走势'}${showVolume?'、成交量':''}${lines.some(line=>line.pane==='sub')?'与技术副图':''}`}/>;
}
