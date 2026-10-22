'use client';
import Link from 'next/link';
import {useEffect,useRef,useState,type FormEvent} from 'react';
import {GoldRequestError,goldRequest} from '../../../lib/gold';
import {PendingPolicy,PolicyAccount,PolicyDetail,PolicyFill,PolicyList,PolicyStatus,verifyPolicyReceipt,policyReasonLabels,policyStatusLabels,type PendingPolicyType,type PolicyAccountType,type PolicyDetailType,type PolicyStatusType} from '../../../lib/gold-policy';
import '../gold.css';
import './automation.css';

const selectedKey='fc666-gold-policy-selected',pendingKey='fc666-gold-policy-pending';
const money=(v:string)=>Number(v).toLocaleString('zh-CN',{minimumFractionDigits:2,maximumFractionDigits:2});
const stamp=(v:number|null)=>v===null?'尚无记录':new Date(v).toLocaleString('zh-CN',{hour12:false});
const message=(e:unknown)=>e instanceof Error?e.message:'服务请求未完成';
type StopSnapshot={id:string;revision:number;model:string};
function ReceiptFill({value}:{value:unknown}){
 const fill=PolicyFill.safeParse(value);
 if(value===undefined)return null;
 if(!fill.success)return <p role="alert">成交字段格式异常，请核对原始收据；不会推断成交数量。</p>;
 return <p className="gold-automation-fill">模拟{fill.data.side==='BUY'?'买入':'卖出'} <strong>{fill.data.ounces} oz</strong> · 成交价 ${fill.data.price} · 手续费 ${fill.data.fee}</p>;
}

function PolicyReceipts({account}:{account:PolicyDetailType}){
 const [older,setOlder]=useState<PolicyDetailType['events']>([]),[before,setBefore]=useState(account.next_before),[busy,setBusy]=useState(false),[error,setError]=useState('');
 async function more(){
  if(before===null||busy)return;setBusy(true);setError('');
  try{const detail=PolicyDetail.parse(await goldRequest(`policy/accounts/${account.id}?before=${before}`));setOlder(previous=>[...previous,...detail.events]);setBefore(detail.next_before);}catch(e){setError(message(e));}finally{setBusy(false);}
 }
 const events=[...new Map([...account.events,...older].map(event=>[event.seq,event])).values()].sort((a,b)=>b.seq-a.seq);
 return <section className="gold-card"><h2>决策与成交收据</h2><p>显示已加载的收据。账户版本变化时返回最新一页，避免混用旧分页。</p>{events.length?<ol className="gold-events">{events.map(event=><li key={event.seq}><div><strong>#{event.seq} · {event.kind}</strong><time>{stamp(event.at)}</time></div>{typeof event.details.reason==='string'?<p>{policyReasonLabels[event.details.reason]??event.details.reason}</p>:null}{typeof event.details.target_weight==='string'?<p>模型目标仓位 {Number(event.details.target_weight)*100}%</p>:null}<ReceiptFill value={event.details.fill}/><details><summary>查看该笔真实收据</summary><pre>{JSON.stringify(event.details,null,2)}</pre></details><small className="gold-automation-digest">账本摘要 {event.hash}</small></li>)}</ol>:<p>暂无收据；不会根据按钮点击推断已成交。</p>}{error?<p role="alert">{error}</p>:null}{before!==null?<button disabled={busy} onClick={()=>void more()}>{busy?'读取中…':'更早的收据'}</button>:null}</section>;
}

export default function GoldAutomation(){
 const [status,setStatus]=useState<PolicyStatusType|null>(null),[accounts,setAccounts]=useState<PolicyAccountType[]>([]),[selected,setSelected]=useState(''),[account,setAccount]=useState<PolicyDetailType|null>(null);
 const [modelId,setModelId]=useState(''),[ack,setAck]=useState(false),[pending,setPending]=useState<PendingPolicyType|null>(null),[pendingBroken,setPendingBroken]=useState(false),[busy,setBusy]=useState(false),[loading,setLoading]=useState(true),[refresh,setRefresh]=useState(0);
 const [statusError,setStatusError]=useState(''),[accountError,setAccountError]=useState(''),[mutationError,setMutationError]=useState(''),[notice,setNotice]=useState(''),[stop,setStop]=useState<StopSnapshot|null>(null);
 const inFlight=useRef(false),dialog=useRef<HTMLDialogElement>(null);
 useEffect(()=>{try{const saved=sessionStorage.getItem(selectedKey);if(saved)setSelected(saved);const raw=sessionStorage.getItem(pendingKey);if(raw){const parsed=PendingPolicy.safeParse(JSON.parse(raw));if(parsed.success)setPending(parsed.data);else setPendingBroken(true);}}catch{setPendingBroken(true);}},[]);
 useEffect(()=>{
  const abort=new AbortController();let timer:ReturnType<typeof setTimeout>;
  async function load(){
   setLoading(true);
   const signal=AbortSignal.any([abort.signal,AbortSignal.timeout(15000)]);
   const [health,list]=await Promise.allSettled([goldRequest('policy/status',signal),goldRequest('policy/accounts',signal)]);
   if(abort.signal.aborted)return;
   try{if(health.status==='rejected')throw health.reason;setStatus(PolicyStatus.parse(health.value));setStatusError('');}catch(e){setStatus(null);setStatusError(message(e));}
   try{
    if(list.status==='rejected')throw list.reason;
    const rows=PolicyList.parse(list.value).accounts;setAccounts(rows);
    if(selected){
     if(!rows.some(row=>row.id===selected))throw new Error('当前账户不属于此浏览器会话，请显式选择可用账户；不会自动切换。');
     const detail=PolicyDetail.parse(await goldRequest(`policy/accounts/${selected}`,signal));
     if(!abort.signal.aborted)setAccount(detail);
    }
    if(!abort.signal.aborted)setAccountError('');
   }catch(e){if(!abort.signal.aborted){setAccount(null);setAccountError(message(e));}}
   finally{if(!abort.signal.aborted){setLoading(false);timer=setTimeout(()=>void load(),5000);}}
  }
  void load();return()=>{abort.abort();clearTimeout(timer);};
 },[selected,refresh]);
 useEffect(()=>{const node=dialog.current;if(stop&&!node?.open)node?.showModal();if(!stop&&node?.open)node.close();},[stop]);
 const model=status?.status==='READY'?status.models.find(row=>row.id===modelId):undefined;
 function select(id:string){setSelected(id);setAccount(null);setStop(null);setMutationError('');setNotice('');try{sessionStorage.setItem(selectedKey,id);}catch{}}
 function remember(value:PendingPolicyType|null){setPending(value);try{if(value)sessionStorage.setItem(pendingKey,JSON.stringify(value));else sessionStorage.removeItem(pendingKey);setPendingBroken(false);return true;}catch{setPendingBroken(true);return false;}}
 async function reconcile(request:PendingPolicyType){
  if(inFlight.current)return;inFlight.current=true;setBusy(true);setMutationError('');setNotice('');
  try{
   const receipt=await verifyPolicyReceipt(request,await goldRequest(`policy/requests/${request.body.request_id}`));
   if(!receipt.found){setNotice('当前快照尚未找到已提交收据，请求仍可能在途。已保留原编号与参数，请稍后继续只读核对。');return;}
   remember(null);select(receipt.account_id);setNotice(`已核对原始 ${receipt.kind} 收据（版本 ${receipt.seq}）。当前账户版本 ${receipt.account.revision}；是否成交以收据中的实际成交字段为准。`);
  }catch(e){setMutationError(`${message(e)}。原请求仍保留，未发送写指令。`);}
  finally{inFlight.current=false;setBusy(false);setRefresh(v=>v+1);}
 }
 async function submit(request:PendingPolicyType){
  if(inFlight.current)return;
  inFlight.current=true;setBusy(true);setMutationError('');setNotice('');
  try{
   if(!remember({...request,uncertain:true})){setMutationError('请求编号无法持久保存，本次未发送任何写请求。请恢复浏览器存储后再试。');return;}
   const path=request.kind==='create'?'policy/accounts':`policy/accounts/${request.account_id}/command`;
   const row=PolicyAccount.parse(await goldRequest(path,undefined,request.body));
   remember(null);select(row.id);setNotice('请求已由服务器确认。是否成交，以本账户最新收据为准；HTTP成功不等于下单成交。');
  }catch(e){
   const definiteRejection=!request.uncertain&&e instanceof GoldRequestError&&e.status>=400&&e.status<500&&e.status!==408;
   if(definiteRejection){remember(null);setMutationError(`${message(e)}。服务器明确拒绝了本次请求；请核对刷新后的账户再操作。`);}
   else setMutationError(`${message(e)}。请求编号已保留，不会自动重发；请刷新核对，或显式重试相同请求。`);
  }
  finally{inFlight.current=false;setBusy(false);setRefresh(v=>v+1);}
 }
 function create(event:FormEvent){
  event.preventDefault();if(!model||!ack||status?.status!=='READY'||pending||pendingBroken||inFlight.current)return;
  const body={...model.account_config,mode:'POLICY' as const,model_id:model.id,experimental:true as const,request_id:crypto.randomUUID()};
  void submit({kind:'create',body,uncertain:false});
 }
 function command(action:'DECIDE'|'PAUSE'|'RESUME'|'STOP',snapshot?:StopSnapshot){
  if(!account||pending||pendingBroken||inFlight.current)return;
  if(snapshot&&(snapshot.id!==account.id||snapshot.revision!==account.revision)){setMutationError('确认期间账户已更新，请重新查看并确认。');return;}
  setStop(null);
  void submit({kind:'command',account_id:account.id,body:{request_id:crypto.randomUUID(),expected_revision:account.revision,action},uncertain:false});
 }
 const controlsDisabled=busy||loading||Boolean(pending)||pendingBroken||Boolean(accountError);
 return <main className="gold-workspace gold-automation">
  <header className="gold-heading"><div><p>黄金 · AI 模拟执行</p><h1>模型与模拟账户</h1><span>服务端校验 · 独立 USD 账本 · 实盘关闭</span></div><button disabled={loading} onClick={()=>setRefresh(v=>v+1)}>{loading?'同步中…':'刷新状态'}</button></header>
  <div className="gold-automation-links"><Link href="/gold">黄金行情与规则模拟</Link><Link href="/gold/ppo">PPO 研究结果</Link></div>
  <section className="gold-card"><h2>执行状态</h2>{statusError?<p role="alert" className="gold-error">状态读取失败：{statusError}。未确认可用状态时不会开放模型决策。</p>:null}
   {status?<><strong>{policyStatusLabels[status.status]}</strong><dl className="gold-values"><div><dt>完整同源小时柱</dt><dd>{status.complete_bars} / {status.required_bars}</dd></div><div><dt>小时数据状态</dt><dd>{policyStatusLabels[status.warmup_status]}</dd></div><div><dt>最新闭合小时</dt><dd>{stamp(status.latest_close_at)}</dd></div><div><dt>可用模型</dt><dd>{status.models.length}</dd></div><div><dt>状态检查时间</dt><dd>{stamp(status.checked_at)}</dd></div></dl><progress value={status.complete_bars} max={status.required_bars} aria-label="真实小时历史暖机进度"/></>:!statusError?<p>正在读取后台真实状态…</p>:null}
   <p>使用瑞讯同源采样形成的完整 1h 柱；缺口保留，不把历史 HistData 或 1m 指标冒充实时模型输入。模型需通过服务端准入，账户创建和恢复后等待下一个闭合小时。</p>
  </section>
  {accountError?<p role="alert" className="gold-message gold-error">账户读取失败：{accountError}</p>:null}
  {mutationError?<p role="alert" className="gold-message gold-error">{mutationError}</p>:null}{notice?<p role="status" className="gold-message">{notice}</p>:null}
  {pendingBroken?<section className="gold-card"><h2>本标签页请求记录无法恢复</h2><p role="alert">操作已锁定。请先查看服务器账户与账本，确认没有需要核对的未知请求；此处不会删除服务器记录。</p><button disabled={busy} onClick={()=>{try{sessionStorage.removeItem(pendingKey);setPending(null);setPendingBroken(false);setMutationError('');}catch{setMutationError('浏览器存储仍不可用，请恢复存储权限后再试。');}}}>已核对账本，清除此标签页的损坏记录</button></section>:null}
  {pending?<section className="gold-card"><h2>有待核对的请求</h2><p>{pending.kind==='create'?'创建模拟账户':`${pending.body.action} · ${pending.account_id}`}。请先核对账本，不要新建另一笔相同指令。</p><small className="gold-automation-digest">请求编号 {pending.body.request_id}</small><div className="gold-actions"><button disabled={busy} onClick={()=>void reconcile(pending)}>{busy?'核对中…':'只读核对原始回执'}</button><button disabled={busy||pendingBroken} onClick={()=>void submit(pending)}>重试相同请求编号</button></div></section>:null}
  <div className="gold-grid"><section className="gold-card"><h2>独立 AI 模拟账户</h2>{accounts.length?<label>选择账户<select aria-label="选择AI黄金模拟账户" value={selected} disabled={busy} onChange={e=>{if(!inFlight.current)select(e.target.value);}}><option value="">请选择账户</option>{accounts.map(row=><option key={row.id} value={row.id}>{row.config.policy.model_id} · {row.id.slice(-8)} · {row.status}</option>)}</select></label>:<p>{loading?'正在读取账户…':'此浏览器还没有模型模拟账户，原手动与 SMA20 账户及账本保留。'}</p>}
   {account?<><dl className="gold-values"><div><dt>美元现金</dt><dd>${money(account.cash)}</dd></div><div><dt>黄金持仓</dt><dd>{money(account.quantity)} oz</dd></div><div><dt>累计额外费用</dt><dd>${money(account.fees)}</dd></div><div><dt>已实现盈亏</dt><dd>${money(account.realized)}</dd></div><div><dt>账户 / 执行阶段</dt><dd>{account.status} / {account.phase}</dd></div><div><dt>最近处理小时</dt><dd>{stamp(account.last_bar)}</dd></div><div><dt>账户版本</dt><dd>{account.revision}</dd></div></dl><p className="gold-automation-digest">模型 {account.config.policy.model_id} · {account.config.policy.protocol}<br/>权重 {account.config.policy.actor_sha256}</p>
    <div className="gold-actions"><button disabled={controlsDisabled||account.status!=='ACTIVE'||status?.status!=='READY'} onClick={()=>command('DECIDE')}>核对本小时决策</button>{account.status==='ACTIVE'?<button disabled={controlsDisabled} onClick={()=>command('PAUSE')}>暂停模拟</button>:account.status==='PAUSED'?<button disabled={controlsDisabled||!status?.enabled} onClick={()=>command('RESUME')}>恢复模拟</button>:null}<button disabled={controlsDisabled||account.status==='STOPPED'} onClick={()=>setStop({id:account.id,revision:account.revision,model:account.config.policy.model_id})}>停止此实验账户</button></div><p>启用后的账户由后台推进，关闭网页不停止运行；本小时已处理则不补单。暂停和停止均保留现金、持仓与全部账本，停止后不能恢复。</p>
   </>:null}
  </section><section className="gold-card"><h2>创建实验账户</h2><form onSubmit={create}><label>服务端已准入模型<select aria-label="选择准入模型" value={modelId} onChange={e=>{setModelId(e.target.value);setAck(false);}} disabled={!status?.enabled||busy||Boolean(pending)||pendingBroken}><option value="">{status?.models.length?'请选择模型':'暂无可用模型'}</option>{status?.models.map(row=><option key={row.id} value={row.id}>{row.id}</option>)}</select></label>{model?<><dl className="gold-values">{Object.entries(model.account_config).map(([key,value])=><div key={key}><dt>{({initial_cash:'初始模拟美元',allocation:'最大权益仓位',max_spread:'点差上限 USD/oz',commission:'单边费用 USD/oz',slippage:'单边滑点 USD/oz'} as Record<string,string>)[key]}</dt><dd>{value}</dd></div>)}</dl><p>资格到期 {stamp(model.valid_until)} · {model.feature_version}</p></>:<p>前端不能注册权重、修改准入或开启实盘。未完成研究验收的模型不会出现在这里。</p>}<label className="gold-automation-ack"><input type="checkbox" checked={ack} onChange={e=>setAck(e.target.checked)} disabled={!model||busy}/>我理解这是模型实验，历史结果不保证未来收益；账户使用独立模拟资金。</label>
   {accounts.length>=8?<p id="gold-policy-account-limit" role="status">已达到此浏览器会话的 8 个实验账户上限，暂不能创建新账户；已有账户与账本仍可查看。</p>:null}
   <button className="gold-primary" type="submit" aria-describedby={accounts.length>=8?'gold-policy-account-limit':undefined} disabled={busy||loading||Boolean(pending)||pendingBroken||!status?.enabled||!model||!ack||accounts.length>=8}>{busy?'处理中…':'创建独立模型模拟账户'}</button></form></section></div>
  {account?<PolicyReceipts key={`${account.id}:${account.revision}`} account={account}/>:null}
  <dialog ref={dialog} className="gold-automation-dialog" onCancel={()=>setStop(null)} onClose={()=>setStop(null)} aria-labelledby="policy-stop-heading"><h2 id="policy-stop-heading">停止此实验模拟账户</h2>{stop?<><p>模型 {stop.model}</p><p className="gold-automation-digest">账户 {stop.id}</p><p>永久停止且不能恢复。不自动平仓；现金、库存和旧账本保留。</p>{account?.id!==stop.id||account.revision!==stop.revision?<p role="alert">账户已更新，请取消后重新核对。</p>:null}<div className="gold-actions"><button autoFocus onClick={()=>setStop(null)}>取消</button><button disabled={busy||account?.id!==stop.id||account.revision!==stop.revision} onClick={()=>command('STOP',stop)}>确认停止此账户</button></div></>:null}</dialog>
 </main>;
}
