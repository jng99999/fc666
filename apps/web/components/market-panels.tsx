"use client";
import {useEffect,useState} from "react";
import {useMarketStore} from "../lib/market-store";
import {keyFor,Symbols,type SymbolName,type TickerSnapshot,type BookSnapshot,type TradeSnapshot,type Snapshot} from "../lib/market";
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
export function Watchlist({symbol,onSelect}:{symbol:SymbolName;onSelect:(symbol:SymbolName)=>void}) {
  return <><h2 className="panel-title">自选市场 <span>SPOT</span></h2>{Symbols.map(s=><WatchRow key={s} symbol={s} selected={s===symbol} onSelect={()=>onSelect(s)}/>)}<div className="panel-note"><strong>公共行情 · 只读</strong><p>BTC / ETH 现货。衍生品、账户和交易执行尚未接入。</p><a href="/system">查看系统服务状态 ↗</a></div></>;
}
export function TickerHeader({symbol}:{symbol:SymbolName}) {
  const value=useFresh(symbol,"ticker") as TickerSnapshot|undefined;
  return <div className="ticker-head"><strong>{symbol.replace("USDT","/USDT")}</strong><span className="ticker-price mono" data-testid="live-price">{decimalDisplay(value?.payload.price)}</span><div><small>24h 涨跌</small><span className={Number(value?.payload.change_percent ?? 0)<0?"sell":"buy"}>{value?`${value.payload.change_percent}%`:"—"}</span></div><div className="volume-head"><small>24h 成交量</small><span className="mono">{decimalDisplay(value?.payload.volume)}</span></div><span className="market-state">{value?"公共行情在线":"等待真实行情"}</span></div>;
}
export function BookPanel({symbol}:{symbol:SymbolName}) {
  const value=useFresh(symbol,"book") as BookSnapshot|undefined;
  return <section className="book-panel" data-received-at={value?.received_at} data-generation={value?.generation} data-sequence={value?.sequence}><h2 className="panel-title">订单簿 <span>{value?"已同步":"不可用"}</span></h2>{!value?<p className="empty">订单簿未就绪或已失效。正在等待重新同步。</p>:<><div className="book-columns"><span>价格 (USDT)</span><span>数量</span></div><div className="book-side asks" data-testid="orderbook-asks">{value.payload.asks.slice(0,10).reverse().map(([p,q])=><div className="book-level" key={p}><span className="sell">{decimalDisplay(p)}</span><span>{decimalDisplay(q)}</span></div>)}</div><div className="book-divider">最优买卖价 · 实际市场深度</div><div className="book-side bids" data-testid="orderbook-bids">{value.payload.bids.slice(0,10).map(([p,q])=><div className="book-level" key={p}><span className="buy">{decimalDisplay(p)}</span><span>{decimalDisplay(q)}</span></div>)}</div><p className="depth-note">显示 10 档；初始快照最多 1,000 档，非完整市场流动性。</p></>}</section>;
}
export function TradesPanel({symbol}:{symbol:SymbolName}) {
  const value=useFresh(symbol,"trade") as TradeSnapshot|undefined;
  return <section className="trades-panel"><h2 className="panel-title">最近成交 <span>UTC</span></h2>{!value?<p className="empty">等待真实成交数据。</p>:<table><thead><tr><th>价格</th><th>数量</th><th>时间</th></tr></thead><tbody data-testid="recent-trades">{value.payload.trades.slice(-15).reverse().map(t=><tr key={t.trade_id}><td className={t.aggressor_side==="BUY"?"buy":"sell"}>{decimalDisplay(t.price)}</td><td>{decimalDisplay(t.quantity)}</td><td>{new Date(t.event_time).toISOString().slice(11,19)}</td></tr>)}</tbody></table>}</section>;
}
export function ConnectionFooter({symbol}:{symbol:SymbolName}) {
  const connection=useMarketStore(state=>state.connection);
  const ticker=useFresh(symbol,"ticker"),book=useFresh(symbol,"book");
  return <footer className="terminal-footer"><span className={connection==="connected"?"buy":"warning"}>网关 {connection==="connected"?"已连接":connection==="retrying"?"重连中":"连接中"}</span><span>市场数据 {ticker && book?"健康":"不可用 / 等待同步"}</span><span>策略 未实现</span><span>风控 未实现</span><span>执行 禁用</span><span className="mode-label">PAPER · 未启用交易</span></footer>;
}
