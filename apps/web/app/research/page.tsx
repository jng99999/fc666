"use client";
import {useState,type FormEvent} from 'react';
import {z} from 'zod';
const decimal=z.string().refine(v=>Number.isFinite(Number(v)));
const Fill=z.object({side:z.enum(['BUY','SELL']),decision_at:z.string(),execution_at:z.string(),quantity:decimal,price:decimal,fee:decimal,simulated:z.literal(true)}).passthrough();
const Result=z.object({run_id:z.string(),trading_enabled:z.literal(false),manifest:z.object({instrument:z.object({native_symbol:z.string()}).passthrough(),engine_version:z.string(),data_sha256:z.string(),bars:z.number(),start:z.string(),end:z.string(),assumptions:z.array(z.string())}).passthrough(),metrics:z.object({final_equity:decimal,net_pnl:decimal,realized_pnl:decimal,unrealized_pnl:decimal,fees:decimal,total_return:decimal,max_drawdown:decimal,fill_count:z.number(),open_quantity:decimal}).passthrough(),fills:z.array(Fill),equity:z.array(z.object({as_of:z.string(),equity:decimal}).passthrough()),orders:z.array(z.object({status:z.string()}).passthrough())}).passthrough();
type Run=z.infer<typeof Result>;
function Equity({rows}:{rows:Run['equity']}){
 const values=rows.map(row=>Number(row.equity)),min=Math.min(...values),max=Math.max(...values),span=max-min||1;
 const points=values.map((value,i)=>`${20+i*960/Math.max(1,values.length-1)},${170-(value-min)/span*140}`).join(' ');
 return <figure><figcaption>收盘标记权益曲线 · quote currency · 未强制平仓</figcaption><svg viewBox="0 0 1000 200" role="img" aria-label="回测权益曲线"><polyline points={points} fill="none" stroke="#77b7ff" strokeWidth="2"/><text x="20" y="195" fill="#a9b8cc">{min.toFixed(2)} — {max.toFixed(2)}</text></svg></figure>;
}
export default function Research(){
 const [symbol,setSymbol]=useState('BTCUSDT'),[timeframe,setTimeframe]=useState('1h'),[limit,setLimit]=useState('120'),[period,setPeriod]=useState('20');
 const [cash,setCash]=useState('10000'),[fee,setFee]=useState('0.001'),[slippage,setSlippage]=useState('0.0005'),[cutoff,setCutoff]=useState('');
 const [run,setRun]=useState<Run|null>(null),[busy,setBusy]=useState(false),[error,setError]=useState('');
 async function submit(event:FormEvent){event.preventDefault();setBusy(true);setError('');setRun(null);
  try{const response=await fetch('/api/market/backtest',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({symbol,timeframe,limit:Number(limit),as_of:cutoff||null,config:{initial_cash:cash,fee_rate:fee,slippage_rate:slippage,period:Number(period),allocation:'0.5',participation:'0.01'}}),signal:AbortSignal.timeout(15000)});const data=await response.json();if(!response.ok)throw new Error(typeof data.detail==='string'?data.detail:'参数或历史数据不可用');setRun(Result.parse(data));}
  catch(e){setError(e instanceof Error?e.message:'回测失败');}finally{setBusy(false);}
 }
 return <main className="research-page"><header><strong>FC666 · Research</strong><a href="/">行情终端</a><span className="badge">只读研究 · 实盘关闭</span></header><h1>现货历史回测</h1><p>EMA Long / Flat 基线策略；闭合柱决策，下一柱开盘模拟成交。仅支持现货做多/空仓，不含杠杆、卖空和真实下单。</p>
 <form onSubmit={submit} className="research-form">
 <label>交易对<select value={symbol} onChange={e=>setSymbol(e.target.value)}><option>BTCUSDT</option><option>ETHUSDT</option></select></label>
 <label>周期<select value={timeframe} onChange={e=>setTimeframe(e.target.value)}>{['1m','5m','15m','1h','4h','1d'].map(v=><option key={v}>{v}</option>)}</select></label>
 <label>历史柱数<input required type="number" min="2" max="1000" value={limit} onChange={e=>setLimit(e.target.value)}/></label>
 <label>EMA 周期<input required type="number" min="2" max="500" value={period} onChange={e=>setPeriod(e.target.value)}/></label>
 <label>初始资金<input required inputMode="decimal" value={cash} onChange={e=>setCash(e.target.value)}/></label>
 <label>费率<input required inputMode="decimal" value={fee} onChange={e=>setFee(e.target.value)}/></label>
 <label>滑点率<input required inputMode="decimal" value={slippage} onChange={e=>setSlippage(e.target.value)}/></label>
 <label>截止时间（含时区，可空）<input placeholder="2026-10-05T12:00:00Z" value={cutoff} onChange={e=>setCutoff(e.target.value)}/></label>
 <button disabled={busy} type="submit">{busy?'计算中…':'运行历史回测'}</button></form><p>预算上限为可用资金的50%，单次成交容量用上一闭合柱成交量的1%作为研究代理；不是开盘时真实流动性保证。</p>
 {error?<p role="alert">{error}</p>:null}
 {run?<section data-testid="backtest-result"><h2>已完成 · {run.manifest.instrument.native_symbol} · {run.manifest.bars} 根真实历史柱</h2><div className="research-metrics">{([['期末权益 (USDT)',run.metrics.final_equity],['净收益 (USDT)',run.metrics.net_pnl],['已实现收益 (USDT)',run.metrics.realized_pnl],['未实现收益 (USDT)',run.metrics.unrealized_pnl],['费用 (USDT)',run.metrics.fees],['累计收益率',`${(Number(run.metrics.total_return)*100).toFixed(4)}%`],['最大回撤',`${(Number(run.metrics.max_drawdown)*100).toFixed(4)}%`],['模拟成交次数',run.metrics.fill_count],['期末持仓数量',run.metrics.open_quantity]] as const).map(([key,value])=><div key={key}><small>{key}</small><strong>{typeof value==='number'?value:value.endsWith('%')?value:Number(value).toLocaleString('en-US',{maximumFractionDigits:8})}</strong></div>)}</div><Equity rows={run.equity}/>
 <p>{run.orders.filter(o=>o.status==='REJECTED').length} 次拒绝 · {run.orders.filter(o=>o.status==='PARTIAL_CANCELLED').length} 次部分成交后取消。结束仓位按最后收盘价标记，未强制清仓。</p>
 <h3>模拟成交记录</h3><div className="research-table"><table><thead><tr><th>方向</th><th>决策 UTC</th><th>成交 UTC</th><th>数量</th><th>价格</th><th>费用</th></tr></thead><tbody>{run.fills.map((fill,i)=><tr key={i}><td>{fill.side}</td><td>{fill.decision_at}</td><td>{fill.execution_at}</td><td>{fill.quantity}</td><td>{fill.price}</td><td>{fill.fee}</td></tr>)}</tbody></table></div>{run.fills.length===0?<p>该参数与时间窗口没有成交。</p>:null}
 <details><summary>数据与运行清单</summary><p>run_id: {run.run_id}</p><p>data_sha256: {run.manifest.data_sha256}</p><p>{run.manifest.start} → {run.manifest.end}</p><ul>{run.manifest.assumptions.map(s=><li key={s}>{s}</li>)}</ul></details><button onClick={()=>{const blob=new Blob([JSON.stringify(run,null,2)],{type:'application/json'});const url=URL.createObjectURL(blob);const a=document.createElement('a');a.href=url;a.download=`fc666-${run.run_id}.json`;a.click();URL.revokeObjectURL(url);}}>下载结果与清单</button></section>:null}
 </main>;
}
