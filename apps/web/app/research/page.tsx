"use client";
import {Symbols,MarketProvider} from '../../lib/market';
import {StrategyCatalog,StrategyIds,StrategyVariant,ParameterLabels,type StrategyId} from '../../lib/research-strategies';
import VerifyResult from './VerifyResult';
import {useState,useEffect,type FormEvent} from 'react';
import {z} from 'zod';
import BatchResearch from './BatchResearch';
import HoldoutResearch from './HoldoutResearch';
import WalkForwardResearch from './WalkForwardResearch';
import DrawdownReport from './DrawdownReport';
const decimal=z.string().refine(v=>Number.isFinite(Number(v)));
const Fill=z.object({side:z.enum(['BUY','SELL']),decision_at:z.string(),execution_at:z.string(),quantity:decimal,price:decimal,fee:decimal,simulated:z.literal(true)}).passthrough();
const Analysis=z.object({annualized_return:z.number().finite().nullable(),sharpe:z.number().finite().nullable(),sortino:z.number().finite().nullable(),calmar:z.number().finite().nullable(),closed_trade_count:z.number().int(),win_rate:z.number().finite().nullable(),profit_factor:decimal.nullable(),expectancy:decimal.nullable(),exposure_fraction:z.number().finite(),warnings:z.array(z.string()),unavailable_reasons:z.record(z.string(),z.string()),closed_trades:z.array(z.object({entry_at:z.string(),exit_at:z.string(),net_pnl:decimal,fees:decimal,holding_seconds:z.number(),exit_count:z.number()}).passthrough()),drawdown:z.array(z.object({as_of:z.string(),drawdown:decimal}))}).passthrough();
const Result=z.object({analysis:Analysis,run_id:z.string(),trading_enabled:z.literal(false),manifest:z.object({instrument:z.object({native_symbol:z.string()}).passthrough(),engine_version:z.string(),data_sha256:z.string(),bars:z.number(),start:z.string(),end:z.string(),assumptions:z.array(z.string())}).passthrough(),metrics:z.object({final_equity:decimal,net_pnl:decimal,realized_pnl:decimal,unrealized_pnl:decimal,fees:decimal,total_return:decimal,max_drawdown:decimal,fill_count:z.number(),open_quantity:decimal}).passthrough(),fills:z.array(Fill),equity:z.array(z.object({as_of:z.string(),equity:decimal}).passthrough()),orders:z.array(z.object({status:z.string()}).passthrough())}).passthrough();
type Run=z.infer<typeof Result>;
const JobSchema=z.object({job_id:z.uuid(),status:z.enum(['QUEUED','RUNNING','SUCCEEDED','FAILED','CANCELLED']),progress:z.number().int().min(0).max(100),cancel_requested:z.boolean(),attempts:z.number().int(),error:z.string().nullable(),request:z.object({symbol:z.string(),timeframe:z.string(),as_of:z.string()}).passthrough()});
type Job=z.infer<typeof JobSchema>;
const ACTIVE_JOB='fc666.research.activeJob.v1';
function Equity({rows}:{rows:Run['equity']}){
 const values=rows.map(row=>Number(row.equity)),min=Math.min(...values),max=Math.max(...values),span=max-min||1;
 const points=values.map((value,i)=>`${20+i*960/Math.max(1,values.length-1)},${170-(value-min)/span*140}`).join(' ');
 return <figure><figcaption>收盘标记权益曲线 · quote currency · 未强制平仓</figcaption><svg viewBox="0 0 1000 200" role="img" aria-label="回测权益曲线"><polyline points={points} fill="none" stroke="#77b7ff" strokeWidth="2"/><text x="20" y="195" fill="#a9b8cc">{min.toFixed(2)} — {max.toFixed(2)}</text></svg></figure>;
}
export default function Research(){
 const [strategy,setStrategy]=useState<StrategyId>('ema_long_flat_v1'),[parameters,setParameters]=useState<Record<string,string>>({period:'20'});
 const strategyParameters=Object.fromEntries(Object.entries(parameters).map(([key,value])=>[key,value===''?null:Number(value)]));
 const validStrategy=StrategyVariant.safeParse({strategy,parameters:strategyParameters}).success;
 function selectStrategy(value:StrategyId){setStrategy(value);setParameters(Object.fromEntries(Object.entries(StrategyCatalog[value].defaults).map(([k,v])=>[k,String(v)])));if(value==='ema_long_flat_v1')setPeriod('20');}
 const [symbol,setSymbol]=useState('BTCUSDT'),[timeframe,setTimeframe]=useState('1h'),[limit,setLimit]=useState('120'),[period,setPeriod]=useState('20');
 const [cash,setCash]=useState('10000'),[fee,setFee]=useState('0.001'),[slippage,setSlippage]=useState('0.0005'),[cutoff,setCutoff]=useState('');
 const [job,setJob]=useState<Job|null>(null),[activeId,setActiveId]=useState<string|null>(null),[history,setHistory]=useState<Job[]>([]);
 const [run,setRun]=useState<Run|null>(null),[busy,setBusy]=useState(false),[error,setError]=useState('');
 async function loadHistory(){try{const response=await fetch('/api/research/jobs',{cache:'no-store'});if(response.ok)setHistory(z.array(JobSchema).parse(await response.json()).filter(item=>!['holdout','walk_forward'].includes(String(item.request.research_type))));}catch{/* History fetch does not manufacture successful tasks. */}}
 useEffect(()=>{const requested=new URLSearchParams(location.search).get('symbol');if(Symbols.some(value=>value===requested))setSymbol(requested!);const saved=localStorage.getItem(ACTIVE_JOB);if(saved&&z.uuid().safeParse(saved).success){setActiveId(saved);setBusy(true);}void loadHistory();},[]);
 useEffect(()=>{
  if(!activeId)return;
  const abort=new AbortController();let timer:ReturnType<typeof setTimeout>|undefined;
  async function poll(){
   try{const response=await fetch(`/api/research/jobs/${activeId}`,{cache:'no-store',signal:abort.signal});if(response.status===404){setError('任务不存在');setBusy(false);setActiveId(null);localStorage.removeItem(ACTIVE_JOB);return;}if(!response.ok)throw new Error('任务状态不可用');const current=JobSchema.parse(await response.json());setJob(current);setError('');
    if(current.status==='SUCCEEDED'){const result=await fetch(`/api/research/jobs/${activeId}/result`,{cache:'no-store',signal:abort.signal});if(!result.ok)throw new Error('任务结果不可用');setRun(Result.parse(await result.json()));setBusy(false);void loadHistory();}
    else if(current.status==='FAILED'||current.status==='CANCELLED'){setRun(null);setBusy(false);if(current.status==='FAILED')setError(current.error??'任务失败');void loadHistory();}
    else timer=setTimeout(poll,500);
   }catch(e){if(!abort.signal.aborted){setError(e instanceof Error?e.message:'任务连接失败');timer=setTimeout(poll,2000);}}
  }
  void poll();return()=>{abort.abort();if(timer)clearTimeout(timer);};
 },[activeId]);
 async function submit(event:FormEvent){event.preventDefault();if(!validStrategy)return;setBusy(true);setError('');setRun(null);setJob(null);setActiveId(null);localStorage.removeItem(ACTIVE_JOB);
  try{const response=await fetch('/api/research/jobs',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({strategy,parameters:strategyParameters,symbol,timeframe,limit:Number(limit),as_of:cutoff||null,config:{initial_cash:cash,fee_rate:fee,slippage_rate:slippage,period:Number(period),allocation:'0.5',participation:'0.01'}}),signal:AbortSignal.timeout(15000)});const data=await response.json();if(!response.ok)throw new Error(typeof data.detail==='string'?data.detail:'参数或历史数据不可用');const created=JobSchema.parse(data);localStorage.setItem(ACTIVE_JOB,created.job_id);setJob(created);setActiveId(created.job_id);void loadHistory();}
  catch(e){setError(e instanceof Error?e.message:'提交失败');setBusy(false);}
 }
 async function cancel(){if(!job)return;try{const response=await fetch(`/api/research/jobs/${job.job_id}/cancel`,{method:'POST'});if(!response.ok)throw new Error('任务已结束或无法取消');setJob(JobSchema.parse(await response.json()));}catch(e){setError(e instanceof Error?e.message:'取消失败');}}
 function restore(item:Job){setRun(null);setError('');setJob(item);setBusy(!['SUCCEEDED','FAILED','CANCELLED'].includes(item.status));localStorage.setItem(ACTIVE_JOB,item.job_id);setActiveId(null);setTimeout(()=>setActiveId(item.job_id),0);}
 return <main className="research-page research-workbench"><div className="page-heading"><div><h1>策略研究</h1><p>{StrategyIds.length} 种现货做多 / 空仓策略，基于 {MarketProvider==='bitget'?'Bitget':'Binance'} 真实历史数据比较；保存输入、成本和结果，支持独立复验。实盘关闭。</p></div><a className="secondary" href={`/analysis?symbol=${symbol}`}>查看行情分析 →</a></div>
 <p><a href="/research/matrix">多产品 · 多周期批量研究 →</a> · 统一日期窗口，独立资金，支持长历史对比与 CSV 导出。</p>
 <form onSubmit={submit} className="research-form research-configuration"><div className="research-section-heading"><h2>单次历史回测</h2><p>先设置市场与策略，再配置资金和成本。批次研究沿用这里的市场设置。</p></div>
 <fieldset disabled={busy}><legend>市场与策略</legend><div className="research-fields">
 <label>策略<select aria-label="策略" value={strategy} onChange={e=>selectStrategy(e.target.value as StrategyId)}>{StrategyIds.map(id=><option key={id} value={id}>{StrategyCatalog[id].name}</option>)}</select></label>
 <label>交易对<select value={symbol} onChange={e=>setSymbol(e.target.value)}>{Symbols.map(s=><option key={s}>{s}</option>)}</select></label>
 <label>周期<select value={timeframe} onChange={e=>setTimeframe(e.target.value)}>{['1m','5m','15m','1h','4h','1d'].map(v=><option key={v}>{v}</option>)}</select></label>
 <label>历史柱数<input required type="number" min="2" max="5000" value={limit} onChange={e=>setLimit(e.target.value)}/></label>
 {Object.entries(parameters).map(([key,value])=><label key={key}>{ParameterLabels[key]}<input aria-label={`策略参数 ${ParameterLabels[key]}`} required type="number" step="1" min={key==='lower'||key==='upper'||key==='deviations'?1:2} max={key==='lower'||key==='upper'?99:key==='deviations'?5:500} value={value} onChange={e=>{setParameters(p=>({...p,[key]:e.target.value}));if(strategy==='ema_long_flat_v1'&&key==='period')setPeriod(e.target.value);}}/></label>)}
 {strategy!=='ema_long_flat_v1'?<label>市场分类周期<input required type="number" min="2" max="500" value={period} onChange={e=>setPeriod(e.target.value)}/></label>:null}
 </div><p>{StrategyCatalog[strategy].description}</p>{!validStrategy?<p role="alert">参数不合法：周期需为整数，SMA / MACD 快周期小于慢周期，RSI 下阈值小于上阈值，退出窗口不大于突破窗口。</p>:null}<small>新增策略仅用于历史研究。参考 <a href="https://github.com/QuantConnect/Lean/tree/80e7843f645673bcbeaab963049f76f20f6785e1" target="_blank" rel="noreferrer">QuantConnect LEAN（Apache-2.0）</a> 的策略示例与指标，以及 <a href="https://github.com/bukosabino/ta/tree/a890410710a6e483c9ba08da7f3dd5089e4b9dff" target="_blank" rel="noreferrer">ta（MIT）</a> 的 ROC 指标，按本平台现货规则独立实现。</small></fieldset><fieldset disabled={busy}><legend>资金与成本</legend><div className="research-fields"><label>初始资金（USDT）<input required inputMode="decimal" value={cash} onChange={e=>setCash(e.target.value)}/></label>
 <label>费率<input required inputMode="decimal" value={fee} onChange={e=>setFee(e.target.value)}/></label>
 <label>滑点率<input required inputMode="decimal" value={slippage} onChange={e=>setSlippage(e.target.value)}/></label>
 <label>截止时间（含时区，可空）<input placeholder="2026-10-05T12:00:00Z" value={cutoff} onChange={e=>setCutoff(e.target.value)}/></label>
 </div></fieldset><div className="research-submit"><button disabled={busy||!validStrategy} type="submit">{busy?'计算中…':'运行历史回测'}</button><small>闭合柱决策，下一柱开盘模拟成交；仅现货做多 / 空仓。</small></div></form><p>预算上限为可用资金的50%，单次成交容量用上一闭合柱成交量的1%作为研究代理；不是开盘时真实流动性保证。</p>
 {job?<section data-testid="research-task" data-status={job.status}><h2>后台任务 · {{QUEUED:"排队中",RUNNING:"计算中",SUCCEEDED:"已完成",FAILED:"失败",CANCELLED:"已取消"}[job.status]}{job.cancel_requested?' · 已请求取消':''}</h2><progress aria-label="回测任务进度" max={100} value={job.progress}/><span> {job.progress}% · 尝试 {job.attempts}/3</span><p>{job.status==='QUEUED'?'排队等待研究进程，刷新页面可恢复任务。 ':''}{job.request.symbol} · {job.request.timeframe} · 固定截止 {job.request.as_of}</p>{['QUEUED','RUNNING'].includes(job.status)?<button disabled={job.cancel_requested} onClick={()=>void cancel()}>取消任务</button>:null}</section>:null}
 <details><summary>最近研究任务（最多20条）</summary><button onClick={()=>void loadHistory()}>刷新任务记录</button><ul>{history.map(item=><li key={item.job_id}><button onClick={()=>restore(item)}>{item.request.symbol} · {item.request.timeframe} · {item.status} · {item.job_id.slice(0,8)}</button></li>)}</ul></details>
 {error?<p role="alert">{error}</p>:null}
 {run?<section data-testid="backtest-result"><h2>已完成 · {run.manifest.instrument.native_symbol} · {run.manifest.bars} 根真实历史柱</h2><div className="research-metrics">{([['期末权益 (USDT)',run.metrics.final_equity],['净收益 (USDT)',run.metrics.net_pnl],['已实现收益 (USDT)',run.metrics.realized_pnl],['未实现收益 (USDT)',run.metrics.unrealized_pnl],['费用 (USDT)',run.metrics.fees],['累计收益率',`${(Number(run.metrics.total_return)*100).toFixed(4)}%`],['最大回撤',`${(Number(run.metrics.max_drawdown)*100).toFixed(4)}%`],['模拟成交次数',run.metrics.fill_count],['期末持仓数量',run.metrics.open_quantity]] as const).map(([key,value])=><div key={key}><small>{key}</small><strong>{typeof value==='number'?value:value.endsWith('%')?value:Number(value).toLocaleString('en-US',{maximumFractionDigits:8})}</strong></div>)}</div><Equity rows={run.equity}/>
 <section data-testid="risk-analysis"><h3>风险收益分析</h3><p>按365天连续市场年化，无风险利率0。收益波动采用样本标准差；短窗口年化仅作描述，不能当作收益预测。</p><dl className="analysis-grid">{([
 ['年化收益率',run.analysis.annualized_return===null?null:`${(run.analysis.annualized_return*100).toPrecision(6)}%`,'annualized_return'],
 ['Sharpe',run.analysis.sharpe,'sharpe'],['Sortino',run.analysis.sortino,'sortino'],['Calmar',run.analysis.calmar,'calmar'],
 ['完整开平仓次数',run.analysis.closed_trade_count,'trade_statistics'],['完整交易胜率',run.analysis.win_rate===null?null:`${(run.analysis.win_rate*100).toFixed(2)}%`,'trade_statistics'],
 ['Profit Factor',run.analysis.profit_factor,'profit_factor'],['每笔完整交易期望 (USDT)',run.analysis.expectancy,'trade_statistics'],['持仓时间比例',`${(run.analysis.exposure_fraction*100).toFixed(2)}%`,'']
 ] as const).map(([label,value,key])=><div key={label}><dt>{label}</dt><dd>{value===null?'不可用':typeof value==='number'?value.toLocaleString('en-US',{maximumFractionDigits:6}):value}</dd>{value===null?<small>{run.analysis.unavailable_reasons[key]??'无足够样本'}</small>:null}</div>)}</dl><figure><figcaption>权益回撤曲线</figcaption><svg viewBox="0 0 1000 140" role="img" aria-label="回测回撤曲线"><polyline fill="none" stroke="#ff7785" strokeWidth="2" points={run.analysis.drawdown.map((row,i)=>`${20+i*960/Math.max(1,run.analysis.drawdown.length-1)},${20+Number(row.drawdown)*100}`).join(' ')}/></svg></figure></section>
 <DrawdownReport rows={run.analysis.drawdown} totalReturn={run.metrics.total_return} runId={run.run_id}/>
 <h3>完整交易（未平仓及部分退出不计入）</h3><div className="research-table"><table><thead><tr><th>开仓 UTC</th><th>平仓 UTC</th><th>净收益</th><th>费用</th><th>持有秒数</th><th>退出成交次数</th></tr></thead><tbody>{run.analysis.closed_trades.map((trade,i)=><tr key={i}><td>{trade.entry_at}</td><td>{trade.exit_at}</td><td>{trade.net_pnl}</td><td>{trade.fees}</td><td>{trade.holding_seconds}</td><td>{trade.exit_count}</td></tr>)}</tbody></table></div>
 <p>{run.orders.filter(o=>o.status==='REJECTED').length} 次拒绝 · {run.orders.filter(o=>o.status==='PARTIAL_CANCELLED').length} 次部分成交后取消。结束仓位按最后收盘价标记，未强制清仓。</p>
 <p>模拟滑点与tick取整总成本：{String(run.metrics.slippage_cost??"不可用")} USDT；成本已反映在成交价和收益中，不重复扣款。</p><h3>模拟成交记录</h3><div className="research-table"><table><thead><tr><th>方向</th><th>决策 UTC</th><th>成交 UTC</th><th>数量</th><th>价格</th><th>费用</th></tr></thead><tbody>{run.fills.map((fill,i)=><tr key={i}><td>{fill.side}</td><td>{fill.decision_at}</td><td>{fill.execution_at}</td><td>{fill.quantity}</td><td>{fill.price}</td><td>{fill.fee}</td></tr>)}</tbody></table></div>{run.fills.length===0?<p>该参数与时间窗口没有成交。</p>:null}
 <details><summary>数据与运行清单</summary><p>run_id: {run.run_id}</p><p>策略：{String(run.manifest.strategy)} · 参数：{JSON.stringify(run.manifest.parameters??{})} · 引擎：{run.manifest.engine_version}</p><p>data_sha256: {run.manifest.data_sha256}</p><p>{run.manifest.start} → {run.manifest.end}</p><ul>{run.manifest.assumptions.map(s=><li key={s}>{s}</li>)}</ul></details>{activeId?<VerifyResult key={activeId+run.run_id} jobId={activeId} runId={run.run_id}/>:null}<button onClick={()=>{const blob=new Blob([JSON.stringify(run,null,2)],{type:'application/json'});const url=URL.createObjectURL(blob);const a=document.createElement('a');a.href=url;a.download=`fc666-${run.run_id}.json`;a.click();URL.revokeObjectURL(url);}}>下载结果与清单</button></section>:null}
 <details className="research-advanced"><summary>参数批次 · 同一行情比较策略</summary><BatchResearch base={{strategy,parameters:strategyParameters,symbol,timeframe,limit:Number(limit),as_of:cutoff||null,config:{initial_cash:cash,fee_rate:fee,slippage_rate:slippage,period:Number(period),allocation:'0.5',participation:'0.01'}}}/></details>
 <details className="research-advanced"><summary>留出验证 · 检查固定参数</summary><HoldoutResearch base={{strategy,parameters:strategyParameters,symbol,timeframe,limit:Number(limit),as_of:cutoff||null,config:{initial_cash:cash,fee_rate:fee,slippage_rate:slippage,period:Number(period),allocation:'0.5',participation:'0.01'}}}/></details>
 <details className="research-advanced"><summary>滚动研究 · 训练选参 / 样本外测试</summary><WalkForwardResearch base={{strategy,parameters:strategyParameters,symbol,timeframe,limit:Number(limit),as_of:cutoff||null,config:{initial_cash:cash,fee_rate:fee,slippage_rate:slippage,period:Number(period),allocation:'0.5',participation:'0.01'}}}/></details>
 </main>;
}
