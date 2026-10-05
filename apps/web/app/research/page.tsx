"use client";
import {useState,type FormEvent} from 'react';
import {z} from 'zod';
const decimal=z.string().refine(v=>Number.isFinite(Number(v)));
const Fill=z.object({side:z.enum(['BUY','SELL']),decision_at:z.string(),execution_at:z.string(),quantity:decimal,price:decimal,fee:decimal,simulated:z.literal(true)}).passthrough();
const Analysis=z.object({annualized_return:z.number().finite().nullable(),sharpe:z.number().finite().nullable(),sortino:z.number().finite().nullable(),calmar:z.number().finite().nullable(),closed_trade_count:z.number().int(),win_rate:z.number().finite().nullable(),profit_factor:decimal.nullable(),expectancy:decimal.nullable(),exposure_fraction:z.number().finite(),warnings:z.array(z.string()),unavailable_reasons:z.record(z.string(),z.string()),closed_trades:z.array(z.object({entry_at:z.string(),exit_at:z.string(),net_pnl:decimal,fees:decimal,holding_seconds:z.number(),exit_count:z.number()}).passthrough()),drawdown:z.array(z.object({as_of:z.string(),drawdown:decimal}))}).passthrough();
const Result=z.object({analysis:Analysis,run_id:z.string(),trading_enabled:z.literal(false),manifest:z.object({instrument:z.object({native_symbol:z.string()}).passthrough(),engine_version:z.string(),data_sha256:z.string(),bars:z.number(),start:z.string(),end:z.string(),assumptions:z.array(z.string())}).passthrough(),metrics:z.object({final_equity:decimal,net_pnl:decimal,realized_pnl:decimal,unrealized_pnl:decimal,fees:decimal,total_return:decimal,max_drawdown:decimal,fill_count:z.number(),open_quantity:decimal}).passthrough(),fills:z.array(Fill),equity:z.array(z.object({as_of:z.string(),equity:decimal}).passthrough()),orders:z.array(z.object({status:z.string()}).passthrough())}).passthrough();
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
 <section data-testid="risk-analysis"><h3>风险收益分析</h3><p>按365天连续市场年化，无风险利率0。收益波动采用样本标准差；短窗口年化仅作描述，不能当作收益预测。</p><dl className="analysis-grid">{([
 ['年化收益率',run.analysis.annualized_return===null?null:`${(run.analysis.annualized_return*100).toPrecision(6)}%`,'annualized_return'],
 ['Sharpe',run.analysis.sharpe,'sharpe'],['Sortino',run.analysis.sortino,'sortino'],['Calmar',run.analysis.calmar,'calmar'],
 ['完整开平仓次数',run.analysis.closed_trade_count,'trade_statistics'],['完整交易胜率',run.analysis.win_rate===null?null:`${(run.analysis.win_rate*100).toFixed(2)}%`,'trade_statistics'],
 ['Profit Factor',run.analysis.profit_factor,'profit_factor'],['每笔完整交易期望 (USDT)',run.analysis.expectancy,'trade_statistics'],['持仓时间比例',`${(run.analysis.exposure_fraction*100).toFixed(2)}%`,'']
 ] as const).map(([label,value,key])=><div key={label}><dt>{label}</dt><dd>{value===null?'不可用':typeof value==='number'?value.toLocaleString('en-US',{maximumFractionDigits:6}):value}</dd>{value===null?<small>{run.analysis.unavailable_reasons[key]??'无足够样本'}</small>:null}</div>)}</dl><figure><figcaption>权益回撤曲线</figcaption><svg viewBox="0 0 1000 140" role="img" aria-label="回测回撤曲线"><polyline fill="none" stroke="#ff7785" strokeWidth="2" points={run.analysis.drawdown.map((row,i)=>`${20+i*960/Math.max(1,run.analysis.drawdown.length-1)},${20+Number(row.drawdown)*100}`).join(' ')}/></svg></figure></section>
 <h3>完整交易（未平仓及部分退出不计入）</h3><div className="research-table"><table><thead><tr><th>开仓 UTC</th><th>平仓 UTC</th><th>净收益</th><th>费用</th><th>持有秒数</th><th>退出成交次数</th></tr></thead><tbody>{run.analysis.closed_trades.map((trade,i)=><tr key={i}><td>{trade.entry_at}</td><td>{trade.exit_at}</td><td>{trade.net_pnl}</td><td>{trade.fees}</td><td>{trade.holding_seconds}</td><td>{trade.exit_count}</td></tr>)}</tbody></table></div>
 <p>{run.orders.filter(o=>o.status==='REJECTED').length} 次拒绝 · {run.orders.filter(o=>o.status==='PARTIAL_CANCELLED').length} 次部分成交后取消。结束仓位按最后收盘价标记，未强制清仓。</p>
 <p>模拟滑点与tick取整总成本：{String(run.metrics.slippage_cost??"不可用")} USDT；成本已反映在成交价和收益中，不重复扣款。</p><h3>模拟成交记录</h3><div className="research-table"><table><thead><tr><th>方向</th><th>决策 UTC</th><th>成交 UTC</th><th>数量</th><th>价格</th><th>费用</th></tr></thead><tbody>{run.fills.map((fill,i)=><tr key={i}><td>{fill.side}</td><td>{fill.decision_at}</td><td>{fill.execution_at}</td><td>{fill.quantity}</td><td>{fill.price}</td><td>{fill.fee}</td></tr>)}</tbody></table></div>{run.fills.length===0?<p>该参数与时间窗口没有成交。</p>:null}
 <details><summary>数据与运行清单</summary><p>run_id: {run.run_id}</p><p>data_sha256: {run.manifest.data_sha256}</p><p>{run.manifest.start} → {run.manifest.end}</p><ul>{run.manifest.assumptions.map(s=><li key={s}>{s}</li>)}</ul></details><button onClick={()=>{const blob=new Blob([JSON.stringify(run,null,2)],{type:'application/json'});const url=URL.createObjectURL(blob);const a=document.createElement('a');a.href=url;a.download=`fc666-${run.run_id}.json`;a.click();URL.revokeObjectURL(url);}}>下载结果与清单</button></section>:null}
 </main>;
}
