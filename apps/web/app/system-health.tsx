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
  useEffect(() => {
    const controller = new AbortController();
    async function refresh() {
      try {
        const response = await fetch("/api/health", {cache:"no-store", signal: controller.signal});
        if (response.status !== 200 && response.status !== 503) throw new Error("unavailable");
        const data = parseHealth(await response.json());
        setHealth(data); setError(false);
      } catch { if (!controller.signal.aborted) {setHealth(null); setError(true);} }
    }
    void refresh(); const interval = setInterval(() => {void refresh();}, 10000);
    return () => {controller.abort(); clearInterval(interval);};
  }, []);
  const items = [["数据库", health?.checks.database], ["数据库迁移", health?.checks.schema], ["Redis", health?.checks.redis]] as const;
  return <section className="health" aria-labelledby="health"><h2 id="health">基础设施状态</h2><p role="status" aria-live="polite">{error ? "API 不可用，无法确认状态" : !health ? "正在检查…" : health.status === "ready" ? "工程服务就绪 · 交易未启用" : "依赖未就绪 · 交易未启用"}</p><dl>{items.map(([label,ok]) => <div key={label}><dt>{label}</dt><dd className={ok ? "ok" : "muted"}>{ok === undefined ? "未知" : ok ? "就绪" : "未就绪"}</dd></div>)}</dl></section>;
}
