'use client';
import Link from 'next/link';
import {useEffect,useState} from 'react';
import {getCandles,getIndicators,Intervals,MarketProvider,Symbols,type Interval,type SymbolName} from '../../lib/market';
import {summarizeMarket} from '../../lib/market-analysis';
const number=(value:number|null|undefined,digits=2)=>value==null?'不可计算':value.toLocaleString('zh-CN',{maximumFractionDigits:digits});
const trends={TREND_UP:'上行趋势',TREND_DOWN:'下行趋势',RANGE:'区间震荡',UNAVAILABLE:'尚未确认'};
const volatility={LOW:'低波动',NORMAL:'常规波动',HIGH:'高波动',UNAVAILABLE:'尚未确认'};
export default function Analysis(){
 const [symbol,setSymbol]=useState<SymbolName>('BTCUSDT'),[timeframe,setTimeframe]=useState<Interval>('1h'),[refresh,setRefresh]=useState(0);
 const [data,setData]=useState<ReturnType<typeof summarizeMarket>|null>(null),[loading,setLoading]=useState(true),[error,setError]=useState('');
 useEffect(()=>{const requested=new URLSearchParams(location.search).get('symbol');const selected=Symbols.find(value=>value===requested);if(selected)setSymbol(selected);},[]);
 useEffect(()=>{const abort=new AbortController();let timer:ReturnType<typeof setTimeout>;
  async function load(){setLoading(true);setData(null);setError('');try{
   const signal=AbortSignal.any([abort.signal,AbortSignal.timeout(15000)]);
   const [bars,indicators]=await Promise.all([getCandles(symbol,timeframe,signal,120),getIndicators(symbol,timeframe,signal,120,20)]);
   const row=indicators.rows.at(-1);if(!row)throw new Error('指标服务未返回闭合数据。');
   const next=summarizeMarket(bars,row,timeframe,Date.now(),`${MarketProvider}:spot:${symbol.slice(0,-4)}-USDT`);if(!abort.signal.aborted)setData(next);
  }catch(e){if(!abort.signal.aborted)setError(e instanceof Error?e.message:'行情分析服务不可用。');}finally{if(!abort.signal.aborted){setLoading(false);timer=setTimeout(()=>void load(),30000);}}}
  void load();return()=>{abort.abort();clearTimeout(timer);};
 },[symbol,timeframe,refresh]);
 return <main className="research-page analysis-page"><div className="page-heading"><div><h1>行情分析</h1><p>{MarketProvider==='bitget'?'Bitget':'Binance'} 现货 · 使用真实已闭合 K 线和服务器指标，每 30 秒重新读取。</p></div><Link className="secondary" href={`/research?symbol=${symbol}`}>回测当前产品 →</Link></div>
 <section className="analysis-toolbar"><label>分析交易对<select value={symbol} onChange={e=>setSymbol(e.target.value as SymbolName)}>{Symbols.map(value=><option key={value}>{value}</option>)}</select></label><label>分析周期<select value={timeframe} onChange={e=>setTimeframe(e.target.value as Interval)}>{Intervals.map(value=><option key={value}>{value}</option>)}</select></label><button disabled={loading} onClick={()=>setRefresh(value=>value+1)}>{loading?'读取中…':'刷新分析'}</button></section>
 {loading?<section role="status">正在读取闭合 K 线与指标，旧分析已清除…</section>:error?<section role="alert"><h2>暂时无法生成分析</h2><p>{error}</p><button className="secondary" onClick={()=>setRefresh(value=>value+1)}>重试分析</button></section>:data?<>
 <section data-testid="market-analysis"><div className="page-heading"><div><h2>{symbol} · {timeframe} · {trends[data.row.regime.label]}</h2><p>分析时点 UTC：{data.last.close_time} · 闭合收盘 {number(Number(data.last.close))} USDT</p></div><span className="badge">{volatility[data.row.regime.volatility]}</span></div>
 <div className="research-metrics">{[['20 柱窗口涨跌',`${number(data.change)}%`],['RSI',number(data.row.values.rsi)],['ATR / 收盘',data.row.regime.atr_fraction===null?'不可计算':`${number(data.row.regime.atr_fraction*100)}%`],['成交量 / 前 20 柱均量',data.volumeRatio===null?'不可计算':`${number(data.volumeRatio)} 倍`]].map(([label,value])=><div key={label}><small>{label}</small><strong>{value}</strong></div>)}</div>
 <p>趋势采用服务器 ER / ATR 规则（20 周期）：当前为{trends[data.row.regime.label]}，方向效率 {number(data.row.regime.efficiency_ratio,3)}。成交量对比只描述最新一根闭合柱；前 20 柱均量为零时不计算倍数。</p>
 <div className="analysis-columns"><div><h3>价格与动量</h3><dl><dt>EMA 20</dt><dd>{number(data.row.values.ema)}</dd><dt>SMA 20</dt><dd>{number(data.row.values.sma)}</dd><dt>MACD / 信号线</dt><dd>{number(data.row.values.macd,4)} / {number(data.row.values.macd_signal,4)}</dd><dt>布林上轨 / 下轨</dt><dd>{number(data.row.values.bollinger_upper)} / {number(data.row.values.bollinger_lower)}</dd></dl></div><div><h3>最近 20 根闭合柱区间</h3><dl><dt>区间高点</dt><dd>{number(data.high)} USDT</dd><dt>区间低点</dt><dd>{number(data.low)} USDT</dd><dt>窗口起点 UTC</dt><dd>{data.windowStart}</dd></dl><p>区间高低点是已发生的历史边界，不表示一定形成支撑或阻力。</p></div></div>
 </section><section><h2>已确认的结构事件</h2>{data.row.structure_breaks.length?<ul>{data.row.structure_breaks.map((event,index)=><li key={index}>{event.kind} · {event.direction==='up'?'向上':'向下'}穿越 {event.level} · {event.available_at}</li>)}</ul>:<p>最新闭合柱没有新确认的结构突破事件。</p>}<p>指标使用 {data.closedCount} 根闭合历史；不包含未闭合柱，不预测价格，也不自动下单。AI 解读尚未接入。</p></section></>:null}
 </main>;
}
