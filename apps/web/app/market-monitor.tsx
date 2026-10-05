"use client";
import {useEffect, useState} from "react";
type Quote = {price: string; change_percent: string; volume: string};
function QuoteRow({symbol}: {symbol: "BTCUSDT" | "ETHUSDT"}) {
  const [quote,setQuote] = useState<Quote | null>(null);
  const [updated,setUpdated] = useState<string | null>(null);
  useEffect(() => {
    let disposed = false;
    let pending = false;
    const controller = new AbortController();
    async function load() {
      if (pending) return;
      pending=true;
      try {
        const response = await fetch(`/api/market/snapshot?symbol=${symbol}&channel=ticker`, {cache:"no-store", signal:controller.signal});
        if (!response.ok) throw new Error("unavailable");
        const data = await response.json();
        if (data.symbol !== symbol || data.channel !== "ticker" || data.quality !== "healthy" ||
            typeof data.payload?.price !== "string" || typeof data.payload?.change_percent !== "string" ||
            typeof data.payload?.volume !== "string" || typeof data.received_at !== "string" ||
            !Number.isFinite(Date.parse(data.received_at)) ||
            Date.now()-Date.parse(data.received_at)>15000) throw new Error("invalid or stale quote");
        if (!disposed) {setQuote(data.payload as Quote);setUpdated(data.received_at);}
      } catch {if (!disposed) {setQuote(null);setUpdated(null);}}
      finally {pending=false;}
    }
    void load();const timer=setInterval(() => {void load();},2000);
    return () => {disposed=true;controller.abort();clearInterval(timer);};
  },[symbol]);
  return <tr><th scope="row">{symbol.replace("USDT","/USDT")}</th><td>{quote?.price ?? "—"}</td><td>{quote ? `${quote.change_percent}%` : "—"}</td><td>{updated ? "实时公共行情" : "连接中或数据不可用"}</td></tr>;
}
export default function MarketMonitor() {
  return <section className="health market-monitor" aria-labelledby="market-heading"><h2 id="market-heading">真实市场数据 · Binance Spot</h2><p>行情来自公共交易所数据，仅供查看；没有账户连接或交易执行。</p><table><thead><tr><th scope="col">交易对</th><th scope="col">价格 (USDT)</th><th scope="col">24h 涨跌</th><th scope="col">数据状态</th></tr></thead><tbody><QuoteRow symbol="BTCUSDT"/><QuoteRow symbol="ETHUSDT"/></tbody></table></section>;
}
