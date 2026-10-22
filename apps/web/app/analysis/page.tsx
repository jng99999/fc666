'use client';
import Link from 'next/link';
import {useEffect,useState} from 'react';
import {getCandles,getIndicators,Intervals,MarketProvider,Symbols,type Interval,type SymbolName,type IndicatorRow} from '../../lib/market';
import {intervalMs,summarizeMarket} from '../../lib/market-analysis';
import {technicalSeries} from '../../lib/technical-analysis';
import AnalysisChart,{type MainIndicator,type SubIndicator} from './analysis-chart';
import './analysis.css';
const number=(value:number|null|undefined,digits=2)=>value==null||!Number.isFinite(value)?'—':value.toLocaleString('zh-CN',{maximumFractionDigits:digits});
const trends={TREND_UP:'上行趋势',TREND_DOWN:'下行趋势',RANGE:'区间震荡',UNAVAILABLE:'尚未确认'};
type AnalysisData=ReturnType<typeof summarizeMarket>&{rows:IndicatorRow[];technical:ReturnType<typeof technicalSeries>;symbol:SymbolName;timeframe:Interval};
function Rows({items}:{items:[string,string,string][]}){return <dl className="analysis-values">{items.map(([label,value,note])=><div key={label}><dt>{label}<small>{note}</small></dt><dd>{value}</dd></div>)}</dl>;}
export default function Analysis(){
 const [interactive,setInteractive]=useState(false);
 const [symbol,setSymbol]=useState<SymbolName>('BTCUSDT'),[timeframe,setTimeframe]=useState<Interval>('1h'),[refresh,setRefresh]=useState(0);
 const [main,setMain]=useState<MainIndicator>('MA'),[sub,setSub]=useState<SubIndicator>('MACD'),[tab,setTab]=useState('技术指标');
 const [result,setData]=useState<AnalysisData|null>(null),[loading,setLoading]=useState(true),[error,setError]=useState(''),[clock,setClock]=useState(0);
 useEffect(()=>{setInteractive(true);const requested=new URLSearchParams(location.search).get('symbol');const selected=Symbols.find(value=>value===requested);if(selected)setSymbol(selected);const timer=setInterval(()=>setClock(Date.now()),5000);setClock(Date.now());return()=>clearInterval(timer);},[]);
 useEffect(()=>{const abort=new AbortController();let timer:ReturnType<typeof setTimeout>;
  async function load(){let delay=30000;setLoading(true);setError('');try{
   const signal=AbortSignal.any([abort.signal,AbortSignal.timeout(15000)]);
   const [bars,indicators]=await Promise.all([getCandles(symbol,timeframe,signal,120),getIndicators(symbol,timeframe,signal,120,20)]);
   const row=indicators.rows.at(-1);if(!row)throw new Error('指标服务未返回闭合数据。');
   const next=summarizeMarket(bars,row,timeframe,Date.now(),`${MarketProvider}:spot:${symbol.slice(0,-4)}-USDT`);
   if(!abort.signal.aborted){setData({...next,rows:indicators.rows,technical:technicalSeries(next.closed),symbol,timeframe});setClock(Date.now());}
  }catch(e){delay=5000;if(!abort.signal.aborted){setData(null);setError(e instanceof Error?e.message:'行情分析服务不可用。');}}finally{if(!abort.signal.aborted){setLoading(false);timer=setTimeout(()=>void load(),delay);}}}
  void load();return()=>{abort.abort();clearTimeout(timer);};
 },[symbol,timeframe,refresh]);
 const matching=result?.symbol===symbol&&result.timeframe===timeframe?result:null;
 const stale=matching!==null&&clock-Date.parse(matching.last.close_time)>=intervalMs[timeframe]+15000;
 const data=stale?null:matching,v=data?.row.values,t=data?.technical.at(-1),close=data?Number(data.last.close):null;
 const pct=(a:number|null|undefined,b:number|null|undefined)=>a==null||b==null||b===0?null:(a/b-1)*100;
 const bandWidth=v?.bollinger_upper!=null&&v.bollinger_lower!=null?v.bollinger_upper-v.bollinger_lower:null;
 const bandPosition=close!==null&&bandWidth!==null&&bandWidth>0&&v?.bollinger_lower!=null?(close-v.bollinger_lower)/bandWidth*100:null;
 const window=data?.closed.slice(-20)??[],volume=window.reduce((sum,b)=>sum+Number(b.volume),0),quoteVolume=window.reduce((sum,b)=>sum+Number(b.volume)*(Number(b.high)+Number(b.low)+Number(b.close))/3,0);
 const events=data?.rows.flatMap(row=>row.structure_breaks).slice(-8).reverse()??[];
 return <main className="exchange-analysis" aria-busy={!interactive}>
  <header className="analysis-market-heading"><div><h1><label className="analysis-symbol"><span className="sr-only">分析交易对</span><select aria-label="分析交易对" disabled={!interactive} value={symbol} onChange={e=>setSymbol(e.target.value as SymbolName)}>{Symbols.map(value=><option key={value} value={value}>{value.slice(0,-4)} / USDT</option>)}</select></label></h1><p>{MarketProvider==='bitget'?'Bitget':'Binance'} 现货 <span>行情分析</span></p></div><button className="analysis-refresh" disabled={!interactive||loading} onClick={()=>setRefresh(value=>value+1)} aria-label="刷新分析">{loading?'更新中':'↻ 刷新'}</button></header>
  {!interactive?<p className="analysis-data-clock" role="status">正在准备分析控件…</p>:null}
  <section className="analysis-quote" aria-label="闭合行情摘要"><div><strong className={data&&Number(data.last.close)>=Number(data.last.open)?'quote-up':'quote-down'}>{number(close,6)}</strong><span>最新闭合价 · USDT</span><p className={data&&data.change>=0?'quote-up':'quote-down'}>{data?`${data.change>0?'+':''}${number(data.change)}%`:'—'} <small>20 柱涨跌</small></p></div><dl><div><dt>20 柱最高</dt><dd>{number(data?.high,6)}</dd></div><div><dt>20 柱最低</dt><dd>{number(data?.low,6)}</dd></div><div><dt>20 柱成交量 · {symbol.slice(0,-4)}</dt><dd>{data?number(volume):'—'}</dd></div></dl></section>
  <div className="analysis-periods" role="group" aria-label="分析周期">{Intervals.map(value=><button key={value} disabled={!interactive} aria-pressed={timeframe===value} onClick={()=>setTimeframe(value)}>{value}</button>)}</div>
  {data?<div data-testid="market-analysis" data-symbol={symbol} data-timeframe={timeframe}>
   <div className="analysis-chart-legend"><span>{main==='MA'?`MA5 ${number(t?.ma5,4)} · MA10 ${number(t?.ma10,4)} · MA60 ${number(t?.ma60,4)}`:main==='EMA'?`EMA20 ${number(v?.ema,4)}`:main==='BOLL'?`BOLL(20,2) 上 ${number(v?.bollinger_upper,4)} 中 ${number(v?.sma,4)} 下 ${number(v?.bollinger_lower,4)}`:main==='VWAP'?`UTC VWAP ${number(v?.vwap,4)}`:'价格与成交量'}</span><span>{sub}{sub==='MACD'?'(12,26,9)':sub==='RSI'||sub==='ATR'||sub==='WR'?'(14)':sub==='STOCH'?'(14,3)':sub==='CCI'?'(20)':sub==='ROC'?'(12)':' · 窗口累计'}</span></div>
   <AnalysisChart key={`${symbol}:${timeframe}`} bars={data.closed} rows={data.rows} technical={data.technical} main={main} sub={sub}/>
  </div>:<div className="analysis-chart-state" role={error||stale?'alert':'status'}><strong>{error?'行情分析暂不可用':stale?'闭合数据已过期':'正在读取真实行情'}</strong><p>{error||(stale?'已隐藏过期指标，等待下一次同步。':'同步 K 线、成交量和技术指标…')}</p>{error||stale?<button onClick={()=>setRefresh(value=>value+1)} disabled={loading}>重新读取</button>:<div className="analysis-skeleton"/>}</div>}
  <div className="analysis-indicator-controls"><div role="group" aria-label="主图指标">{(['MA','EMA','BOLL','VWAP','NONE'] as const).map(value=><button key={value} disabled={!interactive} aria-pressed={main===value} onClick={()=>setMain(value)}>{value==='NONE'?'无主图':value}</button>)}</div><div role="group" aria-label="副图指标">{(['MACD','RSI','STOCH','CCI','WR','ROC','OBV','ATR'] as const).map(value=><button key={value} disabled={!interactive} aria-pressed={sub===value} onClick={()=>setSub(value)}>{value}</button>)}</div></div>
  <div className="analysis-data-clock">{data?`${data.closedCount} 根闭合柱 · UTC ${data.last.close_time.replace('T',' ').replace(/(?:\.000)?(?:Z|\+00:00)$/,'')}`:'仅使用已闭合 K 线'}<span>每 30 秒同步</span></div>
  <nav className="analysis-content-tabs" aria-label="行情分析内容">{['技术指标','量价波动','市场结构','计算口径'].map(value=><button key={value} disabled={!interactive} aria-pressed={tab===value} onClick={()=>setTab(value)}>{value}</button>)}</nav>
  {data&&v&&t?<section className="analysis-detail" aria-label={tab}>
   {tab==='技术指标'?<><div className="analysis-regime"><strong>{trends[data.row.regime.label]}</strong><span>ER20 {number(data.row.regime.efficiency_ratio,3)} · RSI14 {number(v.rsi)}</span></div><h2>趋势与均线</h2><Rows items={[[ 'MA 5 / 10 / 60',`${number(t.ma5,4)} / ${number(t.ma10,4)} / ${number(t.ma60,4)}`,'闭合收盘简单均值'],['EMA 20',number(v.ema,4),`收盘偏离 ${number(pct(close,v.ema))}%`],['SMA 20',number(v.sma,4),'服务器 20 周期'],['UTC VWAP',number(v.vwap,4),'当日已加载窗口的典型价量加权']]}/><h2>动量指标</h2><Rows items={[[ 'RSI 14',number(v.rsi),v.rsi===null?'预热不足':v.rsi>=70?'≥70 高位区':v.rsi<=30?'≤30 低位区':'30–70 中间区'],['MACD DIF / DEA',`${number(v.macd,4)} / ${number(v.macd_signal,4)}`,'EMA12 − EMA26 / 信号 EMA9'],['MACD 柱',number(v.macd_histogram,4),'DIF − DEA，不乘 2'],['Stochastic K / D',`${number(t.stochK)} / ${number(t.stochD)}`,'Fast %K14 / SMA3；不是 KDJ'],['CCI 20',number(t.cci),'典型价与均值的标准化偏离'],['Williams %R 14',number(t.wr),'范围 −100 至 0'],['ROC 12',`${number(t.roc)}%`,'相对 12 柱前收盘涨跌']]}/></>:null}
   {tab==='量价波动'?<><h2>成交量与价格</h2><Rows items={[[ '最新柱量 / 前20柱均量',data.volumeRatio===null?'—':`${number(data.volumeRatio)} 倍`,'不包含最新柱本身；零均量不可计算'],['OBV 窗口累计',number(t.obv,4),'首柱归零，之后按涨跌增减基础币量'],['20 柱估算成交额',`${number(quoteVolume)} USDT`,'典型价 × 基础币成交量，不是交易所逐笔成交额'],['最新柱实体占振幅',Number(data.last.high)===Number(data.last.low)?'—':`${number(Math.abs(Number(data.last.close)-Number(data.last.open))/(Number(data.last.high)-Number(data.last.low))*100)}%`,'开收差绝对值 / 高低差']]}/><h2>波动与布林通道</h2><Rows items={[[ 'ATR 14',number(v.atr,4),'Wilder 平滑真实波幅'],['ATR / 收盘',data.row.regime.atr_fraction===null?'—':`${number(data.row.regime.atr_fraction*100)}%`,'当前周期尺度，不年化'],['BOLL 上 / 中 / 下',`${number(v.bollinger_upper,4)} / ${number(v.sma,4)} / ${number(v.bollinger_lower,4)}`,'20 柱均值 ± 2 倍总体标准差'],['BOLL 带宽',bandWidth===null||v.sma===null?'—':`${number(bandWidth/v.sma*100)}%`,'(上轨 − 下轨) / 中轨'],['BOLL %B',bandPosition===null?'—':`${number(bandPosition)}%`,'0% 为下轨，100% 为上轨；允许超出']]}/></>:null}
   {tab==='市场结构'?<><h2>历史区间与确认事件</h2><Rows items={[[ '20 柱历史高点',number(data.high,6),'已发生的区间边界，不是确认阻力'],['20 柱历史低点',number(data.low,6),'已发生的区间边界，不是确认支撑'],['20 柱区间位置',data.high===data.low?'—':`${number((Number(data.last.close)-data.low)/(data.high-data.low)*100)}%`,'收盘相对区间高低点位置']]}/><h3>最近确认的结构突破</h3>{events.length?<ul className="analysis-events">{events.map((event,i)=><li key={`${event.available_at}:${i}`}><strong>{event.kind} · {event.direction==='up'?'向上':'向下'}</strong><span>{number(Number(event.level),6)} USDT</span><time>{event.available_at}</time></li>)}</ul>:<p>当前历史窗口没有已确认的结构突破。</p>}<p>以确认时间展示，不回填到尚未确认的历史柱；最多展示最近8条。</p></>:null}
   {tab==='计算口径'?<><h2>数据与指标说明</h2><p>来源 {MarketProvider}.public，现货，只读。分析采用 {data.closedCount} 根连续闭合柱；窗口起点 UTC {data.closed[0].open_time}，缺口、来源混用、时点不一致或过期时隐藏分析。</p><p>服务器计算 SMA20、EMA20、BOLL20、RSI14、MACD12/26/9、ATR14、UTC VWAP 与结构事件。浏览器基于同一窗口计算 MA5/10/60、Fast Stochastic14/3、CCI20、WR14、ROC12 与 OBV。预热不足或分母为零显示“—”。</p><p>EMA 以 SMA 初始化，RSI/ATR 用 Wilder 平滑；VWAP 仅统计当前 UTC 日期中已加载的数据，窗口不覆盖完整当日时属于部分会话估算。OBV 首柱归零，换窗口会改变累计基线。这些值用于描述历史，不生成综合买卖评分。</p><p>AI 解读尚未接入，当前内容均为确定性计算；本页不发送订单。</p><a href="https://www.bitget.com/support/articles/12560603829092" target="_blank" rel="noreferrer">Bitget 图表使用说明 ↗</a><p>图表：TradingView Lightweight Charts™ · <a href="https://www.tradingview.com/" target="_blank" rel="noreferrer">TradingView</a></p></>:null}
  </section>:<section className="analysis-detail"><p>指标和分析内容将在有效行情同步后显示。</p></section>}
  <div className="analysis-research-actions"><Link href={`/research?symbol=${symbol}`}>策略回测 ↗</Link><Link href="/research/matrix">批量对比 ↗</Link></div>
 </main>;
}
