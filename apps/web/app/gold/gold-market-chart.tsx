'use client';
import {useEffect,useMemo,useRef,useState} from 'react';
import {AreaSeries,CandlestickSeries,ColorType,createChart,type UTCTimestamp} from 'lightweight-charts';
import type {GoldMarketType} from '../../lib/gold';
import {chartCoordinates,type ChartCoordinates} from '../../lib/chart-coordinates';
import ChartWorkbench from '../../components/chart-workbench';
import ChartPeriodControl from '../../components/chart-period-control';
import {aggregateChartCandles,chartPeriodMs} from '../../lib/chart-periods';
import AnalysisDialog from '../analysis/analysis-dialog';
import {fitImmersiveChart,mobileChartViewport,mobileExchangeQuery} from '../../lib/chart-landscape';
import '../analysis/analysis.css';
import '../analysis/chart-periods.css';
import '../analysis/exchange-market.css';

export default function GoldMarketChart({bars:sampled,ready}:{bars:GoldMarketType['bars'];ready:boolean}){
 const host=useRef<HTMLDivElement>(null);
 const [period,setPeriod]=useState('1m'),[expanded,setExpanded]=useState(false);
 const [mobile,setMobile]=useState(false);
 useEffect(()=>{const media=window.matchMedia(mobileExchangeQuery),update=()=>setMobile(media.matches);update();media.addEventListener('change',update);return()=>media.removeEventListener('change',update);},[]);
 const aggregation=useMemo(()=>{try{
  const rows=aggregateChartCandles(sampled.map(bar=>({...bar,instrument_id:'XAU/USD',source:'swissquote.public:AT:AT:standard:XAU/USD',timeframe:'1m',open_time:new Date(bar.open_time).toISOString(),close_time:new Date(bar.close_time).toISOString(),volume:'0',is_closed:true})),period,Date.now(),true);
  return {bars:rows.map(bar=>({...bar,open_time:Date.parse(bar.open_time),close_time:Date.parse(bar.close_time)})),error:''};
 }catch(error){return {bars:[],error:error instanceof Error?error.message:'黄金采样无法合成该周期。'};}},[sampled,period]);
 const bars=aggregation.bars;
 const [mode,setMode]=useState<'candles'|'line'>('candles');
 const [failed,setFailed]=useState(false);
 const [coordinates,setCoordinates]=useState<ChartCoordinates|null>(null);
 const priceBars=useMemo(()=>failed?[]:bars.map(b=>({time:b.open_time,closedAt:b.close_time,open:Number(b.open),high:Number(b.high),low:Number(b.low),close:Number(b.close),volume:null})),[bars,failed]);
 useEffect(()=>{
  if(!host.current||bars.length<2)return;
  const valid=bars.every((bar,index)=>[bar.open_time,bar.close_time,Number(bar.open),Number(bar.high),Number(bar.low),Number(bar.close)].every(Number.isFinite)&&Number.isSafeInteger(bar.open_time)&&Number.isSafeInteger(bar.close_time)&&bar.open_time>=0&&bar.close_time<=8640000000000000&&bar.close_time-bar.open_time===chartPeriodMs(period)&&Number(bar.low)>0&&Number(bar.high)>=Math.max(Number(bar.open),Number(bar.close),Number(bar.low))&&Number(bar.low)<=Math.min(Number(bar.open),Number(bar.close))&&(index===0||bar.open_time>bars[index-1].open_time));
  if(!valid){setFailed(true);return;}
  const element=host.current;
  const chart=createChart(element,{width:Math.max(1,element.clientWidth),height:element.clientHeight||252,layout:{background:{type:ColorType.Solid,color:'transparent'},textColor:'#8296b3',fontSize:11,fontFamily:'-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif',attributionLogo:true},grid:{vertLines:{color:'#f1f5fa'},horzLines:{color:'#edf2f8',style:2}},rightPriceScale:{borderVisible:false,minimumWidth:58,scaleMargins:{top:.12,bottom:.1}},timeScale:{borderVisible:false,timeVisible:true,secondsVisible:false,rightOffset:2,minBarSpacing:.1},localization:{locale:'zh-CN',timeFormatter:(value:UTCTimestamp)=>new Date(Number(value)*1000).toISOString().slice(5,16).replace('T',' ')+' UTC'},crosshair:{vertLine:{color:'#9cacc0',labelBackgroundColor:'#617fa5'},horzLine:{color:'#9cacc0',labelBackgroundColor:'#617fa5'}},handleScroll:{mouseWheel:false,horzTouchDrag:true,vertTouchDrag:false,pressedMouseMove:true},handleScale:{pinch:true,mouseWheel:true,axisPressedMouseMove:true}});
  const candles=mode==='candles'?chart.addSeries(CandlestickSeries,{upColor:'#08bc76',downColor:'#f14c68',wickUpColor:'#08bc76',wickDownColor:'#f14c68',borderVisible:false,priceFormat:{type:'price',precision:3,minMove:.001}}):null;
  const line=mode==='line'?chart.addSeries(AreaSeries,{lineColor:'#267aff',topColor:'#267aff20',bottomColor:'#267aff01',lineWidth:2,priceFormat:{type:'price',precision:3,minMove:.001}}):null;
  if(candles)candles.setData(bars.map(bar=>({time:bar.open_time/1000 as UTCTimestamp,open:Number(bar.open),high:Number(bar.high),low:Number(bar.low),close:Number(bar.close)})));
  if(line)line.setData(bars.map(bar=>({time:bar.open_time/1000 as UTCTimestamp,value:Number(bar.close)})));
  const bridge=chartCoordinates(chart,(candles??line)!,element,()=>bars.map(b=>b.open_time));setCoordinates(bridge);
  chart.timeScale().fitContent();setFailed(false);
  const resize=()=>{if(mobileChartViewport())fitImmersiveChart(element);else{const immersive=element.closest('.analysis-modal-immersive');if(immersive)element.style.height=`${Math.max(150,immersive.getBoundingClientRect().bottom-element.getBoundingClientRect().top-12)}px`;}chart.resize(Math.max(1,element.clientWidth),Math.max(80,element.clientHeight));};
  const observer=new ResizeObserver(resize);observer.observe(element);const immersive=element.closest('[data-chart-frame]')??element.closest('.analysis-modal-immersive');if(immersive)observer.observe(immersive);resize();
  return()=>{bridge.dispose();observer.disconnect();chart.remove();};
 },[bars,mode,period,expanded]);
 const controls=<div className="gold-chart-toolbar"><ChartPeriodControl value={period} onChange={setPeriod}/><div className="gold-segmented" role="group" aria-label="黄金图表类型"><button type="button" disabled={bars.length<2||failed} aria-pressed={mode==='candles'} onClick={()=>setMode('candles')}>K线</button><button type="button" disabled={bars.length<2||failed} aria-pressed={mode==='line'} onClick={()=>setMode('line')}>走势</button>{!expanded?<button type="button" onClick={()=>setExpanded(true)}>{mobile?'横屏看图':'放大图表'}</button>:null}</div></div>;
 const content=<><ChartWorkbench bars={priceBars} context={{instrumentId:'XAU/USD',source:'swissquote.public:AT:AT:standard:XAU/USD',timeframe:period,volumeKind:'unavailable'}} coordinates={bars.length>=2&&!failed?coordinates:null}><div className="gold-chart-wrap"><div ref={host} className="gold-sampled-chart quote-chart-host" data-testid="gold-market-chart" data-timeframe={period} role="img" aria-label={mode==='candles'?'黄金真实闭合采样K线':'黄金真实闭合采样收盘走势'}/>{bars.length<2||failed?<div className="gold-chart-empty" role="status"><strong>{failed||aggregation.error?'采样数据无法绘制':'所选周期的完整采样不足'}</strong><p>{aggregation.error||(failed?'时间或价格未确认，不绘制错误曲线。':`${period.toUpperCase()} 需要至少两根完整柱；当前一分钟采样 ${sampled.length} 根。请切换较短周期或等待积累，不使用其他来源补齐。`)}</p></div>:null}</div></ChartWorkbench><div className="gold-chart-caption"><span>UTC · {period} · {bars.length} 根完整采样柱</span><span>中间价 · USD/oz{!ready?' · 报价未就绪，仅历史研究':''}</span></div></>;
 return <div className="exchange-analysis gold-chart-workspace">{expanded?<AnalysisDialog title={`XAU/USD · ${period.toUpperCase()}`} immersive onClose={()=>setExpanded(false)}>{controls}<p className="expanded-chart-hint">横放手机查看横屏；可切换周期、箱体图层并双指缩放。</p><div className="analysis-expanded-chart">{content}</div></AnalysisDialog>:<>{controls}{content}</>}</div>;
}
