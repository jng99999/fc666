'use client';
import Link from 'next/link';
import {useEffect,useRef,useState} from 'react';
import type {z} from 'zod';
import WorkspaceSelect from '../../../components/workspace-select';
import {DirectionalAccounts,DirectionalAccount,DirectionalError,directionalRequest,directionalReason,type DirectionalView} from '../../../lib/directional';
import {ResearchJob,ResearchJobs,ResearchStatus,ResearchRoles,ResearchAutomation,automationReason,researchReason,type ResearchView,type ResearchAutomationView} from '../../../lib/tradingagents';
import styles from '../execution/page.module.css';
import ui from './page.module.css';

type AutomationCommand={request_id:string;expected_epoch:number;expected_revision:number;action:'START'|'PAUSE';interval_seconds:number};
function AutomationPanel({accountId,instrument,stopped,researchBusy,openJob}:{accountId:string;instrument:string;stopped:boolean;researchBusy:boolean;openJob:(id:string)=>Promise<void>}){
 const [view,setView]=useState<ResearchAutomationView|null>(null),[interval,setInterval]=useState(''),[busy,setBusy]=useState(false),[hydrated,setHydrated]=useState(false),[error,setError]=useState(''),[readError,setReadError]=useState(''),[notice,setNotice]=useState(''),[pending,setPending]=useState<AutomationCommand|null>(null);
 const lock=useRef(false),alive=useRef(false),generation=useRef(0),latest=useRef<ResearchAutomationView|null>(null);
 function adopt(next:ResearchAutomationView){if(next.account_id!==accountId)throw new Error('自动模拟返回了不匹配的账户，请刷新核对。');if(latest.current&&next.epoch<latest.current.epoch)return;const first=latest.current===null;latest.current=next;setView(next);if(first)setInterval(String(next.interval_seconds));}
 async function read(signal?:AbortSignal){const version=generation.current;const next=await directionalRequest('research/automation/'+accountId,ResearchAutomation,undefined,signal);if(alive.current&&version===generation.current)adopt(next);}
 useEffect(()=>{
  alive.current=true;setHydrated(true);const abort=new AbortController();let timer:ReturnType<typeof setTimeout>;
  async function poll(){try{if(!lock.current){await read(abort.signal);if(alive.current)setReadError('');}}catch{if(alive.current&&!abort.signal.aborted)setReadError('自动模拟状态读取失败，保留最后确认状态；请刷新核对。');}finally{if(alive.current)timer=setTimeout(poll,5000);}}
  void poll();return()=>{alive.current=false;abort.abort();clearTimeout(timer);};
 },[accountId]);
 async function refresh(){if(lock.current)return;lock.current=true;setBusy(true);try{await read();setError('');setReadError('');}catch(value){setReadError(value instanceof Error?value.message:'自动模拟状态读取失败。');}finally{if(alive.current){lock.current=false;setBusy(false);}}}
 async function command(action:'START'|'PAUSE',retry?:AutomationCommand){
  if(lock.current||!view)return;lock.current=true;generation.current++;setBusy(true);setError('');setNotice('');let request=retry;
  try{
   if(!request){const account=await directionalRequest('accounts/'+accountId,DirectionalAccount);request={request_id:crypto.randomUUID(),expected_epoch:view.epoch,expected_revision:account.revision,action,interval_seconds:action==='START'?Number(interval):view.interval_seconds};}
   if(!alive.current)return;setPending(request);
   const next=await directionalRequest('research/automation/'+accountId,ResearchAutomation,request);
   if(alive.current){adopt(next);setInterval(String(next.interval_seconds));setPending(null);setNotice(action==='PAUSE'?'已暂停自动模拟并撤销待处理建议；持仓保护继续。':'已保存自动模拟意愿；实际运行取决于下方模型、授权、行情与新闻状态。');}
  }catch(value){
   if(!alive.current)return;
   if(value instanceof DirectionalError&&value.status<500){setPending(null);if(value.status===409){try{await read();}catch{/* Keep the last confirmed state and the conflict visible. */}setError('账户或控制版本已变化，已尝试刷新；请核对最新状态后重新操作。');}else setError(value.message);}
   else{setError(request?'提交结果尚未确认；请刷新核对，或重查同一请求。':'账户读取失败，未发送自动模拟控制。');}
  }finally{if(alive.current){lock.current=false;setBusy(false);}}
 }
 const validInterval=/^\d+$/.test(interval)&&Number(interval)>=300&&Number(interval)<=86400;
 const receipt=view?.receipt;
 const receiptStatus=receipt?({ACCEPTED:'已接收，等待后续报价',FILLED:'已模拟成交',REJECTED:'已拒绝',HOLD:'本次不采取行动',SKIPPED:'本次未执行'}[receipt.status]):'';
 return <section className={styles.card} data-testid="ai-automation" aria-busy={!view||busy}>
  <h2>AI 自动模拟</h2><p>仅此 AI 研究账户可启用。服务器定时保存新快照、调用模型并校验建议，合格建议等待后续真实报价模拟成交；实盘始终关闭。</p>
  <div className={ui.notice} role="status">{view?`${view.enabled?'已启用自动模拟意愿':'未启用自动模拟'} · ${directionalReason(automationReason(view.state==='HISTORY_UNAVAILABLE'&&instrument==='XAU/USD'?'HISTORICAL_CANDLES_UNAVAILABLE':view.state))}`:readError?'自动模拟状态尚未确认，请刷新重试。':'正在读取此账户的自动模拟状态…'}</div>
  <p>未配置模型、未授权、新闻缺失或行情过期时保持等待，不会用规则代替 AI。暂停可恢复，会撤销待处理的自动建议；已有持仓止盈止损继续，暂停不会立即平仓。</p>
  <div className={ui.controls}><label>研究间隔 · 秒（300–86400）<input type="number" min="300" max="86400" step="1" value={interval} disabled={!view||busy||Boolean(pending)||Boolean(view.enabled)} onChange={event=>setInterval(event.target.value)}/></label>
   <button disabled={!view||busy||Boolean(pending)||Boolean(view.enabled)||stopped||view.state==='ACCOUNT_STOPPED'||!validInterval} onClick={()=>void command('START')}>{busy?'正在核对…':'启用自动模拟'}</button>
   <button disabled={!view||busy||Boolean(pending)||!view.enabled} onClick={()=>void command('PAUSE')}>暂停自动模拟</button>
   <button disabled={!hydrated||busy} onClick={()=>void refresh()}>刷新自动模拟状态</button>
  </div>
  {!validInterval&&view?<p>研究间隔须为 300 到 86400 秒的整数。修改已启用账户的间隔，请先暂停。</p>:null}
  {stopped?<p>此账户已停止新风险，不能重新启用；持仓保护退出仍继续。</p>:null}
  {pending&&!busy?<div className={ui.notice}><p>上次控制请求结果待核对；沿用同一请求编号，避免重复变更。</p><button onClick={()=>void command(pending.action,pending)}>重查同一自动模拟请求</button></div>:null}
  {notice?<p role="status">{notice}</p>:null}{readError?<p role="alert" className={ui.error}>{readError}</p>:null}{error?<p role="alert" className={ui.error}>{error}</p>:null}
  {view?<p className={ui.wrap}>控制版本 {view.epoch} · 下次检查 UTC {view.next_due??'等待服务器安排'}<br/>最近研究 {view.last_job??'暂无'}</p>:null}
  {view?.last_job?<button className={ui.download} disabled={busy||researchBusy} onClick={()=>{setError('');void openJob(view.last_job!).catch(()=>setError('最近研究读取失败，请刷新研究记录重试。'));}}>查看最近模型建议</button>:null}
  <h3>最近自动模拟回执</h3>{receipt?<div className={ui.notice}><strong>{receiptStatus}</strong><p>{directionalReason(automationReason(receipt.reason))}</p><p className={ui.wrap}>研究 {receipt.job_id}<br/>请求 {receipt.request_id}<br/>{receipt.at} UTC{receipt.account_revision===undefined?null:<><br/>账户版本 {receipt.account_revision}</>}{receipt.fills_count===undefined?null:<> · 累计成交 {receipt.fills_count} 笔</>}</p>{receipt.status==='ACCEPTED'?<p>接收不代表成交。必须等待严格晚于决策接收时刻的有效报价；服务器会再次检查风险。</p>:null}</div>:<p>暂无自动模拟回执；等待状态不会伪造成交。</p>}
  <p><Link href="/paper/directional">查看账户持仓、保护退出与成交记录 →</Link></p>
 </section>;
}

export default function AIResearch(){
 const [ready,setReady]=useState(false),[busy,setBusy]=useState(false),[error,setError]=useState('');
 const [status,setStatus]=useState<z.infer<typeof ResearchStatus>|null>(null),[accounts,setAccounts]=useState<DirectionalView[]>([]),[allowed,setAllowed]=useState<string[]>([]),[account,setAccount]=useState('');
 const [jobs,setJobs]=useState<ResearchView[]>([]),[job,setJob]=useState<ResearchView|null>(null),[retry,setRetry]=useState(false);
 const lock=useRef(false),pending=useRef<{path:string;body:{mode:'PREPARE'|'ANALYZE';request_id:string;expected_revision:number}}|null>(null);
 async function load(signal?:AbortSignal){
  // Establish one owner cookie before reading the research collection.
  const owned=await directionalRequest('accounts',DirectionalAccounts,undefined,signal);
  setAccounts(owned.accounts);
  const linked=new URL(window.location.href).searchParams.get('account');if(linked&&owned.accounts.some(row=>row.account_id===linked))setAccount(previous=>previous||linked);
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
 async function openJob(id:string){if(lock.current)return;lock.current=true;setBusy(true);try{const next=await directionalRequest('research/jobs/'+id,ResearchJob);setJob(next);setJobs(rows=>[next,...rows.filter(row=>row.job_id!==next.job_id)].slice(0,20));}finally{lock.current=false;setBusy(false);}}
 function download(){if(!job)return;const url=URL.createObjectURL(new Blob([JSON.stringify(job,null,2)],{type:'application/json'}));const link=document.createElement('a');link.href=url;link.download=`fc666-research-${job.job_id}.json`;link.click();URL.revokeObjectURL(url);}
 const result=job?.result&&'advice'in job.result?job.result:null;
 const current=accounts.find(row=>row.account_id===account),gate=job?.snapshot.snapshot.entry_gate,evidence=job?.snapshot.snapshot.market_evidence;
 return <main className={styles.page} aria-busy={!ready}>
  <header className={styles.heading}><div><p className={styles.eyebrow}>策略研究 / AI 研究</p><h1>TradingAgents 研究</h1><p className={styles.muted}>用真实快照形成多空观点，独立校验每一条建议。</p></div><span className={styles.badge}>研究与模拟 · 实盘关闭</span></header>
  <nav className={styles.navigation} aria-label="AI研究导航"><Link href="/research">策略回测</Link><Link href="/paper/directional">双向模拟账户</Link><Link href="/research/execution">执行验证</Link><Link href="/system/api">API 接入</Link><Link href="/system">连接状态</Link></nav>
  <section className={styles.card}><h2>选择研究账户</h2><p>本卡片的保存快照与单次研究只记录建议，不创建订单。AI 研究账户可在下方单独启用自动模拟；模型调用仍需服务器配置和账户授权，新闻门禁与执行保护独立生效。</p>
   <div className={ui.notice} role="status">{!status?(ready?'研究连接尚未确认，请刷新研究记录重试。':'正在检查研究连接…'):status.model_configured?'模型配置已就绪；真实调用效果仍需验证。建议仍需校验，不保证收益。':status.runner_configured?`研究连接已配置，但模型尚未就绪：${automationReason(status.state)}。仍可保存快照。`:'模型研究尚未配置。可先保存真实行情快照；不会使用默认模型或产生调用费用。'}</div>
   <div className={ui.controls}><label>独立模拟账户<WorkspaceSelect label="AI研究账户" value={account} disabled={!ready||busy||retry||active} onValueChange={value=>{setAccount(value);setError('');setJob(null);}} options={[{value:'',label:'请选择账户',disabled:true},...accounts.map(row=>({value:row.account_id,label:`${row.config.instrument} · ${row.metadata.strategy==='AI_RESEARCH'?'AI 研究 · ':''}${row.account_id.slice(-8)}${row.status==='STOPPED'?' · 已停新风险':''}`}))]}/></label>
    <button disabled={!ready||busy||!account||retry||active} onClick={()=>create('PREPARE')}>保存研究快照</button><button disabled={!ready||busy||!account||!status?.model_configured||!allowed.includes(account)||retry||active} onClick={()=>create('ANALYZE')}>{busy?'正在提交…':'运行 AI 研究'}</button><button disabled={!ready||busy} onClick={refresh}>刷新研究记录</button>
   </div>
   {retry?<button disabled={busy} onClick={()=>create(pending.current?.body.mode??'PREPARE')}>重试同一研究请求</button>:null}
   {ready&&accounts.length===0?<p>此浏览器没有独立双向账户。<Link href="/paper/directional">创建模拟账户 →</Link></p>:null}
   {current&&status?.runner_configured&&!allowed.includes(account)?<p>该账户尚未获服务器的模型研究授权。</p>:null}
   {current&&current.metadata.strategy!=='AI_RESEARCH'?<p>当前账户仅支持单次旁路研究。需要自动模拟时，请<Link href="/paper/directional">创建决策方式为“AI 研究自动模拟”的独立账户</Link>。</p>:null}
   {error?<p role="alert" className={ui.error}>{error}</p>:null}
  </section>
  {current?.metadata.strategy==='AI_RESEARCH'&&status?.automation_supported?<AutomationPanel key={current.account_id} accountId={current.account_id} instrument={current.config.instrument} stopped={current.status==='STOPPED'} researchBusy={busy} openJob={openJob}/>:null}
  {current?.metadata.strategy==='AI_RESEARCH'&&status&&!status.automation_supported?<p role="alert">自动模拟控制暂不可用，请检查服务器状态；未发送启动请求。</p>:null}
  {job?<section className={styles.card} data-testid="ai-research-job"><h2>{job.snapshot.snapshot.config.instrument} · {researchReason(job.state)}</h2><div className={ui.stats}><span>研究快照版本 {job.snapshot.snapshot.revision}</span><span>创建 UTC {job.created_at}</span></div>
   <p>{directionalReason(gate??'QUOTE_UNAVAILABLE')}</p><p>快照最多含 20 条独立 bid/ask 报价，报价样本不冒充闭合 K 线。{evidence?.status==='READY'?`已接入 ${evidence.bars.length} 根独立历史闭合柱及 EMA20、RSI14、ATR14 指标。`:'历史闭合柱与指标尚未就绪，不能据此声称已具备完整历史输入。'}社交情绪和公司财报未提供；新闻缺失不等于没有风险事件。</p>
   {evidence?<div className={ui.notice}><strong>{evidence.status==='READY'?'历史行情与指标已就绪':automationReason(evidence.reason??evidence.status)}</strong><p className={ui.wrap}>报价源 {evidence.quote_source??'未提供'}<br/>闭合柱源 {evidence.candle_source??'未提供'} · {evidence.bars.length} 根</p>{evidence.indicators?<p className={ui.wrap}>EMA20 {evidence.indicators.ema20} · RSI14 {evidence.indicators.rsi14??'不可用'} · ATR14 {evidence.indicators.atr14}</p>:null}<details><summary>查看历史行情与指标来源</summary><pre className={ui.evidence}>{JSON.stringify(evidence,null,2)}</pre></details></div>:null}
   {job.snapshot.snapshot.quote?<div className={styles.stage}><strong>快照真实报价</strong><span>Bid {job.snapshot.snapshot.quote.bid} / Ask {job.snapshot.snapshot.quote.ask}<br/>{job.snapshot.snapshot.quote.source}<br/>{job.snapshot.snapshot.quote.at} UTC</span></div>:<p>该快照没有有效行情，不能生成可执行建议。</p>}
   {job.state==='SNAPSHOT_ONLY'?<p>已保存真实快照，未调用模型。选择模型并配置服务器授权后，使用新的行情快照运行研究。</p>:null}
   {active?<p role="status">研究在后台独立运行，最多 9 次模型调用。{job.mode==='AUTO'?'这是已启用的自动模拟研究，结果仍需服务器校验。':'本次单次研究不下单。'}中断任务不会自动重试模型。</p>:null}
   {job.state==='FAILED'||job.state==='INTERRUPTED'?<p role="alert">{job.state==='INTERRUPTED'?'研究已中断，未自动重复调用模型。请核对已有自动模拟回执与账户账本；不要另建请求重复下单。':'研究未完成，请核对自动模拟回执与账户账本；不要另建请求重复下单。'}</p>:null}
   {result?<><h3>研究评级：{result.advice.rating}</h3><p>模型 {result.advice.model} · 调用 {result.advice.model_calls} 次 · 建议有效至 {result.advice.valid_until} UTC</p><p>完成时校验：{researchReason(result.assessment.reason)==result.assessment.reason?directionalReason(result.assessment.reason):researchReason(result.assessment.reason)}。此结果为只读记录，过期或账户变化后需重新研究。</p><div className={ui.notice}>候选 {result.advice.candidate.target} · 数量 {result.advice.candidate.quantity} · 止损 {result.advice.candidate.stop_loss??'无'} · 止盈 {result.advice.candidate.take_profit??'无'}<br/>{result.advice.candidate.reason}<br/>{job.mode==='AUTO'?'自动模拟是否接收或成交，以账户回执与成交记录为准。':'本次单次研究不创建订单。'}</div><div className={ui.reports}>{result.advice.reports.map(report=><article className={ui.report} key={report.role}><h3>{ResearchRoles[report.role]}</h3><pre>{report.text}</pre></article>)}</div></>:null}
   <details><summary>查看快照与来源摘要</summary><p className={styles.digest}>研究ID {job.job_id}<br/>SHA256 {job.snapshot.sha256}<br/>原市场 {job.snapshot.snapshot.config.instrument}<br/>上游 {status?.upstream_commit}</p></details><button className={ui.download} onClick={download}>下载当前研究记录</button>
  </section>:null}
  <section className={styles.card}><h2>本浏览器的研究记录</h2><p>只显示最近 20 条。各标签页独立选择账户，服务器校验记录归属；记录不会改写既有账本。</p><div className={ui.history}>{jobs.map(row=><button key={row.job_id} disabled={busy||active||retry} aria-pressed={job?.job_id===row.job_id} onClick={()=>{setJob(row);setAccount(row.account_id);}}><strong>{row.snapshot.snapshot.config.instrument} · {researchReason(row.state)}</strong><span>{row.created_at} · {row.job_id.slice(-8)}</span></button>)}</div>{ready&&jobs.length===0?<p>还没有研究记录。</p>:null}</section>
  <section className={styles.card}><h2>研究边界</h2><p>复用固定版本 TradingAgents 的多空、研究经理、Trader、三类风险观点和最终结论；使用 FC666 冻结快照，不用默认 Yahoo 品种映射替换现有黄金或 Bitget 行情。</p><p>上游评级不等于开仓方向。建议还需精确数量、止盈止损、时效及新闻和资金校验。单次研究只记录建议；只有显式启用的 AI 研究账户参与自动模拟，不连接实盘私有交易 API。模型密钥与账户授权由服务器管理。</p><a href="https://github.com/TauricResearch/TradingAgents/tree/c4544913a14ed3df5f1ed7b9e8483270ce20682a" target="_blank" rel="noreferrer">查看固定版本源码 ↗</a></section>
 </main>;
}
