"use client";
import Link from 'next/link';
import {usePathname} from 'next/navigation';
import {MarketProvider} from '../lib/market';

const accountTabs = [
  ['/paper/realtime','实时模拟'], ['/paper','历史模拟'],
  ['/portfolio','组合估值'], ['/portfolio/history','场景记录'],
] as const;

export default function WorkspaceNav() {
  const path=usePathname();
  const gold=path.startsWith('/gold');
  const accounts=path.startsWith('/paper')||path.startsWith('/portfolio');
  const items=[['/','行情终端',path==='/'],['/analysis','行情分析',path.startsWith('/analysis')],['/research','策略研究',path.startsWith('/research')],
    ['/replay','历史回放',path.startsWith('/replay')],['/paper/realtime','模拟账户',accounts||gold],
    ['/system','系统状态',path.startsWith('/system')]] as const;
  return <>
    <header className="workspace-header">
      <Link className="workspace-brand" href="/" aria-label="FC666 工作台首页">FC666<span>量化工作台</span></Link>
      <nav className="workspace-navigation" aria-label="工作台主导航">{items.map(([href,label,active],index)=><Link key={href} href={href} aria-current={active?'page':undefined} aria-label={label}><svg className="nav-mobile-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d={['M5 4v16M3 8h4v7H3zM12 2v20M10 5h4v8h-4zM19 4v16M17 11h4v6h-4z','M3 19h18M5 15l5-6 4 3 5-8','M9 3h6M10 3v6l-6 10a1 1 0 0 0 1 2h14a1 1 0 0 0 1-2L14 9V3M8 15h8','M5 7a8 8 0 1 1-1 9M5 3v5H1M10 8l6 4-6 4z','M4 6h16v15H4zM8 6V3h8v3M4 11h16M10 11v3h4v-3','M12 3v3M12 18v3M3 12h3M18 12h3M5.6 5.6l2.1 2.1M16.3 16.3l2.1 2.1M5.6 18.4l2.1-2.1M16.3 7.7l2.1-2.1M16 12a4 4 0 1 1-8 0 4 4 0 0 1 8 0'][index]}/></svg><span className="nav-label-desktop">{label}</span><span className="nav-label-mobile">{['行情','分析','研究','回放','账户','系统'][index]}</span></Link>)}</nav>
      <div className="workspace-mode"><span>{gold?'XAU/USD · USD 模拟':`${MarketProvider==='bitget'?'Bitget':'Binance'} 现货`}</span><strong>实盘关闭</strong></div>
    </header>
    {path==='/'||path.startsWith('/analysis')||gold?<nav className="workspace-asset-navigation" aria-label="资产市场"><Link href="/" aria-current={!gold?'page':undefined}>数字资产</Link><Link href="/gold" aria-current={gold?'page':undefined}>黄金 XAU/USD</Link></nav>:null}
    {accounts?<nav className="account-navigation" aria-label="模拟账户功能">{accountTabs.map(([href,label])=><Link key={href} href={href} aria-current={path===href?'page':undefined}>{label}</Link>)}</nav>:null}
  </>;
}
