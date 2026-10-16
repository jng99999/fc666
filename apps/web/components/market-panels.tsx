"use client";
import {useEffect,useState} from "react";
import {useMarketStore} from "../lib/market-store";
import {keyFor,Symbols,MarketProvider,type SymbolName,type TickerSnapshot,type BookSnapshot,type TradeSnapshot,type Snapshot} from "../lib/market";
export function decimalDisplay(value:string|undefined) {
  if(value===undefined)return "—";
  const [whole,fraction] = value.split(".");
  const grouped=whole.replace(/\B(?=(\d{3})+(?!\d))/g,",");
  const trimmed=fraction?.replace(/0+$/,"");return grouped+(trimmed?`.${trimmed}`:"");
}
export function useFresh(symbol:SymbolName,channel:string):Snapshot|undefined {
  const value=useMarketStore(state=>state.snapshots[keyFor(symbol,channel)]);
  const connection=useMarketStore(state=>state.connection);
  const [now,setNow]=useState(Date.now());
  useEffect(()=>{const timer=setInterval(()=>setNow(Date.now()),1000);return ()=>clearInterval(timer);},[]);
  return connection==="connected" && value && now-Date.parse(value.received_at)<15000 ? value : undefined;
}
function WatchRow({symbol,selected,onSelect}:{symbol:SymbolName;selected:boolean;onSelect:()=>void}) {
  const value=useFresh(symbol,"ticker") as TickerSnapshot|undefined;
  return <button className={`watch-row ${selected?"selected":""}`} onClick={onSelect} aria-pressed={selected}><span>{symbol.replace("USDT","/USDT")}</span><span className="mono">{decimalDisplay(value?.payload.price)}</span><small className={Number(value?.payload.change_percent ?? 0)<0?"sell":"buy"}>{value?`${value.payload.change_percent}%`:"数据不可用"}</small></button>;
}
export function Watchlist({symbol,symbols,onSelect,onChange}:{symbol:SymbolName;symbols:SymbolName[];onSelect:(symbol:SymbolName)=>void;onChange:(symbols:SymbolName[])=>void}) {
  const [editing,setEditing]=useState(false),[query,setQuery]=useState('');
  const matches=Symbols.filter(s=>s.includes(query.trim().toUpperCase().replace('/','')));
  return <><h2 className="panel-title">自选市场 <span>{symbols.length} / {Symbols.length}</span></h2><div className="watch-actions"><button aria-expanded={editing} onClick={()=>setEditing(v=>!v)}>{editing?'完成管理':'添加 / 管理产品'}</button></div>{editing?<div className="watch-editor"><label>搜索现货产品<input aria-label="搜索现货产品" placeholder="例如 SOL / USDT" value={query} onChange={e=>setQuery(e.target.value)}/></label><div className="watch-candidates">{matches.map(s=><button key={s} aria-pressed={symbols.includes(s)} disabled={symbols.length===1&&symbols.includes(s)} onClick={()=>onChange(symbols.includes(s)?symbols.filter(v=>v!==s):[...symbols,s])}><span>{s.replace('USDT','/USDT')}</span><span>{symbols.includes(s)?'移除':'添加'}</span></button>)}</div>{!matches.length?<p role="status">没有匹配产品。当前支持 {Symbols.length} 个现货市场。</p>:null}<small>至少保留一个；自选仅保存于当前浏览器。</small></div>:null}{symbols.map(s=><WatchRow key={s} symbol={s} selected={s===symbol} onSelect={()=>onSelect(s)}/>)}<div className="panel-note"><strong>{MarketProvider === 'bitget' ? 'Bitget' : 'Binance'} 公共行情 · 只读</strong><p>{Symbols.length} 个 USDT 现货市场。实盘关闭。</p><a href={`/analysis?symbol=${symbol}`}>分析当前产品 →</a><br/><a href={`/research?symbol=${symbol}`}>回测当前产品 →</a><br/><a href="/system">查看系统服务状态 ↗</a></div></>;
}
export function TickerHeader({symbol}:{symbol:SymbolName}) {
  const value=useFresh(symbol,"ticker") as TickerSnapshot|undefined;
  return <div className="ticker-head"><strong>{symbol.replace("USDT","/USDT")}</strong><span className="ticker-price mono" data-testid="live-price">{decimalDisplay(value?.payload.price)}</span><div><small>24h 涨跌</small><span className={Number(value?.payload.change_percent ?? 0)<0?"sell":"buy"}>{value?`${Number(value.payload.change_percent).toFixed(2)}%`:"—"}</span></div><div className="volume-head"><small>24h 成交量</small><span className="mono">{decimalDisplay(value?.payload.volume)}</span></div><span className="market-state">{value?"公共行情在线":"等待真实行情"}</span></div>;
}
export function BookPanel({symbol}:{symbol:SymbolName}) {
  const value=useFresh(symbol,"book") as BookSnapshot|undefined;
  return <section className="book-panel" data-received-at={value?.received_at} data-generation={value?.generation} data-sequence={value?.sequence}><h2 className="panel-title">订单簿 <span>{value?"已同步":"不可用"}</span></h2>{!value?<p className="empty">订单簿未就绪或已失效。正在等待重新同步。</p>:<><div className="book-columns"><span>价格 (USDT)</span><span>数量</span></div><div className="book-side asks" data-testid="orderbook-asks">{value.payload.asks.slice(0,10).reverse().map(([p,q])=><div className="book-level" key={p}><span className="sell">{decimalDisplay(p)}</span><span>{decimalDisplay(q)}</span></div>)}</div><div className="book-divider">最优买卖价 · 实际市场深度</div><div className="book-side bids" data-testid="orderbook-bids">{value.payload.bids.slice(0,10).map(([p,q])=><div className="book-level" key={p}><span className="buy">{decimalDisplay(p)}</span><span>{decimalDisplay(q)}</span></div>)}</div><p className="depth-note">显示 10 档；快照最多 {value.payload.snapshot_depth_limit.toLocaleString()} 档，非完整市场流动性。</p></>}</section>;
}
export function TradesPanel({symbol}:{symbol:SymbolName}) {
  const value=useFresh(symbol,"trade") as TradeSnapshot|undefined;
  return <section className="trades-panel"><h2 className="panel-title">最近成交 <span>UTC</span></h2>{!value?<p className="empty">等待真实成交数据。</p>:<table><thead><tr><th>价格</th><th>数量</th><th>时间</th></tr></thead><tbody data-testid="recent-trades">{value.payload.trades.slice(-15).reverse().map(t=><tr key={t.trade_id}><td className={t.aggressor_side==="BUY"?"buy":"sell"}>{decimalDisplay(t.price)}</td><td>{decimalDisplay(t.quantity)}</td><td>{new Date(t.event_time).toISOString().slice(11,19)}</td></tr>)}</tbody></table>}</section>;
}
export function ConnectionFooter({symbol}:{symbol:SymbolName}) {
  const connection=useMarketStore(state=>state.connection);
  const ticker=useFresh(symbol,"ticker"),book=useFresh(symbol,"book");
  return <footer className="terminal-footer"><span className={connection==="connected"?"buy":"warning"}>网关 {connection==="connected"?"已连接":connection==="retrying"?"重连中":"连接中"}</span><span>市场数据 {ticker && book?"健康":"不可用 / 等待同步"}</span><a href="/research">策略研究</a><span>账户风控未连接</span><span>执行 禁用</span><span className="mode-label">PAPER · 未启用交易</span></footer>;
}
