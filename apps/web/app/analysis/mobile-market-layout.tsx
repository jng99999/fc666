'use client';
import Link from 'next/link';
import type {ReactNode} from 'react';
import './mobile-market-layout.css';

function PhoneIcon({name}:{name:'back'|'down'|'tools'|'indicators'|'layers'|'more'}){
 const paths={back:'M19 12H5m6-6-6 6 6 6',down:'m7 10 5 5 5-5',tools:'M4 4h6v6H4ZM14 4h6v6h-6ZM4 14h6v6H4ZM14 14h6v6h-6Z',indicators:'M4 17 9 11l4 4 7-10M4 21h17',layers:'m3 8 9-5 9 5-9 5Zm0 5 9 5 9-5M3 18l9 5 9-5',more:'M5 12h.01M12 12h.01M19 12h.01'};
 return <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d={paths[name]}/></svg>;
}
type HeaderProps={symbol:string;timeframe:string;analysis?:boolean;onBack:()=>void;onProducts:()=>void;onTools:()=>void;disabled:boolean};
function ProductHeader({symbol,timeframe,analysis=false,onBack,onProducts,onTools,disabled}:HeaderProps){
 return <><header className="phone-product-header"><button type="button" aria-label="返回行情列表" onClick={onBack}><PhoneIcon name="back"/></button><button type="button" className="phone-product-picker" aria-label="选择产品" disabled={disabled} onClick={onProducts}><strong>{symbol}</strong><span>现货</span><PhoneIcon name="down"/></button><small className="phone-paper-label">实盘关闭</small><button type="button" aria-label="行情工具与更多" onClick={onTools}><PhoneIcon name="more"/></button></header><nav className="phone-detail-tabs" aria-label="品种页面"><Link href={`/?symbol=${symbol}&timeframe=${timeframe}`} aria-current={!analysis?'page':undefined}>行情</Link><Link href={`/analysis?symbol=${symbol}&timeframe=${timeframe}`} aria-current={analysis?'page':undefined}>分析</Link><Link href={`/research?symbol=${symbol}`}>策略</Link><Link href="/replay">回放</Link></nav></>;
}
type DetailProps=HeaderProps&{quote:ReactNode;periods:ReactNode;chart:ReactNode;onIndicators:()=>void;onLayers:()=>void};
export function MobileQuoteDetail(props:DetailProps){
 return <div className="phone-quote-detail"><ProductHeader {...props}/><div className="phone-price-summary">{props.quote}</div><section className="quote-card phone-chart-panel" aria-label="现货K线">{props.periods}{props.chart}</section><footer className="phone-chart-actions" aria-label="行情操作"><button type="button" onClick={props.onTools}><PhoneIcon name="tools"/><span>工具</span></button><button type="button" onClick={props.onIndicators} disabled={props.disabled}><PhoneIcon name="indicators"/><span>指标</span></button><button type="button" onClick={props.onLayers} disabled={props.disabled}><PhoneIcon name="layers"/><span>画线 / 图层</span></button><Link href="/paper/directional">模拟交易</Link></footer></div>;
}
export function MobileAnalysisOverview(props:HeaderProps&{quote:ReactNode;report:ReactNode;indicators:ReactNode;details:ReactNode;onIndicators:()=>void}){
 return <div className="phone-analysis-overview"><ProductHeader {...props} analysis/><div className="phone-analysis-caption"><strong>{props.symbol} · {props.timeframe.endsWith('m')?props.timeframe:props.timeframe.toUpperCase()}</strong><span>闭合行情 · 规则分析</span></div>{props.report}<section className="phone-analysis-links"><Link href={`/?symbol=${props.symbol}&timeframe=${props.timeframe}`}>查看 K 线</Link><button type="button" onClick={props.onIndicators} disabled={props.disabled}>设置指标</button><Link href="/paper/directional">模拟交易</Link></section>{props.indicators}{props.details}</div>;
}
export function MobileMarketList({children,onProducts}:{children:ReactNode;onProducts:()=>void}){
 return <div className="phone-market-list"><header><h1>行情</h1><button type="button" onClick={onProducts}>搜索品种</button><Link href="/system">设置</Link></header>{children}</div>;
}
