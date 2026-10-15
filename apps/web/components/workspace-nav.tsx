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
  const accounts=path.startsWith('/paper')||path.startsWith('/portfolio');
  const items=[['/','行情终端',path==='/'],['/research','策略研究',path.startsWith('/research')],
    ['/replay','历史回放',path.startsWith('/replay')],['/paper/realtime','模拟账户',accounts],
    ['/system','系统状态',path.startsWith('/system')]] as const;
  return <>
    <header className="workspace-header">
      <Link className="workspace-brand" href="/" aria-label="FC666 工作台首页">FC666<span>量化工作台</span></Link>
      <nav className="workspace-navigation" aria-label="工作台主导航">{items.map(([href,label,active])=><Link key={href} href={href} aria-current={active?'page':undefined}>{label}</Link>)}</nav>
      <div className="workspace-mode"><span>{MarketProvider==='bitget'?'Bitget':'Binance'} 现货</span><strong>实盘关闭</strong></div>
    </header>
    {accounts?<nav className="account-navigation" aria-label="模拟账户功能">{accountTabs.map(([href,label])=><Link key={href} href={href} aria-current={path===href?'page':undefined}>{label}</Link>)}</nav>:null}
  </>;
}
