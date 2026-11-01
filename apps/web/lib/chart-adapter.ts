import {createChart,CandlestickSeries,HistogramSeries,LineSeries,ColorType,CrosshairMode,createSeriesMarkers,type ISeriesApi,type UTCTimestamp} from "lightweight-charts";
import type {Candle,IndicatorRow} from "./market";
export type ChartBar = Pick<Candle,"open_time"|"open"|"high"|"low"|"close"|"volume">;
export interface ChartAdapter {setMode?:(mode:'candles'|'line')=>void;setIndicators:(rows:IndicatorRow[],visible:boolean)=>void;setHistory:(bars:ChartBar[])=>void;update:(bar:ChartBar)=>void;dispose:()=>void;fit:()=>void;}
const WINDOW=5000;
// Float conversion is confined to rendering; financial data keeps decimal strings.
const point=(bar:ChartBar)=>({time:(Date.parse(bar.open_time)/1000) as UTCTimestamp,open:Number(bar.open),high:Number(bar.high),low:Number(bar.low),close:Number(bar.close)});
const volume=(bar:ChartBar)=>({time:point(bar).time,value:Number(bar.volume),color:Number(bar.close)>=Number(bar.open)?"#00875a55":"#d94d6255"});
export function createChartAdapter(container:HTMLElement,options?:{theme?:'approved';pricePrecision?:()=>number|undefined}):ChartAdapter {
  const approved=options?.theme==='approved';
  const chart=createChart(container,{autoSize:true,localization:{locale:"zh-CN"},layout:approved?{background:{type:ColorType.Solid,color:'transparent'},textColor:'#8296b3',fontFamily:'-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif',fontSize:11,attributionLogo:true,panes:{separatorColor:'#edf1f8',separatorHoverColor:'#dae6f7',enableResize:false}}:{background:{type:ColorType.Solid,color:"#ffffff"},textColor:"#6e6e73",attributionLogo:true},grid:approved?{vertLines:{color:'#f1f5fa'},horzLines:{color:'#edf2f8',style:2}}:{vertLines:{color:"#f0f1f4"},horzLines:{color:"#f0f1f4"}},timeScale:approved?{timeVisible:true,secondsVisible:false,borderVisible:false,rightOffset:2,barSpacing:6,minBarSpacing:3}:{timeVisible:true,secondsVisible:false},rightPriceScale:approved?{borderVisible:false,minimumWidth:54,scaleMargins:{top:.12,bottom:.25}}:{borderColor:"#e3e5e9",scaleMargins:{top:.1,bottom:.25}},crosshair:approved?{mode:CrosshairMode.Normal,vertLine:{color:'#9cacc0',labelBackgroundColor:'#617fa5',style:2},horzLine:{color:'#9cacc0',labelBackgroundColor:'#617fa5',style:2}}:{vertLine:{color:"#0071e3"},horzLine:{color:"#0071e3"}},...(approved?{handleScroll:{mouseWheel:false,pressedMouseMove:true,vertTouchDrag:false,horzTouchDrag:true},handleScale:{pinch:true,mouseWheel:true,axisPressedMouseMove:true,axisDoubleClickReset:true}}:{})});
  const prices=chart.addSeries(CandlestickSeries,{upColor:approved?'#08bc76':"#00875a",downColor:approved?'#f14c68':"#d94d62",borderVisible:false,wickUpColor:approved?'#08bc76':"#00875a",wickDownColor:approved?'#f14c68':"#d94d62",...(approved?{priceLineStyle:2,priceLineColor:'#88c1aa'}:{})});
  const trend=chart.addSeries(LineSeries,{color:'#2c6ef2',lineWidth:2,visible:false});
  const volumes=chart.addSeries(HistogramSeries,{priceFormat:{type:"volume"},priceScaleId:"volume",lastValueVisible:false,priceLineVisible:false});
  chart.priceScale("volume").applyOptions({visible:false,scaleMargins:{top:.82,bottom:0}});
  const overlays=([['sma',approved?'#e9a22c':'#f5c96a'],['ema',approved?'#0d9e91':'#0071e3'],['bollinger_upper',approved?'#949dd9':'#ab94dd'],['bollinger_lower',approved?'#949dd9':'#ab94dd'],['vwap','#ef99c0']] as const).map(([key,color])=>({key,series:chart.addSeries(LineSeries,{color,lineWidth:approved&&(key==='sma'||key==='ema')?2:1,lastValueVisible:false,priceLineVisible:false,visible:false})}));
  const markers=createSeriesMarkers(prices,[]);
  let oscillators:{key:'rsi'|'atr'|'macd'|'macd_signal'|'macd_histogram';series:ISeriesApi<'Line'>}[]=[];
  function configurePanes(visible:boolean){
    if(visible&&!oscillators.length){
      oscillators=([['rsi',approved?'#9666ef':'#e7c366',1],['macd',approved?'#2877ff':'#0071e3',2],['macd_signal',approved?'#e9a22c':'#ef99c0',2],['macd_histogram',approved?'#08b970':'#00875a',2],['atr',approved?'#949dd9':'#ab94dd',3]] as const).map(([key,color,pane])=>({key,series:chart.addSeries(LineSeries,{color,lineWidth:1,lastValueVisible:false,priceLineVisible:false},pane)}));
      chart.panes().slice(1).forEach(pane=>pane.setHeight(70));
    }else if(!visible&&oscillators.length){for(const {series} of oscillators)chart.removeSeries(series);oscillators=[];}
  }
  let indicators:IndicatorRow[]=[],overlayVisible=false;
  function renderIndicators(){
    const first=history[start]?.open_time,last=history[end-1]?.open_time;
    const rows=first&&last?indicators.filter(r=>Date.parse(r.open_time)>=Date.parse(first)&&Date.parse(r.open_time)<=Date.parse(last)):[];
    for(const {key,series} of [...overlays,...oscillators]){series.applyOptions({visible:overlayVisible});series.setData(rows.map(r=>r.values[key]===null?{time:(Date.parse(r.open_time)/1000) as UTCTimestamp}:{time:(Date.parse(r.open_time)/1000) as UTCTimestamp,value:r.values[key]!}));}
    markers.setMarkers(overlayVisible?rows.flatMap(r=>[...r.confirmed_swings.map(p=>({time:(Date.parse(r.open_time)/1000) as UTCTimestamp,position:'aboveBar' as const,color:'#e7c366',shape:'circle' as const,text:`${p.label??(p.kind==='high'?'H':'L')}`})),...r.structure_breaks.map(b=>({time:(Date.parse(r.open_time)/1000) as UTCTimestamp,position:'belowBar' as const,color:b.direction==='up'?'#00875a':'#d94d62',shape:'square' as const,text:`${b.kind} ${b.direction==='up'?'↑':'↓'}`}))]).slice(-20):[]);
  }
  let history:ChartBar[]=[],start=0,end=0,last=-Infinity,applying=false;
  function render(first:number,until:number,preserve=true) {
    const range=preserve?chart.timeScale().getVisibleRange():null;
    applying=true;start=first;end=until;
    const precision=approved?options?.pricePrecision?.():undefined;
    if(precision!==undefined&&Number.isInteger(precision)&&precision>=0&&precision<=18){const priceFormat={type:'price' as const,precision,minMove:10**-precision};prices.applyOptions({priceFormat});trend.applyOptions({priceFormat});for(const {series} of overlays)series.applyOptions({priceFormat});}
    const window=history.slice(start,end);
    prices.setData(window.map(point));trend.setData(window.map(bar=>({time:point(bar).time,value:Number(bar.close)})));volumes.setData(window.map(volume));renderIndicators();
    if(range && window.length)chart.timeScale().setVisibleRange(range);
    applying=false;
  }
  chart.timeScale().subscribeVisibleLogicalRangeChange(range=>{
    if(!range||applying||!history.length)return;
    if(range.from<100&&start>0){const first=Math.max(0,start-WINDOW/2);render(first,Math.min(history.length,first+WINDOW));}
    else if(range.to>end-start-100&&end<history.length){const until=Math.min(history.length,end+WINDOW/2);render(Math.max(0,until-WINDOW),until);}
  });
  return {
    setMode:mode=>{prices.applyOptions({visible:mode==='candles'});trend.applyOptions({visible:mode==='line'});},
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
      else {prices.update(value);trend.update({time:value.time,value:Number(bar.close)});volumes.update(volume(bar));end=history.length;}
    },
    fit:()=>chart.timeScale().fitContent(),dispose:()=>chart.remove(),
  };
}
