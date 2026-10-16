"use client";
import {useEffect,useRef,useState,type PointerEvent,type KeyboardEvent} from "react";
import {QueryClient,QueryClientProvider} from "@tanstack/react-query";
import {z} from "zod";
import {Symbols,Intervals,MarketProvider,type SymbolName,type Interval} from "../lib/market";
import {StrategyIds} from "../lib/research-strategies";
import MarketSocket from "./market-socket";
import TradingChart from "./trading-chart";
import {Watchlist,TickerHeader,BookPanel,TradesPanel,ConnectionFooter} from "./market-panels";
const WatchSymbols=z.array(z.enum(Symbols)).min(1).max(Symbols.length).refine(v=>new Set(v).size===v.length);
const WorkspaceSchema=z.object({symbol:z.enum(Symbols),watchlist:WatchSymbols.default(["BTCUSDT","ETHUSDT"]),timeframe:z.enum(Intervals),left:z.number().min(160).max(300),right:z.number().min(250).max(400),showLeft:z.boolean(),showRight:z.boolean(),tab:z.enum(["持仓","订单","策略","AI","连接"]),mobilePanel:z.enum(["chart","book","trades","watch"]).default("chart") });
type Workspace=z.infer<typeof WorkspaceSchema>;
const INITIAL:Workspace={symbol:"BTCUSDT",watchlist:["BTCUSDT","ETHUSDT"],timeframe:"1m",left:200,right:300,showLeft:true,showRight:true,tab:"持仓",mobilePanel:"chart"};
const KEY="fc666.workspace.v1";
function Separator({label,value,min,max,sign,onChange}:{label:string;value:number;min:number;max:number;sign:number;onChange:(n:number)=>void}) {
  const drag=useRef<{x:number;value:number}|null>(null);
  const resize=(next:number)=>onChange(Math.min(max,Math.max(min,next)));
  function down(event:PointerEvent<HTMLDivElement>) {drag.current={x:event.clientX,value};event.currentTarget.setPointerCapture(event.pointerId);}
  function move(event:PointerEvent<HTMLDivElement>) {if(drag.current)resize(drag.current.value+(event.clientX-drag.current.x)*sign);}
  function key(event:KeyboardEvent<HTMLDivElement>) {if(event.key==="ArrowLeft"||event.key==="ArrowRight"){event.preventDefault();resize(value+(event.key==="ArrowRight"?10:-10)*sign);}}
  return <div className="panel-separator" role="separator" aria-label={label} aria-orientation="vertical" aria-valuemin={min} aria-valuemax={max} aria-valuenow={value} tabIndex={0} onPointerDown={down} onPointerMove={move} onPointerUp={()=>{drag.current=null;}} onPointerCancel={()=>{drag.current=null;}} onKeyDown={key}/>;
}
function Workstation() {
  const [workspace,setWorkspace]=useState<Workspace>(INITIAL),[hydrated,setHydrated]=useState(false),[fullscreenError,setFullscreenError]=useState(false),[isFullscreen,setIsFullscreen]=useState(false);
  const panel=useRef<HTMLElement>(null);
  useEffect(()=>{
    const changed=()=>setIsFullscreen(document.fullscreenElement===panel.current);
    const escape=(event:globalThis.KeyboardEvent)=>{if(event.key==="Escape"&&document.fullscreenElement){void document.exitFullscreen().catch(()=>{if(document.fullscreenElement)setFullscreenError(true);});}};
    document.addEventListener("fullscreenchange",changed);document.addEventListener("keydown",escape);
    return ()=>{document.removeEventListener("fullscreenchange",changed);document.removeEventListener("keydown",escape);};
  },[]);
  useEffect(()=>{try{const raw=localStorage.getItem(KEY);if(raw){const result=WorkspaceSchema.safeParse(JSON.parse(raw));if(result.success)setWorkspace(result.data);}}catch{}setHydrated(true);},[]);
  useEffect(()=>{if(hydrated){try{localStorage.setItem(KEY,JSON.stringify(workspace));}catch{}}},[workspace,hydrated]);
  function update(changes:Partial<Workspace>) {setWorkspace(w=>{const next={...w,...changes};if(!next.watchlist.includes(next.symbol))next.watchlist=[...next.watchlist,next.symbol];return next;});}
  function changeWatchlist(watchlist:SymbolName[]){setWorkspace(w=>({...w,watchlist,symbol:watchlist.includes(w.symbol)?w.symbol:watchlist[0]}));}
  async function fullscreen() {try{setFullscreenError(false);if(document.fullscreenElement)await document.exitFullscreen();else await panel.current?.requestFullscreen();}catch{setFullscreenError(true);}}
  return <main className="terminal"><MarketSocket timeframe={workspace.timeframe} symbols={workspace.watchlist}/><div className="terminal-top terminal-controls"><span>现货行情</span><span className="read-only">{Symbols.length} 个现货 · 公共只读</span><nav aria-label="面板控制"><button aria-pressed={workspace.showLeft} onClick={()=>update({showLeft:!workspace.showLeft})}>自选市场</button><button aria-pressed={workspace.showRight} onClick={()=>update({showRight:!workspace.showRight})}>深度 / 成交</button></nav></div><TickerHeader symbol={workspace.symbol}/>
    <div className="mobile-tabs" role="group" aria-label="移动端市场视图"><label className="mobile-market-picker"><span>交易对</span><select aria-label="移动端交易对" value={workspace.symbol} onChange={e=>update({symbol:e.target.value as SymbolName})}>{Symbols.map(s=><option key={s}>{s}</option>)}</select></label>{([["watch","自选"],["chart","图表"],["book","订单簿"],["trades","成交"]] as const).map(([value,label])=><button key={value} aria-pressed={workspace.mobilePanel===value} onClick={()=>update({mobilePanel:value,...(value==='watch'?{showLeft:true}:{})})}>{label}</button>)}</div>
    <div className={`terminal-workspace mobile-view-${workspace.mobilePanel}`} style={{gridTemplateColumns:`${workspace.showLeft?workspace.left:0}px ${workspace.showLeft?6:0}px minmax(0,1fr) ${workspace.showRight?6:0}px ${workspace.showRight?workspace.right:0}px`}}>
      {workspace.showLeft?<aside className="watch-panel"><Watchlist symbol={workspace.symbol} symbols={workspace.watchlist} onSelect={symbol=>update({symbol})} onChange={changeWatchlist}/></aside>:<div/>}
      {workspace.showLeft?<Separator label="调整自选面板宽度" value={workspace.left} min={160} max={300} sign={1} onChange={left=>update({left})}/>:<div/>}
      <section className="chart-panel" ref={panel}><div className="chart-toolbar"><label>交易对<select aria-label="交易对" value={workspace.symbol} onChange={e=>update({symbol:e.target.value as SymbolName})}>{Symbols.map(s=><option key={s}>{s}</option>)}</select></label><div className="timeframes" role="group" aria-label="图表周期">{Intervals.map(i=><button key={i} aria-pressed={workspace.timeframe===i} className={workspace.timeframe===i?"active":""} onClick={()=>update({timeframe:i})}>{i}</button>)}</div><button onClick={fullscreen} aria-label={isFullscreen?"退出图表全屏":"图表全屏"}>{isFullscreen?"退出全屏":"全屏"}</button></div>{fullscreenError?<p role="alert">浏览器无法切换全屏。</p>:null}<TradingChart symbol={workspace.symbol} timeframe={workspace.timeframe}/></section>
      {workspace.showRight?<Separator label="调整深度面板宽度" value={workspace.right} min={250} max={400} sign={-1} onChange={right=>update({right})}/>:<div/>}
      <aside className={`depth-panel ${workspace.showRight?"":"hidden-desktop"}`}><BookPanel symbol={workspace.symbol}/><TradesPanel symbol={workspace.symbol}/></aside>
    </div>
    <section className="terminal-bottom"><div role="tablist" aria-label="账户与研究">{["持仓","订单","策略","AI","连接"].map((tab,index)=><button key={tab} id={`account-tab-${index}`} role="tab" aria-controls="account-panel" tabIndex={workspace.tab===tab?0:-1} aria-selected={workspace.tab===tab} onKeyDown={event=>{if(event.key==="ArrowLeft"||event.key==="ArrowRight"){event.preventDefault();const tabs=["持仓","订单","策略","AI","连接"] as const;const next=(index+(event.key==="ArrowRight"?1:-1)+tabs.length)%tabs.length;update({tab:tabs[next]});document.getElementById(`account-tab-${next}`)?.focus();}}} onClick={()=>update({tab:tab as Workspace["tab"]})}>{tab}</button>)}</div><div id="account-panel" role="tabpanel" aria-labelledby={`account-tab-${["持仓","订单","策略","AI","连接"].indexOf(workspace.tab)}`} tabIndex={0}>{workspace.tab==="连接"?<p>行情由 {MarketProvider === 'bitget' ? 'Bitget' : 'Binance'} 公共 Spot 数据提供。断线与数据过期会停止显示实时价格和订单簿；历史 K 线仍可查看。</p>:<><p>{workspace.tab==="持仓"?"在模拟账户中查看持仓和账本，实时账户可进一步汇总组合估值。":workspace.tab==="订单"?"下单功能尚未开放。当前只读，不发送交易订单。":workspace.tab==="策略"?`支持 ${StrategyIds.length} 种现货策略、参数批次、留出验证和滚动样本外研究。`:"AI 服务尚未配置，目前不生成 AI 决策。"}</p>{workspace.tab==="策略"?<a href="/research">打开研究工作台 →</a>:workspace.tab==="持仓"?<a href="/portfolio">查看模拟组合 →</a>:workspace.tab==="订单"?<a href="/paper">查看 Paper 工作台 →</a>:workspace.tab==="AI"?<a href="/analysis">查看技术指标 →</a>:null}</>}</div></section>
    <ConnectionFooter symbol={workspace.symbol}/><div className="chart-attribution"><a href="https://www.tradingview.com/" target="_blank" rel="noreferrer">TradingView Lightweight Charts™</a><span>© 2025 TradingView, Inc.</span></div>
  </main>;
}
export default function Terminal() {const [client]=useState(()=>new QueryClient());return <QueryClientProvider client={client}><Workstation/></QueryClientProvider>;}
