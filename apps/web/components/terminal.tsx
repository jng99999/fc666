"use client";
import {useEffect,useRef,useState,type PointerEvent,type KeyboardEvent} from "react";
import {QueryClient,QueryClientProvider} from "@tanstack/react-query";
import {z} from "zod";
import {Symbols,Intervals,type SymbolName,type Interval} from "../lib/market";
import MarketSocket from "./market-socket";
import TradingChart from "./trading-chart";
import {Watchlist,TickerHeader,BookPanel,TradesPanel,ConnectionFooter} from "./market-panels";
const WorkspaceSchema=z.object({symbol:z.enum(Symbols),timeframe:z.enum(Intervals),left:z.number().min(160).max(300),right:z.number().min(250).max(400),showLeft:z.boolean(),showRight:z.boolean(),tab:z.enum(["持仓","订单","策略","AI","连接"]),mobilePanel:z.enum(["chart","book","trades"]).default("chart") });
type Workspace=z.infer<typeof WorkspaceSchema>;
const INITIAL:Workspace={symbol:"BTCUSDT",timeframe:"1m",left:200,right:300,showLeft:true,showRight:true,tab:"持仓",mobilePanel:"chart"};
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
  function update(changes:Partial<Workspace>) {setWorkspace(w=>({...w,...changes}));}
  async function fullscreen() {try{setFullscreenError(false);if(document.fullscreenElement)await document.exitFullscreen();else await panel.current?.requestFullscreen();}catch{setFullscreenError(true);}}
  return <main className="terminal"><MarketSocket timeframe={workspace.timeframe}/><header className="terminal-top"><a className="brand" href="/">FC666</a><span className="top-label">QUANT WORKSTATION</span><span className="read-only">只读终端</span><nav aria-label="面板控制"><button aria-pressed={workspace.showLeft} onClick={()=>update({showLeft:!workspace.showLeft})}>自选</button><button aria-pressed={workspace.showRight} onClick={()=>update({showRight:!workspace.showRight})}>深度 / 成交</button><a href="/research">研究</a><a href="/replay">回放</a><a href="/system">系统</a></nav><span className="mode-pill">PAPER · 交易未启用</span></header><TickerHeader symbol={workspace.symbol}/>
    <div className="mobile-tabs" role="group" aria-label="移动端市场视图"><select aria-label="移动端交易对" value={workspace.symbol} onChange={e=>update({symbol:e.target.value as SymbolName})}>{Symbols.map(s=><option key={s}>{s}</option>)}</select>{([["chart","图表"],["book","订单簿"],["trades","成交"]] as const).map(([value,label])=><button key={value} aria-pressed={workspace.mobilePanel===value} onClick={()=>update({mobilePanel:value})}>{label}</button>)}</div>
    <div className={`terminal-workspace mobile-view-${workspace.mobilePanel}`} style={{gridTemplateColumns:`${workspace.showLeft?workspace.left:0}px ${workspace.showLeft?6:0}px minmax(0,1fr) ${workspace.showRight?6:0}px ${workspace.showRight?workspace.right:0}px`}}>
      {workspace.showLeft?<aside className="watch-panel"><Watchlist symbol={workspace.symbol} onSelect={symbol=>update({symbol})}/></aside>:<div/>}
      {workspace.showLeft?<Separator label="调整自选面板宽度" value={workspace.left} min={160} max={300} sign={1} onChange={left=>update({left})}/>:<div/>}
      <section className="chart-panel" ref={panel}><div className="chart-toolbar"><label>交易对<select aria-label="交易对" value={workspace.symbol} onChange={e=>update({symbol:e.target.value as SymbolName})}>{Symbols.map(s=><option key={s}>{s}</option>)}</select></label><div className="timeframes" role="group" aria-label="图表周期">{Intervals.map(i=><button key={i} aria-pressed={workspace.timeframe===i} className={workspace.timeframe===i?"active":""} onClick={()=>update({timeframe:i})}>{i}</button>)}</div><button onClick={fullscreen} aria-label={isFullscreen?"退出图表全屏":"图表全屏"}>{isFullscreen?"退出全屏":"全屏"}</button></div>{fullscreenError?<p role="alert">浏览器无法切换全屏。</p>:null}<TradingChart symbol={workspace.symbol} timeframe={workspace.timeframe}/></section>
      {workspace.showRight?<Separator label="调整深度面板宽度" value={workspace.right} min={250} max={400} sign={-1} onChange={right=>update({right})}/>:<div/>}
      <aside className={`depth-panel ${workspace.showRight?"":"hidden-desktop"}`}><BookPanel symbol={workspace.symbol}/><TradesPanel symbol={workspace.symbol}/></aside>
    </div>
    <section className="terminal-bottom"><div role="tablist" aria-label="账户与研究">{["持仓","订单","策略","AI","连接"].map((tab,index)=><button key={tab} id={`account-tab-${index}`} role="tab" aria-controls="account-panel" tabIndex={workspace.tab===tab?0:-1} aria-selected={workspace.tab===tab} onKeyDown={event=>{if(event.key==="ArrowLeft"||event.key==="ArrowRight"){event.preventDefault();const tabs=["持仓","订单","策略","AI","连接"] as const;const next=(index+(event.key==="ArrowRight"?1:-1)+tabs.length)%tabs.length;update({tab:tabs[next]});document.getElementById(`account-tab-${next}`)?.focus();}}} onClick={()=>update({tab:tab as Workspace["tab"]})}>{tab}</button>)}</div><div id="account-panel" role="tabpanel" aria-labelledby={`account-tab-${["持仓","订单","策略","AI","连接"].indexOf(workspace.tab)}`} tabIndex={0}>{workspace.tab==="连接"?<p>行情由 Binance 公共 Spot 数据提供。断线与数据过期会停止显示实时价格和订单簿；历史 K 线仍可查看。</p>:<p>{workspace.tab==="持仓"?"尚未创建模拟账户，未连接实盘账户。":workspace.tab==="订单"?"下单功能尚未开放。当前只读，不发送交易订单。":workspace.tab==="策略"?"策略实验室与回测将在后续阶段交付。":"AI 分析尚未接入，不生成市场结论。"}<small className="not-implemented">Not Implemented</small></p>}</div></section>
    <ConnectionFooter symbol={workspace.symbol}/><div className="chart-attribution">TradingView Lightweight Charts™ · Copyright (с) 2025 TradingView, Inc. <a href="https://www.tradingview.com/" target="_blank" rel="noreferrer">TradingView</a></div>
  </main>;
}
export default function Terminal() {const [client]=useState(()=>new QueryClient());return <QueryClientProvider client={client}><Workstation/></QueryClientProvider>;}
