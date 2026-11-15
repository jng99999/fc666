'use client';
import {useEffect,useState} from 'react';
import type {PriceContext} from '../lib/price-action';
import {derivativeSymbol,supportsDerivatives,validateDerivativesSnapshot,derivativesStale,type DerivativesSnapshot} from '../lib/chart-derivatives';
import styles from './chart-derivatives-context.module.css';

const utc=(value:number)=>new Date(value).toISOString().replace('T',' ').slice(0,19)+' UTC';
export default function ChartDerivativesContext({context}:{context:PriceContext}){return <DerivativesView key={context.instrumentId+context.source} context={context}/>;}
function DerivativesView({context}:{context:PriceContext}){
 const [snapshot,setSnapshot]=useState<DerivativesSnapshot|null>(null),[busy,setBusy]=useState(false),[error,setError]=useState(''),[clock,setClock]=useState(0);
 const supported=supportsDerivatives(context),symbol=derivativeSymbol(context.instrumentId);
 useEffect(()=>{if(!snapshot)return;const timer=setInterval(()=>setClock(Date.now()),15000);return()=>clearInterval(timer);},[snapshot]);
 async function load(){if(!supported||!symbol)return;setBusy(true);setError('');try{const response=await fetch('/api/market/derivatives?instrument='+encodeURIComponent(context.instrumentId),{cache:'no-store',credentials:'omit',signal:AbortSignal.timeout(10000)}),value:unknown=await response.json();if(!response.ok)throw new Error(typeof value==='object'&&value!==null&&'error'in value&&typeof value.error==='string'?value.error:'合约背景暂不可用。');const now=Date.now();setSnapshot(validateDerivativesSnapshot(value,symbol,now));setClock(now);}catch(error){setError(error instanceof Error?error.message:'合约背景读取失败，请重试。');}finally{setBusy(false);}}
 const stale=snapshot?derivativesStale(snapshot,clock):false;
 return <section className={styles.root} aria-label="公开合约背景"><header><h4>合约背景</h4><span>只读 · 不参与交易决策</span></header><p>这是对应 USDT-FUTURES 合约的背景，不是现货成交量，不参与历史回测。OI 统计未平仓头寸总量，不能据此判断净多空方向。</p>
  {!supported?<p role="status">{context.instrumentId==='XAU/USD'?'黄金不适用 Bitget 加密 USDT 合约背景。':'当前产品或行情来源不支持此背景；仅支持 Bitget 现货白名单产品。'}</p>:<><button type="button" disabled={busy} onClick={()=>void load()}>{busy?'读取中…':snapshot?'刷新公开背景':'加载公开背景'}</button><small>{symbol} · USDT-FUTURES · api.bitget.com · 公共 v3 API</small></>}
  {error?<p className={styles.error} role="alert">{error} {snapshot?'下方保留上次确认快照，并非刷新成功。':''}</p>:null}
  {snapshot?<><p className={stale?styles.error:styles.status} role="status">{stale?'快照已过期，不作为当前行情，请刷新。':'读取时有效；快照不自动更新，120 秒后标记过期。'}</p><dl><div><dt>未平仓量 OI · {snapshot.baseAsset}</dt><dd>{Number(snapshot.openInterest).toLocaleString('zh-CN',{maximumFractionDigits:8})}</dd></div><div><dt>当前周期资金费率</dt><dd>{(Number(snapshot.fundingRate)*100).toLocaleString('zh-CN',{maximumFractionDigits:6})}% / {snapshot.fundingIntervalHours} 小时</dd></div></dl><small>OI 数据生成 {utc(snapshot.oiAsOf)}<br/>资金费率响应 {utc(snapshot.fundingAsOf)}<br/>下次更新 / 结算 {utc(snapshot.nextUpdate)}<br/>服务器读取 {utc(snapshot.fetchedAt)}</small><p>费率及结算安排以交易所最终规则为准；正负费率不等于单独买卖信号，也不是模拟账户已收取的费用。</p></>:null}
  <a href="https://www.bitget.com/docs/catalog/market/derivatives" target="_blank" rel="noreferrer">Bitget 官方公开接口说明 ↗</a>
 </section>;
}
