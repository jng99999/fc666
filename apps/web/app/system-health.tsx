"use client";
import {useEffect, useState} from "react";
type Health = {status: "ready" | "not_ready"; checks: {database: boolean; schema: boolean; redis: boolean}; trading_enabled: false};
function parseHealth(value: unknown): Health {
  if (!value || typeof value !== "object") throw new Error("invalid response");
  const v = value as Partial<Health>;
  if ((v.status !== "ready" && v.status !== "not_ready") || !v.checks ||
      typeof v.checks.database !== "boolean" || typeof v.checks.schema !== "boolean" ||
      typeof v.checks.redis !== "boolean" || v.trading_enabled !== false) throw new Error("invalid response");
  return v as Health;
}
export default function SystemHealth() {
  const [health, setHealth] = useState<Health | null>(null);
  const [error, setError] = useState(false);
  const [checking,setChecking]=useState(true),[revision,setRevision]=useState(0),[checkedAt,setCheckedAt]=useState<string|null>(null);
  useEffect(() => {
    const controller = new AbortController();
    let pending=false;
    async function refresh() {
      if(pending)return;pending=true;setChecking(true);
      try {
        const response = await fetch("/api/health", {cache:"no-store", signal: AbortSignal.any([controller.signal,AbortSignal.timeout(8000)])});
        if (response.status !== 200 && response.status !== 503) throw new Error("unavailable");
        const data = parseHealth(await response.json());
        if(!controller.signal.aborted){setHealth(data);setError(false);setCheckedAt(new Date().toISOString());}
      } catch { if (!controller.signal.aborted) {setHealth(null); setError(true);} }
      finally {pending=false;if(!controller.signal.aborted)setChecking(false);}
    }
    void refresh(); const interval = setInterval(() => {void refresh();}, 10000);
    return () => {controller.abort(); clearInterval(interval);};
  }, [revision]);
  const items = [["数据库", health?.checks.database], ["数据库迁移", health?.checks.schema], ["Redis", health?.checks.redis]] as const;
  return <section className="health system-infrastructure" aria-labelledby="health"><div className="system-panel-heading"><h2 id="health">基础设施状态</h2><span className="system-small-badge">自动检查 · 10秒</span></div><p className="system-status-copy" role="status" aria-live="polite">{error ? "API 不可用，无法确认状态" : !health ? "正在检查…" : health.status === "ready" ? "工程服务就绪 · 交易未启用" : "依赖未就绪 · 交易未启用"}</p><dl>{items.map(([label,ok]) => <div key={label}><dt>{label}</dt><dd className={ok ? "ok" : "muted"}>{ok === undefined ? "未知" : ok ? "就绪" : "未就绪"}</dd></div>)}</dl><div className="system-check-footer"><small>{checkedAt&&!error?<>最近成功检查 <time dateTime={checkedAt}>{new Date(checkedAt).toISOString().slice(11,19)} UTC</time></>:'尚无可确认的服务状态'}</small><button type="button" disabled={checking} onClick={()=>setRevision(v=>v+1)}>{checking?'检查中…':'刷新服务状态'}</button></div></section>;
}
