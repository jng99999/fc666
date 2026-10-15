'use client';
import {useState} from 'react';
import type {View} from './page';

function quantity(value:string){return Number(value).toLocaleString('zh-CN',{maximumFractionDigits:8});}
function price(value:string){return Number(value).toLocaleString('zh-CN',{minimumFractionDigits:2,maximumFractionDigits:6});}
export default function PaperLedger({view,kind,labels}:{view:View;kind:'fills'|'orders';labels:Record<string,string>}){
 const [limit,setLimit]=useState(20);
 const all=kind==='fills'?view.fills:view.orders;
 const total=Math.min(100,all.length);
 return <section className="paper-ledger" aria-label={kind==='fills'?'模拟成交记录':'模拟订单记录'}>
  <div className="paper-section-title"><h2>{kind==='fills'?'模拟成交':'模拟订单'}</h2><span>最近 {Math.min(limit,total)} / {total} 条</span></div>
  {all.length===0?<div className="paper-record-empty"><strong>{kind==='fills'?'还没有模拟成交':'还没有模拟订单'}</strong><p>创建账户只预热指标；出现策略信号后，等待后续及时闭合柱。</p></div>:<ol className="paper-ledger-list">{kind==='fills'?view.fills.slice(-limit).reverse().map(fill=><li key={fill.order_id}>
   <div className="paper-ledger-title"><strong className={fill.side==='BUY'?'buy':'sell'}>{fill.side==='BUY'?'买入':'卖出'} <span>模拟成交</span></strong><time dateTime={fill.execution_at}>{fill.execution_at.slice(5,19).replace('T',' ')} UTC</time></div>
   <dl><div><dt>数量 {view.manifest.instrument.base as string}</dt><dd title={fill.quantity}>{quantity(fill.quantity)}</dd></div><div><dt>参考价格 USDT</dt><dd title={fill.price}>{price(fill.price)}</dd></div><div><dt>手续费 USDT</dt><dd title={fill.fee}>{price(fill.fee)}</dd></div></dl>
  </li>):view.orders.slice(-limit).reverse().map(order=><li key={order.order_id}>
   <div className="paper-ledger-title"><strong className={order.side==='BUY'?'buy':'sell'}>{order.side==='BUY'?'买入':'卖出'} <span>{order.status==='REJECTED'?'已拒绝':order.status==='PARTIAL_CANCELLED'?'部分成交，余量取消':'已成交'}</span></strong><time dateTime={order.execution_at}>{order.execution_at.slice(5,19).replace('T',' ')} UTC</time></div>
   <p>{order.reason?(labels[order.reason]??order.reason):`模拟数量 ${order.quantity===undefined?'未提供':quantity(order.quantity)}`}</p>
  </li>)}</ol>}
  {limit<total?<button className="secondary paper-more" onClick={()=>setLimit(value=>Math.min(100,value+20))}>查看更多{kind==='fills'?'成交':'订单'}</button>:null}
  <p className="paper-footnote">闭合柱参考价模拟成交。完整观察时间、编号和原始精度保存在下载账本中。</p>
 </section>;
}
