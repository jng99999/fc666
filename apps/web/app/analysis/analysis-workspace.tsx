'use client';
import Link from 'next/link';
import {useEffect,useMemo,useState} from 'react';
import {z} from 'zod';
import {getCandles,getIndicators,Intervals,MarketProvider,SnapshotSchema,Symbols,type Interval,type SymbolName,type IndicatorRow,type TickerSnapshot} from '../../lib/market';
import {intervalMs,summarizeMarket} from '../../lib/market-analysis';
import {technicalSeries} from '../../lib/technical-analysis';
import AnalysisChart from './analysis-chart';
import AnalysisDialog from './analysis-dialog';
import {mergeAnalysisPreferences,readAnalysisPreferences,workspacePreferenceKey} from './analysis-preferences';
import {displayPrecision,formatPrice} from './analysis-format';
import {analysisSnapshot,calculateIndicator,indicatorCatalog,initialIndicators,validateIndicator,type IndicatorConfig,type IndicatorId} from './analysis-indicators';
import './analysis.css';

const number=(value:number|null|undefined,digits=2)=>value==null||!Number.isFinite(value)?'—':value.toLocaleString('zh-CN',{minimumFractionDigits:digits,maximumFractionDigits:digits});
const signed=(value:number|null|undefined,digits=2)=>value==null?'—':`${value>0?'+':''}${number(value,digits)}%`;
const names:Record<string,string>={BTC:'比特币',ETH:'以太坊',SOL:'Solana',XRP:'XRP',BNB:'BNB',DOGE:'Dogecoin',ADA:'Cardano',AVAX:'Avalanche'};
const ids=Object.keys(indicatorCatalog) as IndicatorId[];
type AnalysisData=ReturnType<typeof summarizeMarket>&{rows:IndicatorRow[];symbol:SymbolName;timeframe:Interval};
type Dialog='indicators'|'watchlist'|'evidence'|'expand'|null;
type IconName='plus'|'close'|'refresh'|'expand'|'settings'|'arrow'|'eye'|'eyeOff'|'trash'|'trend'|'more'|'down'|'candle'|'chart'|'check';
function Icon({name}:{name:IconName}){
 const paths:Record<IconName,string>={plus:'M12 5v14M5 12h14',close:'m6 6 12 12M18 6 6 18',refresh:'M20 7v5h-5M4 17v-5h5M6 7a7 7 0 0 1 11-1l3 3M4 15l3 3a7 7 0 0 0 11-1',expand:'M8 3H3v5M16 3h5v5M3 16v5h5M21 16v5h-5',settings:'M4 7h16M4 17h16M9 4v6M15 14v6',arrow:'M5 12h14m-6-6 6 6-6 6',eye:'M2 12s4-7 10-7 10 7 10 7-4 7-10 7S2 12 2 12Zm7 0a3 3 0 1 0 6 0 3 3 0 1 0-6 0',eyeOff:'m3 3 18 18M9 5a12 12 0 0 1 3 0c6 0 10 7 10 7a20 20 0 0 1-4 4M6 6a21 21 0 0 0-4 6s4 7 10 7a13 13 0 0 0 5-1',trash:'M4 7h16M9 7V4h6v3M6 7l1 14h10l1-14M10 11v6M14 11v6',trend:'M3 17 9 11l4 4 8-10m-6 0h6v6',more:'M5 12h.01M12 12h.01M19 12h.01',down:'m6 9 6 6 6-6',candle:'M5 3v18m-2-13h4v8H3m9-14v20m-2-17h4v7h-4m9-9v18m-2-12h4v8h-4',chart:'M3 18 8 12l4 3 8-11M3 21h18',check:'m5 12 4 4 10-10'};
 return <svg className="icon" viewBox="0 0 24 24" aria-hidden="true"><path d={paths[name]}/></svg>;
}
function Coin({id}:{id:string}){
 return <span className={`coin coin-${id}`}><svg viewBox="0 0 24 24" aria-hidden="true">{id==='BTC'?<path d="M9 3v18m4-18v4m0 10v4M6 7h9a3 3 0 0 1 0 6H7m1 0h8a3 3 0 0 1 0 6H6" fill="none" stroke="currentColor" strokeWidth="2.1"/>:id==='ETH'?<><path d="m12 1-7 12 7 4 7-4Z" fill="currentColor"/><path d="m5 15 7 8 7-8-7 4Z" fill="currentColor" opacity=".8"/><path d="M12 1v16l7-4Z" fill="#9baaff"/></>:id==='SOL'?<path d="M5 4h16l-3 4H2Zm0 6h16l-3 4H2Zm0 6h16l-3 4H2Z" fill="currentColor"/>:id==='XAU'?<text x="12" y="16.5" textAnchor="middle" fill="currentColor" fontSize="14" fontWeight="700" fontFamily="Arial,sans-serif">Au</text>:<circle cx="12" cy="12" r="7" fill="currentColor"/>}</svg></span>;
}
function Rows({items}:{items:[string,string,string][]}){return <dl className="analysis-values">{items.map(([label,value,note])=><div key={label}><dt>{label}<small>{note}</small></dt><dd>{value}</dd></div>)}</dl>;}
function Sparkline({values,up}:{values:number[];up:boolean}){if(values.length<2)return null;const low=Math.min(...values),range=Math.max(...values)-low||1;return <svg className="sparkline" viewBox="0 0 60 20" aria-hidden="true"><path d={values.map((value,i)=>`${i?'L':'M'}${2+i/(values.length-1)*56},${18-(value-low)/range*16}`).join(' ')} fill="none" stroke={up?'#08b970':'#f24061'} strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round"/></svg>;}

export default function AnalysisWorkspace({market=false}:{market?:boolean}={}){
 const [interactive,setInteractive]=useState(false),[symbol,setSymbol]=useState<SymbolName>('BTCUSDT'),[timeframe,setTimeframe]=useState<Interval>('1h'),[refresh,setRefresh]=useState(0);
 const [configs,setConfigs]=useState<IndicatorConfig[]>(initialIndicators),[mode,setMode]=useState<'candles'|'line'>('candles'),[resetKey,setResetKey]=useState(0),[tab,setTab]=useState('技术指标');
 const [showVolume,setShowVolume]=useState(false);
 const [subPaneTop,setSubPaneTop]=useState(0);
 const [result,setData]=useState<AnalysisData|null>(null),[loading,setLoading]=useState(true),[error,setError]=useState(''),[clock,setClock]=useState(0),[inspected,setInspected]=useState<number|null>(null);
 const [quotes,setQuotes]=useState<Partial<Record<SymbolName,TickerSnapshot>>>({}),[watchlist,setWatchlist]=useState<SymbolName[]>([...Symbols]),[filter,setFilter]=useState<'all'|'crypto'|'gold'>('all');
 const [priceTicks,setPriceTicks]=useState<Partial<Record<SymbolName,string>>>({});
 const [dialog,setDialog]=useState<Dialog>(null),[editing,setEditing]=useState<IndicatorId>('sma'),[draft,setDraft]=useState<Record<string,number>>({...indicatorCatalog.sma.defaults}),[formError,setFormError]=useState('');
 useEffect(()=>{
  const query=new URLSearchParams(location.search),requested=query.get('symbol'),period=query.get('timeframe');
  const selected=Symbols.find(value=>value===requested),selectedPeriod=Intervals.find(value=>value===period);
  let saved:ReturnType<typeof readAnalysisPreferences>|null=null;
  if(market)try{saved=readAnalysisPreferences(localStorage.getItem(workspacePreferenceKey));}catch{}
  const nextSymbol=selected??saved?.symbol??'BTCUSDT';
  setSymbol(nextSymbol);if(selectedPeriod??saved?.timeframe)setTimeframe((selectedPeriod??saved?.timeframe)!);
  if(saved?.watchlist)setWatchlist([...new Set([...saved.watchlist,nextSymbol])]);
  setInteractive(true);setClock(Date.now());
  const timer=setInterval(()=>setClock(Date.now()),5000);return()=>clearInterval(timer);
 },[market]);
 useEffect(()=>{if(market&&interactive)try{localStorage.setItem(workspacePreferenceKey,mergeAnalysisPreferences(localStorage.getItem(workspacePreferenceKey),{symbol,timeframe,watchlist}));}catch{}},[market,interactive,symbol,timeframe,watchlist]);
 useEffect(()=>{
  if(!interactive)return;const abort=new AbortController();
  async function load(){try{const response=await fetch('/api/market/instruments',{cache:'no-store',signal:AbortSignal.any([abort.signal,AbortSignal.timeout(7000)])});if(!response.ok)return;const instruments=z.array(z.object({instrument_id:z.string(),native_symbol:z.string(),exchange:z.string(),market_type:z.string(),tick_size:z.string().refine(value=>Number.isFinite(Number(value))&&Number(value)>0)})).parse(await response.json());const ticks:Partial<Record<SymbolName,string>>={};for(const instrument of instruments){const selected=Symbols.find(value=>value===instrument.native_symbol);if(selected&&instrument.exchange===MarketProvider&&instrument.market_type==='SPOT'&&instrument.instrument_id===`${MarketProvider}:spot:${selected.slice(0,-4)}-USDT`)ticks[selected]=instrument.tick_size;}if(!abort.signal.aborted)setPriceTicks(ticks);}catch{}}
  void load();return()=>abort.abort();
 },[interactive]);
 useEffect(()=>{
  if(!interactive)return;
  const abort=new AbortController();let timer:ReturnType<typeof setTimeout>;
  async function load(){let delay=30000;setLoading(true);setError('');try{
   const signal=AbortSignal.any([abort.signal,AbortSignal.timeout(15000)]);
   const [bars,indicators]=await Promise.all([getCandles(symbol,timeframe,signal,500),getIndicators(symbol,timeframe,signal,500,20)]);
   const row=indicators.rows.at(-1);if(!row)throw new Error('指标服务未返回闭合数据。');
   const next=summarizeMarket(bars,row,timeframe,Date.now(),`${MarketProvider}:spot:${symbol.slice(0,-4)}-USDT`);
   if(!abort.signal.aborted){setData({...next,rows:indicators.rows,symbol,timeframe});setClock(Date.now());setInspected(null);}
  }catch(e){delay=5000;if(!abort.signal.aborted){setData(null);setError(e instanceof Error?e.message:'行情分析服务不可用。');}}finally{if(!abort.signal.aborted){setLoading(false);timer=setTimeout(()=>void load(),delay);}}}
  void load();return()=>{abort.abort();clearTimeout(timer);};
 },[interactive,symbol,timeframe,refresh]);
 useEffect(()=>{
  if(!interactive)return;
  const abort=new AbortController();let timer:ReturnType<typeof setTimeout>;
  async function load(){
   const snapshots=await Promise.all(Symbols.map(async value=>{try{
    const response=await fetch(`/api/market/snapshot?symbol=${value}&channel=ticker`,{cache:'no-store',signal:AbortSignal.any([abort.signal,AbortSignal.timeout(7000)])});
    if(!response.ok)return null;
    const snapshot=SnapshotSchema.parse(await response.json());
    if(snapshot.channel!=='ticker'||snapshot.symbol!==value||snapshot.instrument_id!==`${MarketProvider}:spot:${value.slice(0,-4)}-USDT`||Date.now()-Date.parse(snapshot.received_at)>15000||Date.parse(snapshot.received_at)>Date.now()+5000)return null;
    return snapshot;
   }catch{return null;}}));
   if(!abort.signal.aborted){setQuotes(Object.fromEntries(snapshots.filter(value=>value!==null).map(value=>[value.symbol,value])));timer=setTimeout(()=>void load(),10000);}
  }
  void load();return()=>{abort.abort();clearTimeout(timer);};
 },[interactive]);
 const matching=result?.symbol===symbol&&result.timeframe===timeframe?result:null;
 const stale=matching!==null&&clock-Date.parse(matching.last.close_time)>=intervalMs[timeframe]+15000,data=stale?null:matching;
 const bars=data?.closed??[];
 const snapshot=useMemo(()=>data?analysisSnapshot(data.closed,configs,data.rows):null,[data,configs]);
 const lines=useMemo(()=>data?configs.filter(c=>c.visible).flatMap(config=>calculateIndicator(data.closed,config)):[],[data,configs]);
 const allMetrics=useMemo(()=>data?Object.fromEntries(ids.map(id=>[id,calculateIndicator(data.closed,configs.find(c=>c.id===id)??{id,visible:false,params:indicatorCatalog[id].defaults})])) as Record<IndicatorId,ReturnType<typeof calculateIndicator>>:null,[data,configs]);
 const technical=useMemo(()=>data?technicalSeries(data.closed).at(-1):null,[data]);
 const currentBar=bars[inspected??bars.length-1],base=symbol.slice(0,-4),close=data?Number(data.last.close):null;
 const ticker=quotes[symbol],liveTicker=ticker&&clock-Date.parse(ticker.received_at)<=15000?ticker:null;
 const displayPrice=market&&liveTicker?Number(liveTicker.payload.price):close;
 const rawPrice=market&&liveTicker?liveTicker.payload.price:data?.last.close;
 const price=(value:number|null|undefined)=>formatPrice(value,symbol,priceTicks[symbol],rawPrice);
 const chartPrecision=displayPrecision(symbol,priceTicks[symbol],rawPrice)??undefined;
 const displayChange=market&&liveTicker?Number(liveTicker.payload.change_percent):data?.change;
 const trend=snapshot?.trend??'unavailable',trendLabel={up:'趋势偏强',down:'趋势偏弱',mixed:'趋势分歧',unavailable:'等待有效行情'}[trend];
 const currentValue=(id:IndicatorId,index=0)=>allMetrics?.[id]?.[index]?.values.at(-1)??null;
 const atrFraction=snapshot&&snapshot.atr!==null?snapshot.atr/snapshot.close*100:null;
 const last20=bars.slice(-20),volume=last20.reduce((sum,b)=>sum+Number(b.volume),0),quoteVolume=last20.reduce((sum,b)=>sum+Number(b.volume)*(Number(b.high)+Number(b.low)+Number(b.close))/3,0);
 const indicatorNote='隐藏只改变图表；规则解读使用卡片中明示的 MA、RSI 与 ATR 参数。移除后规则参照恢复默认参数。';
 function edit(id:IndicatorId){setEditing(id);setDraft({...configs.find(c=>c.id===id)?.params??indicatorCatalog[id].defaults});setFormError('');setDialog('indicators');}
 function apply(){
  const issue=validateIndicator(editing,draft);if(issue){setFormError(issue);return;}
  setConfigs(previous=>{
   const other=previous.filter(c=>c.id!==editing).map(c=>indicatorCatalog[editing].pane==='sub'&&indicatorCatalog[c.id].pane==='sub'?{...c,visible:false}:c);
   return [...other,{id:editing,visible:true,params:{...draft}}];
  });setFormError('');
 }
 function toggle(id:IndicatorId){setConfigs(previous=>previous.map(c=>c.id===id?{...c,visible:!c.visible}:indicatorCatalog[id].pane==='sub'&&indicatorCatalog[c.id].pane==='sub'?{...c,visible:false}:c));}
 function toggleEditing(){const existing=configs.find(config=>config.id===editing);if(existing){toggle(editing);return;}apply();}
 function selectProduct(value:string){if(value==='XAUUSD'){window.location.assign('/gold');return;}const selected=Symbols.find(item=>item===value);if(selected)setSymbol(selected);}
 function changeWatchlist(value:SymbolName,checked:boolean){const next=checked?[...watchlist,value]:watchlist.filter(item=>item!==value);if(!next.length)return;setWatchlist(next);if(!next.includes(symbol))setSymbol(next[0]);}
 const visibleConfigs=configs.filter(config=>config.visible);
 const shortName=(config:IndicatorConfig)=>config.id==='sma'||config.id==='smaSlow'?'MA':indicatorCatalog[config.id].name;
 const configTitle=(config:IndicatorConfig)=>config.id==='sma'?'短期移动平均':config.id==='smaSlow'?'长期移动平均':indicatorCatalog[config.id].description;
 const configPeriod=(config:IndicatorConfig)=>Object.values(config.params).join(config.id==='boll'?' / ':'/')||'窗口';
 const momentum=snapshot?.momentum==='高位'?'动量偏热':snapshot?.momentum==='低位'?'动量偏冷':`动量${snapshot?.momentum??'待同步'}`;
 const rangePosition=snapshot&&snapshot.low!==null&&snapshot.high!==null&&snapshot.high>snapshot.low?Math.max(0,Math.min(100,(snapshot.close-snapshot.low)/(snapshot.high-snapshot.low)*100)):null;
 const displayDelta=displayPrice!==null&&displayChange!==undefined&&displayChange> -100?displayPrice-displayPrice/(1+displayChange/100):null;
 const mainPeriods=Intervals.filter(value=>value!=='1m'&&value!=='5m'||value===timeframe);
 const chartState=<div className="quote-chart-host analysis-chart-state" role={error||stale?'alert':'status'}><Icon name="trend"/><strong>{error?'行情分析暂不可用':stale?'闭合数据已过期':'正在读取真实行情'}</strong><p>{error||(stale?'已隐藏过期指标，等待下一次同步。':'同步连续闭合 K 线与指标，暂不生成结论。')}</p>{error||stale?<button className="btn primary small" onClick={()=>setRefresh(value=>value+1)} disabled={!interactive||loading}>重新读取</button>:null}</div>;
 const ruleCard=<section className={`card ai-card${market?' ai-wide':''}`} aria-labelledby="analysis-rule-heading">
  <div className="panel-head"><h2 id="analysis-rule-heading"><span className="ai-mark">AI</span>AI 分析</h2><span className="demo-badge">规则 · AI未接入</span></div>
  <div className={`ai-verdict ${trend==='up'?'up':trend==='down'?'down':'neutral'}`}><span className="trend-icon"><Icon name="trend"/></span><strong>{trendLabel}</strong></div>
  <p className="ai-copy">{!snapshot?'等待有效闭合数据，暂不生成结论。':trend==='unavailable'?'均线预热不足，暂不能判断结构。':`价格在 MA${snapshot.fastPeriod} ${snapshot.close>=snapshot.fast!?'上方':'下方'}，${momentum}。`}</p>
  <div className="ai-evidence"><div><span>结构</span><strong>{snapshot?`MA${snapshot.fastPeriod} ${snapshot.fast!==null&&snapshot.slow!==null?(snapshot.fast>=snapshot.slow?'≥':'＜'):'—'} MA${snapshot.slowPeriod}`:'—'}</strong><small>{snapshot&&snapshot.fast!==null&&snapshot.slow!==null?(snapshot.fast>=snapshot.slow?'多头排列':'空头排列'):'预热不足'}</small></div><div><span>动量</span><strong>{snapshot?`RSI${snapshot.rsiPeriod} = ${number(snapshot.rsi,1)}`:'—'}</strong><small>{momentum}</small></div><div className="evidence-volatility"><span>波动</span><strong>ATR{snapshot?.atrPeriod??14} = {number(snapshot?.atr)}</strong><small>{atrFraction===null?'—':`价格的 ${number(atrFraction)}%`}</small></div></div>
  <div className="ai-range"><p>历史价位区间 <span>（过去 20 根）</span></p><div className="range-track">{rangePosition===null?null:<i style={{left:`${rangePosition}%`}}/>}</div><div className="range-values"><span><strong>{price(snapshot?.low)}</strong><small>近期低点</small></span><span><strong>{price(snapshot?.high)}</strong><small>近期高点</small></span></div></div>
  <button className="btn soft full ai-more" disabled={!interactive||!data} onClick={()=>setDialog('evidence')}>查看分析依据 <Icon name="arrow"/></button>
 </section>;
 return <main className={`exchange-analysis${market?' analysis-market-workspace':''}`} aria-busy={!interactive}>
  {!interactive?<p className="analysis-readiness" role="status">正在准备分析控件…</p>:null}
  <div className={`analysis-layout${market?' market-layout':''}`}>
   <aside className="card watchlist-card" aria-label="分析自选市场">
    <div className="panel-head"><h2>自选</h2><button className="icon-btn" disabled={!interactive} aria-label="管理分析自选" onClick={()=>setDialog('watchlist')}><Icon name="plus"/></button></div>
    <div className="segmented watch-filters" role="group" aria-label="自选类型">{([['all','全部'],['crypto','加密货币'],['gold','黄金']] as const).map(([value,label])=><button key={value} className={filter===value?'active':''} disabled={!interactive} aria-pressed={filter===value} onClick={()=>setFilter(value)}>{label}</button>)}</div>
    <div className="watch-rows">
     {filter!=='crypto'?<button className="watch-row" disabled={!interactive} onClick={()=>selectProduct('XAUUSD')}><Coin id="XAU"/><span className="watch-identity"><strong>XAU/USD</strong><small>黄金</small></span><span className="watch-price"><strong>—</strong><small className="neutral">独立行情入口</small></span></button>:null}
     {filter!=='gold'?watchlist.map(value=>{const ticker=quotes[value],fresh=ticker&&clock-Date.parse(ticker.received_at)<=15000,coin=value.slice(0,-4);return <button key={value} disabled={!interactive} className={`watch-row${symbol===value?' selected':''}`} aria-pressed={symbol===value} onClick={()=>setSymbol(value)}><Coin id={coin}/><span className="watch-identity"><strong>{coin}/USDT</strong><small>{names[coin]}</small></span><span className="watch-price"><strong>{fresh?formatPrice(Number(ticker.payload.price),value,priceTicks[value],ticker.payload.price):'—'}</strong><small className={fresh&&Number(ticker.payload.change_percent)>=0?'up':'down'}>{fresh?signed(Number(ticker.payload.change_percent)):'待同步'}</small></span>{value===symbol&&data?<span className="watch-spark"><Sparkline values={data.closed.slice(-24).map(bar=>Number(bar.close))} up={data.change>=0}/></span>:null}</button>}):null}
    </div><div className="watch-footer"><span className="status-dot"/><span>公共行情 · 24h 涨跌</span></div>
   </aside>
   <div className="chart-column">
    <section className="card quote-card" aria-label="闭合行情分析">
     <label className="mobile-symbol-select"><Coin id={base}/><select aria-label="分析交易对" disabled={!interactive} value={symbol} onChange={event=>selectProduct(event.target.value)}>{Symbols.map(value=><option key={value} value={value}>{value.slice(0,-4)}/USDT　{names[value.slice(0,-4)]}</option>)}<option value="XAUUSD">XAU/USD　黄金</option></select><Icon name="down"/></label>
     <div className="instrument-head"><div className="instrument-title"><Coin id={base}/><h1>{names[base]}</h1><span>{base}/USDT</span><span className="demo-badge">{market&&liveTicker?'实时行情':'闭合行情'}</span></div><div className="instrument-actions"><div className="segmented blue chart-mode" role="group" aria-label="图表类型"><button className={mode==='candles'?'active':''} disabled={!interactive} aria-pressed={mode==='candles'} onClick={()=>setMode('candles')}>K 线</button><button className={mode==='line'?'active':''} disabled={!interactive} aria-pressed={mode==='line'} onClick={()=>setMode('line')}>走势</button></div><button className="icon-btn" aria-label="放大分析图表" disabled={!interactive||!data} onClick={()=>setDialog('expand')}><Icon name="expand"/></button></div></div>
     <div className="quote-price"><strong>{price(displayPrice)}</strong><span className="quote-unit">USDT</span><div className={`quote-change ${displayChange!==undefined&&displayChange>=0?'up':'down'}`}><b>{signed(displayChange)}</b><span>{displayDelta===null?'—':`${displayDelta>0?'+':''}${price(displayDelta)}`}<small>{market&&liveTicker?'24h':'20 柱'}</small></span></div></div>
     <div className="chart-toolbar"><div className="segmented blue timeframes" role="group" aria-label="分析周期">{mainPeriods.map(value=><button key={value} className={timeframe===value?'active':''} disabled={!interactive} aria-label={value} aria-pressed={timeframe===value} onClick={()=>setTimeframe(value)}>{value==='15m'?value:value.toUpperCase()}</button>)}</div><div className="chart-tools"><button className="icon-btn mobile-mode" disabled={!interactive} aria-label={mode==='candles'?'切换为走势图':'切换为K线图'} onClick={()=>setMode(mode==='candles'?'line':'candles')}><Icon name={mode==='candles'?'candle':'chart'}/></button><div className="overlay-chips desktop-only">{visibleConfigs.filter(config=>indicatorCatalog[config.id].pane==='main').slice(0,2).map(config=><button key={config.id} disabled={!interactive} onClick={()=>edit(config.id)}><i style={{background:indicatorCatalog[config.id].color}}/>{shortName(config)}{config.id==='boll'?'':config.params.period}</button>)}</div><button className="btn indicator-trigger" disabled={!interactive} aria-label="管理分析指标" onClick={()=>edit('sma')}><Icon name="settings"/><span className="desktop-only">指标</span></button><button className="icon-btn reset-chart desktop-only" disabled={!interactive||!data} aria-label="重置图表视图" onClick={()=>setResetKey(value=>value+1)}><Icon name="refresh"/></button></div></div>
     <div className="quote-chart-wrap"><div className="chart-readout" aria-live="off"><span>{base}/USDT {timeframe.toUpperCase()}</span><span className="analysis-ohlc">{currentBar?`O ${number(Number(currentBar.open))}　H ${number(Number(currentBar.high))}　L ${number(Number(currentBar.low))}　C ${number(Number(currentBar.close))}`:'等待闭合数据'}</span></div><div className="chart-legend">{lines.filter(line=>line.pane==='main').map(line=><span key={line.key}>{line.label} <b style={{color:line.color}}>{number(line.values[inspected??bars.length-1])}</b></span>)}</div>
      {data?<div data-testid="market-analysis" data-symbol={symbol} data-timeframe={timeframe}><AnalysisChart key={`${symbol}:${timeframe}`} bars={data.closed} lines={lines} mode={mode} resetKey={resetKey} onInspect={setInspected} showVolume={showVolume} onPaneLayout={setSubPaneTop} pricePrecision={chartPrecision}/></div>:chartState}
      <div className="sub-chart-label" style={{top:subPaneTop}}>{lines.filter(line=>line.pane==='sub').map(line=><span key={line.key}>{line.label} <b>{number(line.values[inspected??bars.length-1],line.key==='rsi'?1:2)}</b></span>)}</div>
     </div>
     <div className="quote-stats"><div><span>近 20 根低点</span><strong>{price(snapshot?.low)}</strong></div><div><span>近 20 根高点</span><strong>{price(snapshot?.high)}</strong></div><div><span>ATR{snapshot?.atrPeriod??14} <small>（USDT）</small></span><strong>{number(snapshot?.atr)}</strong></div></div>
     {market?<div className="market-open-analysis"><Link className="text-button" href={`/analysis?symbol=${symbol}&timeframe=${timeframe}`}>进入完整分析工作台 <Icon name="arrow"/></Link></div>:null}
    </section>{market?ruleCard:null}
   </div>
   <aside className="tools-column">{market?null:ruleCard}
    <section className="card indicator-card" aria-labelledby="analysis-indicators-heading"><div className="panel-head"><h2 id="analysis-indicators-heading">指标组合</h2><button className="icon-btn" disabled={!interactive} aria-label="添加技术指标" onClick={()=>edit('sma')}><Icon name="more"/></button></div><div className="indicator-rows">{visibleConfigs.map(config=><div className="indicator-row" key={config.id}><button className="indicator-edit" disabled={!interactive} aria-label={`编辑${indicatorCatalog[config.id].name}`} onClick={()=>edit(config.id)}><i style={{background:indicatorCatalog[config.id].color}}/><strong>{shortName(config)}{['sma','smaSlow','rsi'].includes(config.id)?config.params.period:''}</strong><small>{configTitle(config)}</small></button><button className="indicator-period" disabled={!interactive} aria-label={`编辑 ${shortName(config)} 参数 ${configPeriod(config)}`} onClick={()=>edit(config.id)}>{configPeriod(config)}</button><button className="indicator-remove" disabled={!interactive} aria-label={`移除${indicatorCatalog[config.id].name}`} onClick={()=>setConfigs(previous=>previous.filter(item=>item.id!==config.id))}><Icon name="close"/></button></div>)}</div>{!visibleConfigs.length?<p className="caption">当前显示纯价格图表</p>:null}<button className="btn soft full add-indicator" disabled={!interactive} onClick={()=>edit('ema')}><Icon name="plus"/>添加指标</button><p className="indicator-note">参数修改后，图表与分析同步更新。</p></section>
    {market?<section className="card market-context"><span className="pill blue">研究工作流</span><h3>从价格，到策略</h3><p>把当前品种带入策略工作台，查看信号逻辑与参数。</p><Link className="text-button" href={`/research?symbol=${symbol}&timeframe=${timeframe}`}>打开策略 <Icon name="arrow"/></Link></section>:null}
   </aside>
  </div>
    <details className="analysis-full-detail"><summary>完整指标与计算依据<span>趋势、量价、结构与数据口径</span></summary><div className="analysis-advanced-controls"><label className="field">完整分析周期<select disabled={!interactive} value={timeframe} onChange={event=>setTimeframe(event.target.value as Interval)}>{Intervals.map(value=><option key={value} value={value}>{value}</option>)}</select></label><button className="btn small" disabled={!interactive||loading} onClick={()=>setRefresh(value=>value+1)} aria-label="刷新分析">刷新分析数据</button><span className="caption">{data?`${data.closedCount} 根连续闭合柱 · ${data.last.close_time}`:'等待有效闭合数据'}</span></div>
     <nav className="analysis-content-tabs" aria-label="行情分析内容">{['技术指标','量价波动','市场结构','计算口径'].map(value=><button key={value} disabled={!interactive} aria-pressed={tab===value} onClick={()=>setTab(value)}>{value}</button>)}</nav>
     {tab==='量价波动'?<label className="analysis-volume-toggle"><input type="checkbox" disabled={!interactive} checked={showVolume} onChange={event=>setShowVolume(event.target.checked)}/>显示成交量图层</label>:null}
     {data&&snapshot&&allMetrics?<section className="analysis-detail" aria-label={tab}>
      {tab==='技术指标'?<><h2>当前参数下的趋势与动量</h2><Rows items={[[`MA ${snapshot.fastPeriod} / ${snapshot.slowPeriod}`,`${number(snapshot.fast,4)} / ${number(snapshot.slow,4)}`,'规则解读与主图采用同一组参数'],['EMA',number(currentValue('ema'),4),'SMA 初始化的指数均值'],[`RSI ${snapshot.rsiPeriod}`,number(snapshot.rsi),`独立动量读数 · ${snapshot.momentum}`],['MACD DIF / DEA',`${number(currentValue('macd',1),4)} / ${number(currentValue('macd',2),4)}`,'快慢 EMA 差 / 信号 EMA'],['MACD 柱',number(currentValue('macd'),4),'DIF − DEA，不乘 2'],['Stochastic K / D',`${number(currentValue('stoch'))} / ${number(currentValue('stoch',1))}`,'Fast %K / 简单平滑，不是 KDJ'],['CCI',number(currentValue('cci')),'典型价的标准化偏离'],['Williams %R',number(currentValue('wr')),'范围 −100 至 0'],['ROC',`${number(currentValue('roc'))}%`,'与参数指定柱数前的收盘比较']]}/><p>其他指标默认参数可在“指标组合”中添加并修改。数值不足以推导胜率或保证收益。</p></>:null}
      {tab==='量价波动'?<><h2>成交量与波动</h2><Rows items={[[ '最新柱量 / 前20柱均量',data.volumeRatio===null?'—':`${number(data.volumeRatio)} 倍`,'不包含最新柱本身，零均量不可计算'],['20 柱基础币量',`${number(volume)} ${base}`,'同一闭合窗口内的基础币成交量'],['20 柱估算成交额',`${number(quoteVolume)} USDT`,'典型价 × 基础币量，非逐笔成交额'],['OBV 窗口累计',number(currentValue('obv'),4),'首柱归零；换窗口累计基线改变'],['UTC VWAP',number(currentValue('vwap'),4),'仅已加载当日柱，可能是部分会话'],[`ATR ${snapshot.atrPeriod}`,number(snapshot.atr,4),'Wilder 平滑真实波幅'],['ATR / 收盘',atrFraction===null?'—':`${number(atrFraction)}%`,'当前周期尺度，不年化'],['BOLL 上 / 中 / 下',`${number(currentValue('boll',1),4)} / ${number(currentValue('boll'),4)} / ${number(currentValue('boll',2),4)}`,'所选周期均值 ± 所选倍数总体标准差']]}/></>:null}
      {tab==='市场结构'?<><h2>历史区间与确认事件</h2><Rows items={[[ '20 柱区间',`${number(snapshot.low,6)} – ${number(snapshot.high,6)}`,'已发生的边界，不是已确认支撑或阻力'],['20 柱区间位置',snapshot.high===snapshot.low?'—':`${number((snapshot.close-snapshot.low!)/(snapshot.high!-snapshot.low!)*100)}%`,'收盘相对区间高低点位置'],['ER20 服务端状态',data.row.regime.label==='TREND_UP'?'上行趋势':data.row.regime.label==='TREND_DOWN'?'下行趋势':'区间震荡',`ER ${number(data.row.regime.efficiency_ratio,3)}；独立于均线规则`] ]}/><h3>最近确认的结构突破</h3>{snapshot.events.length?<ul className="analysis-events">{snapshot.events.map((event,i)=><li key={`${event.available_at}:${i}`}><strong>{event.kind} · {event.direction==='up'?'向上':'向下'}</strong><span>{number(Number(event.level),6)} USDT</span><time>{event.available_at}</time></li>)}</ul>:<p>当前窗口没有已确认的结构突破。</p>}<p>按确认时间展示，最多最近 8 条；不回填到未确认的柱。</p></>:null}
      {tab==='计算口径'?<><h2>来源与可用性</h2><p>{MarketProvider}.public 现货，只读。窗口起点 UTC {data.closed[0].open_time}，终点 {data.last.close_time}。数据缺口、来源混用、时点不一致或过期时隐藏分析。</p><p>主图、RSI、ATR 与规则解读均基于同一组 {data.closedCount} 根闭合 OHLC。修改参数后按原窗口重算，不更改历史数据。EMA 以 SMA 初始化；RSI/ATR 用 Wilder 平滑，RSI 完全无涨跌时为 50；不足预热和零分母显示“—”。</p><p>服务端另提供 ER20 与已确认的结构事件；本页保留 MA5/10/60 参照：{number(technical?.ma5,4)} / {number(technical?.ma10,4)} / {number(technical?.ma60,4)}。{indicatorNote}</p><p>AI 服务尚未接入，当前只有确定性规则分析。本页没有持仓推断、下单或执行控制，实盘继续关闭。</p><p>图表：TradingView Lightweight Charts™ · <a href="https://www.tradingview.com/" target="_blank" rel="noreferrer">TradingView</a></p></>:null}
     </section>:<p className="analysis-empty-note">有效行情同步后，才显示指标和分析依据。</p>}
    </details>
    <div className="analysis-research-actions"><Link href={`/research?symbol=${symbol}&timeframe=${timeframe}`}>策略回测 <Icon name="arrow"/></Link><Link href="/research/matrix">批量对比 <Icon name="arrow"/></Link><Link href="/gold">黄金分析 <Icon name="arrow"/></Link></div>

  {dialog==='watchlist'?<AnalysisDialog title="管理自选品种" onClose={()=>setDialog(null)}><p className="caption">选择在自选列表中显示的现货产品，至少保留一个。黄金采用独立行情入口。</p><div className="watchlist-options">{Symbols.map(value=><label key={value}><Coin id={value.slice(0,-4)}/><span><strong>{value.slice(0,-4)}/USDT</strong><small>{names[value.slice(0,-4)]}</small></span><input type="checkbox" checked={watchlist.includes(value)} disabled={watchlist.length===1&&watchlist.includes(value)} onChange={event=>changeWatchlist(value,event.target.checked)}/></label>)}</div><button className="btn primary full" onClick={()=>setDialog(null)}>保存自选</button><p className="analysis-modal-note">{market?'保留现有工作台自选偏好；':'仅影响本页当前会话；'}不修改模拟账户或行情订阅。</p></AnalysisDialog>:null}
  {dialog==='indicators'?<AnalysisDialog title="管理技术指标" onClose={()=>setDialog(null)}><div className="indicator-library"><nav className="indicator-catalog" aria-label="指标类型">{ids.map(id=><button key={id} className={editing===id?'active':''} aria-pressed={editing===id} onClick={()=>{setEditing(id);setDraft({...configs.find(config=>config.id===id)?.params??indicatorCatalog[id].defaults});setFormError('');}}><i style={{background:indicatorCatalog[id].color}}/><strong>{id==='sma'?'MA':id==='smaSlow'?'MA 长期':indicatorCatalog[id].name}</strong><small>{indicatorCatalog[id].pane==='main'?'主图':'副图'}</small></button>)}</nav><section id="indicator-editor"><h3>{editing==='sma'?'短期移动平均':editing==='smaSlow'?'长期移动平均':indicatorCatalog[editing].name}</h3><p className="caption">{indicatorCatalog[editing].pane==='main'?'叠加在价格主图':'显示在图表下方的副图'} · 闭合数据计算</p><form onSubmit={event=>{event.preventDefault();apply();}} noValidate><div className="indicator-param-fields">{indicatorCatalog[editing].fields.map(field=><label className="field" key={field.key}>{field.label}<input type="number" required min={field.min} max={field.max} step={field.step??1} value={Number.isFinite(draft[field.key])?draft[field.key]:''} onChange={event=>setDraft(previous=>({...previous,[field.key]:event.target.value===''?NaN:Number(event.target.value)}))}/></label>)}</div>{!indicatorCatalog[editing].fields.length?<p className="caption">此指标按窗口计算，无可修改周期。</p>:null}{formError?<p role="alert" className="indicator-editor-error">{formError}</p>:null}<button type="submit" className="btn primary full">应用指标</button></form><button className="btn soft full" data-indicator-toggle onClick={toggleEditing}><Icon name={configs.find(config=>config.id===editing)?.visible?'check':'plus'}/>{configs.find(config=>config.id===editing)?.visible?'已显示 · 点击隐藏':`添加到${indicatorCatalog[editing].pane==='main'?'主图':'副图'}`}</button><p className="caption indicator-method">{indicatorCatalog[editing].description}。参数不足预热时显示“—”；图表与规则按同一组闭合数据重算。</p>{data&&draft.period>data.closedCount?<p className="analysis-modal-note">当前 {data.closedCount} 根闭合柱不足此周期预热。</p>:null}<p className="analysis-modal-note">{indicatorNote}</p></section></div></AnalysisDialog>:null}
  {dialog==='evidence'&&data&&snapshot?<AnalysisDialog title="分析依据" onClose={()=>setDialog(null)}><div className="analysis-report-heading"><Coin id={base}/><div><strong>{names[base]} · {base}/USDT</strong><p className="caption">{timeframe.toUpperCase()} · {data.last.close_time} UTC</p></div><span className="demo-badge">确定性规则</span></div><div className="notice">当前解读由真实闭合行情的指标规则生成，尚未调用外部 AI 模型。本页不发送订单，实盘保持关闭。</div><section className="report-section"><span>01</span><div><h3>均线结构 · {trendLabel}</h3><p>闭合价 {number(snapshot.close)}，MA{snapshot.fastPeriod} 为 {number(snapshot.fast)}，MA{snapshot.slowPeriod} 为 {number(snapshot.slow)}。{snapshot.fast===null||snapshot.slow===null?'均线预热不足。':`闭合价${snapshot.close>=snapshot.fast?'高于或等于':'低于'}短期均线，短期均线${snapshot.fast>=snapshot.slow?'高于或等于':'低于'}长期均线。`}</p></div></section><section className="report-section"><span>02</span><div><h3>动量 · {momentum}</h3><p>RSI{snapshot.rsiPeriod} 为 {number(snapshot.rsi,1)}，采用 Wilder 平滑，参考边界 30 / 70。均线方向与 RSI 动量可出现分歧，报告保留各自计算结果。</p></div></section><section className="report-section"><span>03</span><div><h3>价格区间与波动</h3><p>近 20 根最低 {number(snapshot.low)}、最高 {number(snapshot.high)}；ATR{snapshot.atrPeriod} 为 {number(snapshot.atr)}，占闭合价 {number(atrFraction)}%。这是历史范围，不代表未来支撑或阻力保证。</p></div></section><p className="caption analysis-modal-note">来源 {MarketProvider}.public；{data.closedCount} 根连续闭合柱，自 {data.closed[0].open_time} 至 {data.last.close_time}。缺口、过期和时点不一致时隐藏分析。{indicatorNote}</p></AnalysisDialog>:null}
  {dialog==='expand'&&data?<AnalysisDialog title={`${base}/USDT · ${timeframe} · 闭合图表`} wide onClose={()=>setDialog(null)}><div className="analysis-expanded-chart"><AnalysisChart bars={data.closed} lines={lines} mode={mode} showVolume={showVolume}/></div><p>图表可横向拖动、缩放；仅公共行情与指标，不包含交易操作。</p></AnalysisDialog>:null}
 </main>;
}
