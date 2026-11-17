import SystemHealth from "../system-health";
import MarketMonitor from "../market-monitor";
import DeploymentCheck from "../deployment-check";
import Link from 'next/link';
import apiStyles from './api/page.module.css';
import './system.css';
export const dynamic="force-dynamic";
export default function SystemPage() {return <main className="approved-system-page"><header className="system-page-heading"><div><span>工作台设置</span><h1>数据与连接</h1><p>查看当前服务与行情状态，实盘保持关闭。</p></div><span className="system-paper-badge">PAPER · 只读检查</span></header><Link href="/system/api" className={apiStyles.entry}><div><strong>API 接入</strong><p>AI 模型、新闻日历与黄金历史行情的配置位置、模板和接入状态。</p></div><span>管理接入 →</span></Link><div className="system-status-grid"><DeploymentCheck hosted={Boolean(process.env.VERCEL)} backendConfigured={Boolean(process.env.API_BASE_URL)}/><SystemHealth/></div><MarketMonitor/><aside className="system-boundary"><strong>模拟与执行边界</strong><p>这里的按钮只重新检查连接，不修改账户、策略或执行权限。数据来源和更新时间以实际接口为准；客户端显示不能代替服务端授权。</p></aside></main>;}
