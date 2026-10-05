"use client";
import {useEffect,useRef,useState} from "react";
import {useQuery} from "@tanstack/react-query";
import {createChartAdapter,type ChartAdapter} from "../lib/chart-adapter";
import {getCandles,getIndicators,keyFor,type SymbolName,type Interval,type CandleSnapshot} from "../lib/market";
import {useMarketStore} from "../lib/market-store";
export default function TradingChart({symbol,timeframe}:{symbol:SymbolName;timeframe:Interval}) {
  const element=useRef<HTMLDivElement>(null),adapter=useRef<ChartAdapter|null>(null),initialized=useRef(false);
  const [period,setPeriod]=useState(20),[limit,setLimit]=useState(1000);
  const query=useQuery({queryKey:["candles",symbol,timeframe,limit],queryFn:({signal})=>getCandles(symbol,timeframe,signal,limit),staleTime:30000,refetchInterval:30000,retry:1,refetchOnWindowFocus:false});
  const [showIndicators,setShowIndicators]=useState(false);
  const indicators=useQuery({queryKey:["indicators",symbol,timeframe,limit,period],queryFn:({signal})=>getIndicators(symbol,timeframe,signal,limit,period),enabled:showIndicators,refetchInterval:30000,retry:1,refetchOnWindowFocus:false});
  const live=useMarketStore(state => state.snapshots[keyFor(symbol,`candle.${timeframe}`)]) as CandleSnapshot|undefined;
  useEffect(() => {if (!element.current)return;adapter.current=createChartAdapter(element.current);return ()=>{adapter.current?.dispose();adapter.current=null;};},[]);
  useEffect(() => {initialized.current=false;adapter.current?.setIndicators([],false);adapter.current?.setHistory([]);},[symbol,timeframe,limit]);
  useEffect(() => {if(query.data){adapter.current?.setHistory(query.data);initialized.current=query.data.length>0;}},[query.data]);
  useEffect(() => {if(live && initialized.current)adapter.current?.update(live.payload);},[live,query.data]);
  useEffect(()=>{adapter.current?.setIndicators(indicators.isError?[]:indicators.data?.rows??[],showIndicators);},[indicators.data,indicators.isError,showIndicators,query.data]);
  const regime=indicators.isError?undefined:indicators.data?.rows.at(-1)?.regime;
  const labels={UNAVAILABLE:'分类预热中',RANGE:'震荡',TREND_UP:'上行趋势',TREND_DOWN:'下行趋势'};
  return <div className="chart-body"><div ref={element} className="chart-canvas" data-testid="price-chart" aria-label={`${symbol} ${timeframe} K线与成交量`}/>{query.isPending?<div className="chart-overlay">读取真实历史行情…</div>:query.isError?<div className="chart-overlay error">{query.error.message}<button onClick={()=>void query.refetch()}>重试</button></div>:query.data.length===0?<div className="chart-overlay">尚无该周期历史数据，请导入行情。</div>:null}<span className="chart-caption"><label>窗口 <select aria-label="历史窗口" value={limit} onChange={e=>setLimit(Number(e.target.value))}>{[120,500,1000].map(v=><option key={v} value={v}>{v}</option>)}</select></label> <label>均线 <select aria-label="均线周期" value={period} onChange={e=>setPeriod(Number(e.target.value))}>{[10,20,50,100].map(v=><option key={v} value={v}>{v}</option>)}</select></label> <button aria-pressed={showIndicators} onClick={()=>setShowIndicators(v=>!v)}>指标 overlays</button> {showIndicators?(indicators.isPending?"指标计算中 · ":indicators.isError?`${indicators.error.message} · `:`SMA${period} · EMA${period} · BB${period} ±2σ · UTC VWAP · RSI14 / MACD / ATR14 · 最近20标记在确认柱 · 仅闭合柱 · `):null}<span data-testid="market-regime">{showIndicators&&regime?`${labels[regime.label]} · 波动 ${regime.volatility} · `:""}</span>UTC · {query.data?.length ?? 0} 根历史 K 线 · 公共只读</span></div>;
}
