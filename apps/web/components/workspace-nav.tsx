"use client";
import Link from 'next/link';
import {usePathname} from 'next/navigation';
import {useEffect,useRef,useState,type ReactNode} from 'react';
import {Symbols} from '../lib/market';
import {StrategyCatalog,StrategyIds} from '../lib/research-strategies';

const accountTabs=[['/paper/realtime','实时模拟'],['/paper','历史模拟'],['/portfolio','组合估值'],['/portfolio/history','场景记录']] as const;
type IconName='market'|'analysis'|'strategy'|'replay'|'account'|'settings'|'search'|'close'|'down';
function Icon({name}:{name:IconName}){
 const paths:Record<IconName,string>={
  market:'M4 13h3v7H4zM10.5 8h3v12h-3zM17 3h3v17h-3z',
  analysis:'M3 4v16h18M6 14l5-6 4 4 6-8',
  strategy:'M12 5C9 3 6 3 3 4v15c3-1 6-1 9 1 3-2 6-2 9-1V4c-3-1-6-1-9 1Zm0 0v15',
  replay:'M21 12a9 9 0 1 1-18 0 9 9 0 0 1 18 0ZM10 8l6 4-6 4Z',
  account:'M16 7a4 4 0 1 1-8 0 4 4 0 0 1 8 0ZM4 21v-2a8 8 0 0 1 16 0v2Z',
  settings:'m10 3-.7 2.4-2 .9L5 5.8 2.9 9.3l1.7 1.8v2l-1.7 1.7L5 18.4l2.3-.5 2 .9L10 21h4l.7-2.2 2-.9 2.3.5 2.1-3.6-1.7-1.7v-2l1.7-1.8L19 5.8l-2.3.5-2-.9L14 3ZM15 12a3 3 0 1 1-6 0 3 3 0 0 1 6 0Z',
  search:'M17 10.5a6.5 6.5 0 1 1-13 0 6.5 6.5 0 0 1 13 0ZM16 16l5 5',
  close:'m6 6 12 12M6 18 18 6',down:'m6 9 6 6 6-6',
 };
 return <svg className="workspace-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.75" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d={paths[name]}/></svg>;
}
export default function WorkspaceNav({children}:{children:ReactNode}){
 const path=usePathname();
 const [search,setSearch]=useState(''),[searchError,setSearchError]=useState(''),[ready,setReady]=useState(false);
 const dialog=useRef<HTMLDialogElement>(null),input=useRef<HTMLInputElement>(null);
 const gold=path.startsWith('/gold'),goldResearch=path==='/gold/ppo'||path.startsWith('/gold/ppo/')||path==='/gold/research'||path.startsWith('/gold/research/');
 const accounts=path.startsWith('/paper')||path.startsWith('/portfolio');
 const items=[['/','行情','行情终端','market',path==='/'||path==='/gold'],['/analysis','分析','行情分析','analysis',path.startsWith('/analysis')],['/research','策略','策略研究','strategy',path.startsWith('/research')||goldResearch],['/replay','回放','历史回放','replay',path.startsWith('/replay')],['/portfolio','账户','账户与资产','account',accounts||(gold&&!goldResearch&&path!=='/gold')]] as const;
 const title=items.find(item=>item[4])?.[1]??(path.startsWith('/system')?'设置':'工作台');
 const query=search.trim().toLowerCase().replace(/[\s/]/g,'');
 const products=[...Symbols.map(symbol=>({label:symbol.replace('USDT',' / USDT'),match:symbol.toLowerCase(),href:'/analysis?symbol='+symbol,note:'公共现货 · USDT'})),{label:'黄金 XAU / USD',match:'xauusd黄金',href:'/gold',note:'独立黄金行情 · USD'}].filter(item=>!query||item.match.includes(query));
 const strategies=StrategyIds.filter(id=>!query||id.includes(query)||StrategyCatalog[id].name.toLowerCase().includes(query));
 function openSearch(){setSearch('');setSearchError('');dialog.current?.showModal();input.current?.focus();}
 useEffect(()=>{setReady(true);const listener=(event:globalThis.KeyboardEvent)=>{if((event.metaKey||event.ctrlKey)&&event.key.toLowerCase()==='k'){event.preventDefault();dialog.current?.showModal();input.current?.focus();}};document.addEventListener('keydown',listener);return()=>document.removeEventListener('keydown',listener);},[]);
 useEffect(()=>{dialog.current?.close();setSearchError('');},[path]);
 function submit(){const symbol=search.toUpperCase().replace(/[\s/]/g,'');if(['XAU','XAUUSD','黄金'].includes(symbol)){window.location.assign('/gold');return;}if(Symbols.some(value=>value===symbol)){window.location.assign('/analysis?symbol='+symbol);return;}if(products.length+strategies.length===1){window.location.assign(products[0]?.href??('/research?strategy='+strategies[0]));return;}setSearchError('请选择搜索结果，或输入完整交易对。');}
 return <>
  <header className="approved-workspace-header">
   <Link className="approved-workspace-brand" href="/" aria-label="FC666 工作台首页">FC<span>666</span></Link>
   <div className="approved-workspace-header-tools">
    <button className="workspace-search-trigger" disabled={!ready} onClick={openSearch} aria-label="搜索品种、策略"><Icon name="search"/><span>搜索品种 / 交易对 / 策略</span><kbd>⌘ K</kbd></button>
    <span className="workspace-live-badge workspace-desktop-status">实盘关闭</span>
    <Link className="workspace-system-link workspace-icon-button" href="/system" aria-label="数据与连接状态"><Icon name="settings"/></Link>
    <Link className="workspace-profile" href="/system" aria-label="FC666 工作台状态"><span className="workspace-avatar">F</span><span>FC666</span><Icon name="down"/></Link>
   </div>
  </header>
  <aside className="approved-workspace-navigation" aria-label="工作台主导航"><nav className="workspace-primary-nav">{items.map(([href,label,aria,icon,active])=><Link key={href} href={href} aria-label={aria} aria-current={active?'page':undefined}><Icon name={icon}/><span>{label}</span></Link>)}</nav><Link className="workspace-settings" href="/system" aria-label="设置与连接状态" aria-current={path.startsWith('/system')?'page':undefined}><Icon name="settings"/></Link></aside>
  <div className="approved-workspace-content"><div className="workspace-mobile-title"><h1>{title}</h1><span className="workspace-live-badge">实盘关闭</span><button className="workspace-icon-button" disabled={!ready} onClick={openSearch} aria-label="搜索品种"><Icon name="search"/></button></div>
  {gold?<nav className="workspace-asset-navigation" aria-label="资产市场"><Link href="/">数字资产</Link><Link href="/gold" aria-current="page">黄金 XAU/USD</Link></nav>:null}
  {accounts&&path!=='/portfolio'?<nav className="account-navigation" aria-label="模拟账户功能">{accountTabs.map(([href,label])=><Link key={href} href={href} aria-current={path===href?'page':undefined}>{label}</Link>)}</nav>:null}
  {children}</div>
  <dialog ref={dialog} className="workspace-search-dialog" aria-labelledby="workspace-search-title" onClick={event=>{if(event.target===event.currentTarget){const box=event.currentTarget.getBoundingClientRect();if(event.clientX<box.left||event.clientX>box.right||event.clientY<box.top||event.clientY>box.bottom)event.currentTarget.close();}}}>
   <header><h2 id="workspace-search-title">搜索品种与策略</h2><button className="workspace-icon-button" aria-label="关闭搜索" onClick={()=>dialog.current?.close()}><Icon name="close"/></button></header>
   <form role="search" onSubmit={event=>{event.preventDefault();submit();}}><label htmlFor="workspace-search" className="workspace-visually-hidden">搜索品种、交易对或策略</label><div className="workspace-search-field"><Icon name="search"/><input ref={input} id="workspace-search" autoComplete="off" value={search} onChange={event=>{setSearch(event.target.value);setSearchError('');}} placeholder="例如 BTCUSDT、黄金、均线"/><button type="submit">搜索</button></div>{searchError?<p role="alert">{searchError}</p>:null}</form>
   <div className="workspace-search-results"><h3>品种</h3>{products.length?products.map(item=><a key={item.href} href={item.href}><strong>{item.label}</strong><small>{item.note}</small></a>):<p>没有匹配品种</p>}<h3>策略</h3>{strategies.length?strategies.map(id=><a key={id} href={'/research?strategy='+id}><strong>{StrategyCatalog[id].name}</strong><small>内置策略 · 历史回测</small></a>):<p>没有匹配策略</p>}</div>
  </dialog>
 </>;
}
