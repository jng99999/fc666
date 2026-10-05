import {createChart,CandlestickSeries,HistogramSeries,LineSeries,ColorType,createSeriesMarkers,type ISeriesApi,type UTCTimestamp} from "lightweight-charts";
import type {Candle,IndicatorRow} from "./market";
export type ChartBar = Pick<Candle,"open_time"|"open"|"high"|"low"|"close"|"volume">;
export interface ChartAdapter {setIndicators:(rows:IndicatorRow[],visible:boolean)=>void;setHistory:(bars:ChartBar[])=>void;update:(bar:ChartBar)=>void;dispose:()=>void;fit:()=>void;}
const WINDOW=5000;
// Float conversion is confined to rendering; financial data keeps decimal strings.
const point=(bar:ChartBar)=>({time:(Date.parse(bar.open_time)/1000) as UTCTimestamp,open:Number(bar.open),high:Number(bar.high),low:Number(bar.low),close:Number(bar.close)});
const volume=(bar:ChartBar)=>({time:point(bar).time,value:Number(bar.volume),color:Number(bar.close)>=Number(bar.open)?"#35d0a055":"#ff778555"});
export function createChartAdapter(container:HTMLElement):ChartAdapter {
  const chart=createChart(container,{autoSize:true,localization:{locale:"zh-CN"},layout:{background:{type:ColorType.Solid,color:"#111925"},textColor:"#a9b8cc",attributionLogo:true},grid:{vertLines:{color:"#1b293b"},horzLines:{color:"#1b293b"}},timeScale:{timeVisible:true,secondsVisible:false},rightPriceScale:{borderColor:"#2a3b50",scaleMargins:{top:.1,bottom:.25}},crosshair:{vertLine:{color:"#77b7ff"},horzLine:{color:"#77b7ff"}}});
  const prices=chart.addSeries(CandlestickSeries,{upColor:"#35d0a0",downColor:"#ff7785",borderVisible:false,wickUpColor:"#35d0a0",wickDownColor:"#ff7785"});
  const volumes=chart.addSeries(HistogramSeries,{priceFormat:{type:"volume"},priceScaleId:"volume",lastValueVisible:false,priceLineVisible:false});
  chart.priceScale("volume").applyOptions({visible:false,scaleMargins:{top:.82,bottom:0}});
  const overlays=([['sma','#f5c96a'],['ema','#77b7ff'],['bollinger_upper','#ab94dd'],['bollinger_lower','#ab94dd'],['vwap','#ef99c0']] as const).map(([key,color])=>({key,series:chart.addSeries(LineSeries,{color,lineWidth:1,lastValueVisible:false,priceLineVisible:false,visible:false})}));
  const markers=createSeriesMarkers(prices,[]);
  let oscillators:{key:'rsi'|'atr'|'macd'|'macd_signal'|'macd_histogram';series:ISeriesApi<'Line'>}[]=[];
  function configurePanes(visible:boolean){
    if(visible&&!oscillators.length){
      oscillators=([['rsi','#e7c366',1],['macd','#77b7ff',2],['macd_signal','#ef99c0',2],['macd_histogram','#35d0a0',2],['atr','#ab94dd',3]] as const).map(([key,color,pane])=>({key,series:chart.addSeries(LineSeries,{color,lineWidth:1,lastValueVisible:false,priceLineVisible:false},pane)}));
      chart.panes().slice(1).forEach(pane=>pane.setHeight(70));
    }else if(!visible&&oscillators.length){for(const {series} of oscillators)chart.removeSeries(series);oscillators=[];}
  }
  let indicators:IndicatorRow[]=[],overlayVisible=false;
  function renderIndicators(){
    const first=history[start]?.open_time,last=history[end-1]?.open_time;
    const rows=first&&last?indicators.filter(r=>Date.parse(r.open_time)>=Date.parse(first)&&Date.parse(r.open_time)<=Date.parse(last)):[];
    for(const {key,series} of [...overlays,...oscillators]){series.applyOptions({visible:overlayVisible});series.setData(rows.map(r=>r.values[key]===null?{time:(Date.parse(r.open_time)/1000) as UTCTimestamp}:{time:(Date.parse(r.open_time)/1000) as UTCTimestamp,value:r.values[key]!}));}
    markers.setMarkers(overlayVisible?rows.flatMap(r=>[...r.confirmed_swings.map(p=>({time:(Date.parse(r.open_time)/1000) as UTCTimestamp,position:'aboveBar' as const,color:'#e7c366',shape:'circle' as const,text:`${p.label??(p.kind==='high'?'H':'L')}`})),...r.structure_breaks.map(b=>({time:(Date.parse(r.open_time)/1000) as UTCTimestamp,position:'belowBar' as const,color:b.direction==='up'?'#35d0a0':'#ff7785',shape:'square' as const,text:`${b.kind} ${b.direction==='up'?'↑':'↓'}`}))]).slice(-20):[]);
  }
  let history:ChartBar[]=[],start=0,end=0,last=-Infinity,applying=false;
  function render(first:number,until:number,preserve=true) {
    const range=preserve?chart.timeScale().getVisibleRange():null;
    applying=true;start=first;end=until;
    const window=history.slice(start,end);
    prices.setData(window.map(point));volumes.setData(window.map(volume));renderIndicators();
    if(range && window.length)chart.timeScale().setVisibleRange(range);
    applying=false;
  }
  chart.timeScale().subscribeVisibleLogicalRangeChange(range=>{
    if(!range||applying||!history.length)return;
    if(range.from<100&&start>0){const first=Math.max(0,start-WINDOW/2);render(first,Math.min(history.length,first+WINDOW));}
    else if(range.to>end-start-100&&end<history.length){const until=Math.min(history.length,end+WINDOW/2);render(Math.max(0,until-WINDOW),until);}
  });
  return {
    setIndicators:(rows,visible)=>{indicators=rows;overlayVisible=visible;configurePanes(visible);renderIndicators();},
    setHistory:bars=>{
      const initial=last===-Infinity,following=end===history.length;
      history=bars.slice();last=history.length?Number(point(history[history.length-1]).time):-Infinity;
      const until=following?history.length:Math.min(end,history.length);
      render(Math.max(0,until-WINDOW),until,!initial);
      if(initial&&history.length){if(end-start<=200)chart.timeScale().fitContent();else chart.timeScale().setVisibleLogicalRange({from:end-start-150,to:end-start+5});}
    },
    update:bar=>{
      const value=point(bar);if(Number(value.time)<last)return;
      const following=end===history.length;
      if(Number(value.time)===last&&history.length)history[history.length-1]=bar;else history.push(bar);
      last=Number(value.time);
      if(!following)return;
      if(history.length-start>WINDOW)render(history.length-WINDOW,history.length);
      else {prices.update(value);volumes.update(volume(bar));end=history.length;}
    },
    fit:()=>chart.timeScale().fitContent(),dispose:()=>chart.remove(),
  };
}
