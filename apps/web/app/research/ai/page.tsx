'use client';
import Link from 'next/link';
import {useEffect,useRef,useState} from 'react';
import type {z} from 'zod';
import WorkspaceSelect from '../../../components/workspace-select';
import {DirectionalAccounts,DirectionalAccount,DirectionalError,directionalRequest,directionalReason,type DirectionalView} from '../../../lib/directional';
import {ResearchJob,ResearchJobs,ResearchStatus,ResearchRoles,researchReason,type ResearchView} from '../../../lib/tradingagents';
import styles from '../execution/page.module.css';
import ui from './page.module.css';

export default function AIResearch(){
 const [ready,setReady]=useState(false),[busy,setBusy]=useState(false),[error,setError]=useState('');
 const [status,setStatus]=useState<z.infer<typeof ResearchStatus>|null>(null),[accounts,setAccounts]=useState<DirectionalView[]>([]),[allowed,setAllowed]=useState<string[]>([]),[account,setAccount]=useState('');
 const [jobs,setJobs]=useState<ResearchView[]>([]),[job,setJob]=useState<ResearchView|null>(null),[retry,setRetry]=useState(false);
 const lock=useRef(false),pending=useRef<{path:string;body:{mode:'PREPARE'|'ANALYZE';request_id:string;expected_revision:number}}|null>(null);
 async function load(signal?:AbortSignal){
  // Establish one owner cookie before reading the research collection.
  const owned=await directionalRequest('accounts',DirectionalAccounts,undefined,signal);
  const [service,history]=await Promise.all([directionalRequest('research/status',ResearchStatus,undefined,signal),directionalRequest('research/jobs',ResearchJobs,undefined,signal)]);
  setAccounts(owned.accounts);setStatus(service);setJobs(history.jobs);setAllowed(history.authorized_accounts);setReady(true);
 }
 useEffect(()=>{const abort=new AbortController();load(abort.signal).catch(()=>{if(!abort.signal.aborted){setError('研究服务暂不可用，请重试；未调用模型或修改账户。');setReady(true);}});return()=>abort.abort();},[]);
 const active=job?.state==='QUEUED'||job?.state==='RUNNING';
 useEffect(()=>{
  if(!active||!job)return;let alive=true;const abort=new AbortController();
  let timer:ReturnType<typeof setTimeout>;const poll=async()=>{try{const next=await directionalRequest('research/jobs/'+job.job_id,ResearchJob,undefined,abort.signal);if(alive){setJob(next);setJobs(rows=>[next,...rows.filter(row=>row.job_id!==next.job_id)]);setError('');}}catch{if(alive)setError('研究状态读取暂不可用，请刷新记录核对；不要重复创建任务。');}finally{if(alive)timer=setTimeout(poll,3000);}};timer=setTimeout(poll,3000);
  return()=>{alive=false;abort.abort();clearTimeout(timer);};
 },[active,job?.job_id]);
 async function create(mode:'PREPARE'|'ANALYZE'){
  if(lock.current||!account||!ready)return;lock.current=true;setBusy(true);setError('');
  try{
   if(!pending.current){const view=await directionalRequest('accounts/'+account,DirectionalAccount);pending.current={path:'research/accounts/'+account,body:{mode,request_id:crypto.randomUUID(),expected_revision:view.revision}};}
   const next=await directionalRequest(pending.current.path,ResearchJob,pending.current.body);setJob(next);setJobs(rows=>[next,...rows.filter(row=>row.job_id!==next.job_id)].slice(0,20));pending.current=null;setRetry(false);
  }catch(value){if(value instanceof DirectionalError&&value.status<500){pending.current=null;setRetry(false);}else setRetry(Boolean(pending.current));setError(value instanceof Error?value.message:'研究请求未确认，请重试同一请求。');}
  finally{lock.current=false;setBusy(false);}
 }
 async function refresh(){if(lock.current)return;lock.current=true;setBusy(true);setError('');try{await load();if(job)setJob(await directionalRequest('research/jobs/'+job.job_id,ResearchJob));}catch{setError('研究记录刷新失败，请稍后重试。');}finally{lock.current=false;setBusy(false);}}
 function download(){if(!job)return;const url=URL.createObjectURL(new Blob([JSON.stringify(job,null,2)],{type:'application/json'}));const link=document.createElement('a');link.href=url;link.download=`fc666-research-${job.job_id}.json`;link.click();URL.revokeObjectURL(url);}
 const result=job?.result&&'advice'in job.result?job.result:null;
 const current=accounts.find(row=>row.account_id===account),gate=job?.snapshot.snapshot.entry_gate;
 return <main className={styles.page} aria-busy={!ready}>
  <header className={styles.heading}><div><p className={styles.eyebrow}>策略研究 / AI 研究</p><h1>TradingAgents 研究</h1><p className={styles.muted}>用真实快照形成多空观点，独立校验每一条建议。</p></div><span className={styles.badge}>仅研究 · 实盘关闭</span></header>
  <nav className={styles.navigation} aria-label="AI研究导航"><Link href="/research">策略回测</Link><Link href="/paper/directional">双向模拟账户</Link><Link href="/research/execution">执行验证</Link><Link href="/system">连接状态</Link></nav>
  <section className={styles.card}><h2>选择研究账户</h2><p>研究不会创建订单或改变账户。模型调用需服务器配置并授权具体账户；新闻门禁与执行保护独立生效。</p>
   <div className={ui.notice} role="status">{!status?(ready?'研究连接尚未确认，请刷新研究记录重试。':'正在检查研究连接…'):status.runner_configured?'研究服务已配置；这不代表模型结果已通过验证。':'模型研究尚未配置。可先保存真实行情快照；不会使用默认模型或产生调用费用。'}</div>
   <div className={ui.controls}><label>独立模拟账户<WorkspaceSelect label="AI研究账户" value={account} disabled={!ready||busy||retry||active} onValueChange={value=>{setAccount(value);setError('');setJob(null);}} options={[{value:'',label:'请选择账户',disabled:true},...accounts.map(row=>({value:row.account_id,label:`${row.config.instrument} · ${row.account_id.slice(-8)}${row.status==='STOPPED'?' · 已停新风险':''}`}))]}/></label>
    <button disabled={!ready||busy||!account||retry||active} onClick={()=>create('PREPARE')}>保存研究快照</button><button disabled={!ready||busy||!account||!status?.runner_configured||!allowed.includes(account)||retry||active} onClick={()=>create('ANALYZE')}>{busy?'正在提交…':'运行 AI 研究'}</button><button disabled={!ready||busy} onClick={refresh}>刷新研究记录</button>
   </div>
   {retry?<button disabled={busy} onClick={()=>create(pending.current?.body.mode??'PREPARE')}>重试同一研究请求</button>:null}
   {ready&&accounts.length===0?<p>此浏览器没有独立双向账户。<Link href="/paper/directional">创建模拟账户 →</Link></p>:null}
   {current&&status?.runner_configured&&!allowed.includes(account)?<p>该账户尚未获服务器的模型研究授权。</p>:null}
   {error?<p role="alert" className={ui.error}>{error}</p>:null}
  </section>
  {job?<section className={styles.card} data-testid="ai-research-job"><h2>{job.snapshot.snapshot.config.instrument} · {researchReason(job.state)}</h2><div className={ui.stats}><span>研究快照版本 {job.snapshot.snapshot.revision}</span><span>创建 UTC {job.created_at}</span></div>
   <p>{directionalReason(gate??'QUOTE_UNAVAILABLE')}</p><p>快照最多含 20 条独立 bid/ask 报价，不能冒充闭合 K 线或指标；当前尚无闭合 K 线、OHLCV 指标、社交情绪和公司财报输入。新闻缺失不等于没有风险事件。</p>
   {job.snapshot.snapshot.quote?<div className={styles.stage}><strong>快照真实报价</strong><span>Bid {job.snapshot.snapshot.quote.bid} / Ask {job.snapshot.snapshot.quote.ask}<br/>{job.snapshot.snapshot.quote.source}<br/>{job.snapshot.snapshot.quote.at} UTC</span></div>:<p>该快照没有有效行情，不能生成可执行建议。</p>}
   {job.state==='SNAPSHOT_ONLY'?<p>已保存真实快照，未调用模型。选择模型并配置服务器授权后，使用新的行情快照运行研究。</p>:null}
   {active?<p role="status">研究在后台独立运行，最多 9 次模型调用。关闭页面不会开启自动下单；中断任务不会自动重试模型。</p>:null}
   {job.state==='FAILED'||job.state==='INTERRUPTED'?<p role="alert">{job.state==='INTERRUPTED'?'服务重启中断了研究，未自动重复调用模型。':'研究服务不可用或结果未通过校验，未产生订单。'}</p>:null}
   {result?<><h3>研究评级：{result.advice.rating}</h3><p>模型 {result.advice.model} · 调用 {result.advice.model_calls} 次 · 建议有效至 {result.advice.valid_until} UTC</p><p>完成时校验：{researchReason(result.assessment.reason)==result.assessment.reason?directionalReason(result.assessment.reason):researchReason(result.assessment.reason)}。此结果为只读记录，过期或账户变化后需重新研究。</p><div className={ui.notice}>候选 {result.advice.candidate.target} · 数量 {result.advice.candidate.quantity} · 止损 {result.advice.candidate.stop_loss??'无'} · 止盈 {result.advice.candidate.take_profit??'无'}<br/>{result.advice.candidate.reason}<br/>自动执行未启用。</div><div className={ui.reports}>{result.advice.reports.map(report=><article className={ui.report} key={report.role}><h3>{ResearchRoles[report.role]}</h3><pre>{report.text}</pre></article>)}</div></>:null}
   <details><summary>查看快照与来源摘要</summary><p className={styles.digest}>研究ID {job.job_id}<br/>SHA256 {job.snapshot.sha256}<br/>原市场 {job.snapshot.snapshot.config.instrument}<br/>上游 {status?.upstream_commit}</p></details><button className={ui.download} onClick={download}>下载当前研究记录</button>
  </section>:null}
  <section className={styles.card}><h2>本浏览器的研究记录</h2><p>只显示最近 20 条。各标签页独立选择账户，服务器校验记录归属；记录不会改写既有账本。</p><div className={ui.history}>{jobs.map(row=><button key={row.job_id} disabled={busy||active||retry} aria-pressed={job?.job_id===row.job_id} onClick={()=>{setJob(row);setAccount(row.account_id);}}><strong>{row.snapshot.snapshot.config.instrument} · {researchReason(row.state)}</strong><span>{row.created_at} · {row.job_id.slice(-8)}</span></button>)}</div>{ready&&jobs.length===0?<p>还没有研究记录。</p>:null}</section>
  <section className={styles.card}><h2>研究边界</h2><p>复用固定版本 TradingAgents 的多空、研究经理、Trader、三类风险观点和最终结论；使用 FC666 冻结快照，不用默认 Yahoo 品种映射替换现有黄金或 Bitget 行情。</p><p>上游评级不等于开仓方向。建议还需精确数量、止盈止损、时效及新闻和资金校验。本入口始终只记录建议，没有自动执行按钮。</p><a href="https://github.com/TauricResearch/TradingAgents/tree/c4544913a14ed3df5f1ed7b9e8483270ce20682a" target="_blank" rel="noreferrer">查看固定版本源码 ↗</a></section>
 </main>;
}
