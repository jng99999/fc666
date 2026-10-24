import SystemHealth from "../system-health";
import MarketMonitor from "../market-monitor";
import DeploymentCheck from "../deployment-check";
import './system.css';
export const dynamic="force-dynamic";
export default function SystemPage() {return <main className="approved-system-page"><header className="system-page-heading"><div><span>工作台设置</span><h1>数据与连接</h1><p>查看当前服务与行情状态，实盘保持关闭。</p></div><span className="system-paper-badge">PAPER · 只读检查</span></header><div className="system-status-grid"><DeploymentCheck hosted={Boolean(process.env.VERCEL)} backendConfigured={Boolean(process.env.API_BASE_URL)}/><SystemHealth/></div><MarketMonitor/><aside className="system-boundary"><strong>模拟与执行边界</strong><p>这里的按钮只重新检查连接，不修改账户、策略或执行权限。数据来源和更新时间以实际接口为准；客户端显示不能代替服务端授权。</p></aside></main>;}
