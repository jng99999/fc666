import SystemHealth from "../system-health";
import MarketMonitor from "../market-monitor";
import DeploymentCheck from "../deployment-check";
export const dynamic="force-dynamic";
export default function SystemPage() {return <main><header><strong>FC666</strong><a href="/">返回终端</a></header><section className="intro"><h1>系统服务状态</h1><p>工程依赖与真实公共行情。交易执行保持禁用。</p></section><DeploymentCheck hosted={Boolean(process.env.VERCEL)} backendConfigured={Boolean(process.env.API_BASE_URL)}/><SystemHealth/><MarketMonitor/></main>;}
