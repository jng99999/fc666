'use client';
import Link from 'next/link';
import {useEffect,useRef,useState,type FormEvent} from 'react';
import {z} from 'zod';
import WorkspaceSelect from '../../../components/workspace-select';
import {DirectionalAccount,DirectionalAccounts,DirectionalStatus,DirectionalError,directionalReason,directionalRequest,type DirectionalView} from '../../../lib/directional';
import styles from './page.module.css';

const KEY='fc666-directional-selected';
const money=(value:string)=>Number(value).toLocaleString('zh-CN',{minimumFractionDigits:2,maximumFractionDigits:4});
const initial={instrument:'XAU/USD',strategy:'MANUAL',initial_cash:'10000',tick_size:'0.01',quantity_step:'0.001',max_quantity:'10',max_order_quote:'5000',fee_rate:'0.001',slippage:'0.05',news_pre_seconds:'1800',news_post_seconds:'1800',quantity:'1',stop_loss_percent:'0.01',take_profit_percent:'0.02'};

function StopDialog({target,busy,error,cancel,confirm}:{target:DirectionalView;busy:boolean;error:string;cancel:()=>void;confirm:()=>void}){
 const dialog=useRef<HTMLDialogElement>(null),button=useRef<HTMLButtonElement>(null),[code,setCode]=useState('');
 useEffect(()=>{const node=dialog.current;if(!node)return;const previous=document.activeElement instanceof HTMLElement?document.activeElement:null;node.showModal();button.current?.focus();return()=>{node.close();previous?.focus();};},[]);
 return <dialog ref={dialog} className={styles.dialog} aria-labelledby="directional-stop-title" onCancel={event=>{event.preventDefault();if(!busy)cancel();}}>
  <h2 id="directional-stop-title">停止新风险？</h2><p>{target.config.instrument} · {target.account_id.slice(-8)}</p><p>此账户不能恢复开仓。保留账本和持仓，后台继续执行已有止盈止损；不会立即平仓。</p>
  <form onSubmit={event=>{event.preventDefault();if(!busy&&code===target.account_id.slice(-4))confirm();}}><label>输入账户末四位 {target.account_id.slice(-4)}<input autoComplete="off" maxLength={4} value={code} disabled={busy} onChange={event=>setCode(event.target.value)}/></label>{error?<p role="alert">{error}</p>:null}<div className={styles.actions}><button ref={button} type="button" disabled={busy} onClick={cancel}>取消</button><button type="submit" disabled={busy||code!==target.account_id.slice(-4)}>确认停止新风险</button></div></form>
 </dialog>;
}

export default function DirectionalPage(){
 const [ready,setReady]=useState(false),[busy,setBusy]=useState(false),[loaded,setLoaded]=useState(false),[error,setError]=useState(''),[readError,setReadError]=useState(''),[notice,setNotice]=useState('');
 const [status,setStatus]=useState<z.infer<typeof DirectionalStatus>|null>(null),[accounts,setAccounts]=useState<DirectionalView[]>([]),[view,setView]=useState<DirectionalView|null>(null),[fields,setFields]=useState(initial);
 const [target,setTarget]=useState<'LONG'|'SHORT'>('LONG'),[quantity,setQuantity]=useState('1'),[stopLoss,setStopLoss]=useState(''),[takeProfit,setTakeProfit]=useState(''),[stopTarget,setStopTarget]=useState<DirectionalView|null>(null);
 const current=useRef<DirectionalView|null>(null),locked=useRef(false),selected=useRef('');
 const [pending,setPending]=useState<{path:string;body:unknown}|null>(null);
 function adopt(next:DirectionalView){if(current.current?.account_id===next.account_id&&next.revision<current.current.revision)return;current.current=next;setView(next);}
 function choose(id:string,rows=accounts){if(locked.current)return;selected.current=id;setNotice('');try{sessionStorage.setItem(KEY,id);}catch{setNotice('账户选择未能保存在此标签页，刷新后请重新选择。');}const next=rows.find(row=>row.account_id===id);current.current=next??null;setView(next??null);setError('');setPending(null);setStopLoss('');setTakeProfit('');}
 useEffect(()=>{
  let active=true;const abort=new AbortController();let timer:ReturnType<typeof setTimeout>;
  try{selected.current=sessionStorage.getItem(KEY)??'';}catch{/* The server list remains the source of truth. */}
  setReady(true);
  async function refresh(){
   try{
    const [health,list]=await Promise.all([directionalRequest('status',DirectionalStatus,undefined,abort.signal),directionalRequest('accounts',DirectionalAccounts,undefined,abort.signal)]);
    if(!active)return;setStatus(health);setAccounts(list.accounts);setLoaded(true);
    if(!locked.current){const row=list.accounts.find(account=>account.account_id===selected.current);if(row)adopt(row);else if(selected.current){current.current=null;setView(null);}}
    setReadError('');
   }catch(value){if(active)setReadError(value instanceof Error?value.message:'读取失败，保留最后确认账本。');}
   finally{if(active)timer=setTimeout(refresh,5000);}
  }
  void refresh();return()=>{active=false;abort.abort();clearTimeout(timer);};
 },[]);
 async function submit(path:string,body:unknown){
  if(locked.current)return;locked.current=true;setBusy(true);setError('');setNotice('');setPending({path,body});
  try{
   const next=await directionalRequest(path,DirectionalAccount,body);
   selected.current=next.account_id;try{sessionStorage.setItem(KEY,next.account_id);}catch{/* Server receipt is still shown. */}
   adopt(next);setAccounts(rows=>[next,...rows.filter(row=>row.account_id!==next.account_id)]);setPending(null);
   setNotice(path.endsWith('/decision')?'决策已记录。是否成交以后台后续报价与账户账本为准。':path.endsWith('/stop')?'已停止新风险，既有持仓继续受到止盈止损保护。':'独立模拟账户已创建。');
   if(path.endsWith('/stop'))setStopTarget(null);
  }catch(value){setError(value instanceof Error?value.message:'提交结果尚未确认，请刷新核对。');if(value instanceof DirectionalError&&[400,401,403,404,409,413,415,422,429].includes(value.status)){setPending(null);if(value.status===409){setStopTarget(null);setNotice('账户状态已变化，本次未执行。请刷新核对后重新操作。');}}}
  finally{locked.current=false;setBusy(false);}
 }
 async function refreshNow(){if(locked.current)return;locked.current=true;setBusy(true);setError('');try{const list=await directionalRequest('accounts',DirectionalAccounts);setAccounts(list.accounts);const row=list.accounts.find(account=>account.account_id===selected.current);if(row)adopt(row);setLoaded(true);}catch(value){setError(value instanceof Error?value.message:'刷新失败');}finally{locked.current=false;setBusy(false);}}
 function create(event:FormEvent){event.preventDefault();const {quantity,stop_loss_percent,take_profit_percent,...base}=fields;const automatic=fields.strategy==='SMA20_QUOTES';void submit('accounts',{...base,request_id:crypto.randomUUID(),news_pre_seconds:Number(fields.news_pre_seconds),news_post_seconds:Number(fields.news_post_seconds),...(fields.strategy==='AI_RESEARCH'?{}:{quantity:automatic?quantity:null,stop_loss_percent:automatic?stop_loss_percent:null,take_profit_percent:automatic?take_profit_percent:null})});}
 function decide(flat=false){const row=current.current;if(!row||locked.current)return;void submit(`accounts/${row.account_id}/decision`,{request_id:crypto.randomUUID(),expected_revision:row.revision,target:flat?'FLAT':target,quantity:flat?'0':quantity,stop_loss:flat?null:stopLoss,take_profit:flat?null:takeProfit});}
 function download(){if(!view)return;const url=URL.createObjectURL(new Blob([JSON.stringify(view,null,2)],{type:'application/json'}));const link=document.createElement('a');link.href=url;link.download=`directional-${view.account_id}-r${view.revision}.json`;link.click();setTimeout(()=>URL.revokeObjectURL(url),1000);}
 const set=(key:keyof typeof initial,value:string)=>setFields(previous=>({...previous,[key]:value}));
 const input=(key:keyof typeof initial,label:string,min:string,max?:string)=><label key={key}>{label}<input required type="number" step={key.startsWith('news_')?'1':'any'} min={min} max={max} value={fields[key]} onChange={event=>set(key,event.target.value)}/></label>;
 const canEnter=Boolean(view&&view.status==='RUNNING'&&!view.entry_halted&&!view.position&&!view.pending&&view.entry_gate==='CLEAR'&&view.last_quote);
 const validBracket=Boolean(view?.last_quote&&Number(quantity)>0&&Number(stopLoss)>0&&Number(takeProfit)>0&&(target==='LONG'?Number(stopLoss)<Number(view.last_quote.bid)&&Number(takeProfit)>Number(view.last_quote.ask):Number(takeProfit)<Number(view.last_quote.bid)&&Number(stopLoss)>Number(view.last_quote.ask)));
 return <main className={styles.page} aria-busy={!ready}>
  <header className={styles.heading}><div><p>账户 / 独立研究模拟</p><h1>双向模拟与保护退出</h1><span>真实公共报价 · 独立资金 · 实盘关闭</span></div><Link href="/research/execution">查看验证记录 →</Link></header>
  <section className={styles.card}><h2>运行边界</h2><p>全额抵押的合成多空模拟；加密使用 Bitget 现货 bid/ask，黄金使用 Swissquote XAU/USD。这里不是永续或经纪商账户，未模拟资金费、隔夜费和保证金强平。</p><p>高影响消息窗口禁止开仓，缺失、过期或覆盖不足也禁止开仓；已有持仓的止盈止损仍由服务器执行。采样中断期间无法执行，恢复后按实际报价处理跳空。</p><div className={styles.tags}><span>{status?directionalReason(status.news.state):'正在检查服务…'}</span><Link href="/research/ai">查看 AI 配置与自动模拟</Link><span>后台独立运行</span></div></section>
  {readError?<p className={styles.error} role="alert">{readError}</p>:null}{error?<p className={styles.error} role="alert">{error}</p>:null}{notice?<p className={styles.notice} role="status">{notice}</p>:null}
  {pending&&!busy?<div className={styles.card}><p>上次提交尚未核对。请先刷新账本；如需再次提交，沿用同一请求编号。</p><div className={styles.actions}><button onClick={()=>void refreshNow()}>刷新并核对</button><button onClick={()=>{if(pending)void submit(pending.path,pending.body);}}>重查同一请求</button></div></div>:null}
  <section className={styles.card}><div className={styles.cardHeading}><h2>我的独立账户</h2><button disabled={!ready||busy} onClick={()=>void refreshNow()}>刷新账户</button></div>
   {accounts.length?<WorkspaceSelect label="选择双向模拟账户" disabled={!ready||busy||Boolean(stopTarget)} value={view?.account_id??''} onValueChange={choose} options={[{value:'',label:'请选择账户',disabled:true},...accounts.map(row=>({value:row.account_id,label:`${row.config.instrument} · ${row.metadata.strategy==='MANUAL'?'手动':row.metadata.strategy==='AI_RESEARCH'?'AI 研究':'SMA20 报价'} · ${row.account_id.slice(-8)}${row.status==='STOPPED'?' · 已停新风险':''}`}))]}/>:<p>{loaded?'此浏览器尚无独立双向账户。':'正在读取账户…'}</p>}
   <p>账户通过此浏览器的 HttpOnly 会话隔离；各标签页独立保存选择。清除浏览器会话后无法访问原账户，请保留账本。权限由服务端校验。</p>
  </section>
  {view?<section className={styles.card} data-testid="directional-account" data-account-id={view.account_id} data-revision={view.revision}>
   <div className={styles.cardHeading}><h2>{view.config.instrument}</h2><span className={styles.badge}>{view.status==='STOPPED'?'已停新风险':view.entry_halted?'已拦截新风险':'模拟账户'}</span></div>
   <p className={styles.reason}>{directionalReason(view.entry_gate)}</p><div className={styles.metrics}><div><small>标记权益</small><strong>{money(view.equity)}</strong></div><div><small>余额</small><strong>{money(view.balance)}</strong></div><div><small>累计费用</small><strong>{money(view.fees)}</strong></div></div>
   {view.last_outcome?<p role="status">最近回执：{directionalReason(view.last_outcome.status)} · {directionalReason(view.last_outcome.reason)} · {view.last_outcome.at}</p>:null}
   <div className={styles.quote}>{view.last_quote?<><span>买价 Bid {view.last_quote.bid}</span><span>卖价 Ask {view.last_quote.ask}</span><small>报价 UTC {view.last_quote.at}</small></>:<span>等待首条有效报价</span>}</div>
   {view.metadata.strategy==='SMA20_QUOTES'?<p>自动基线：最近 20 条独立报价中间价的 SMA；当前 {Math.min(view.recent_quotes.length,20)}/20 条。它不是闭合 K 线策略，也不是 AI 或视频策略。数量 {view.metadata.quantity}；止损 {Number(view.metadata.stop_loss_percent)*100}%，止盈 {Number(view.metadata.take_profit_percent)*100}%。手动平仓后策略可能再次入场；要持续退出，请先停止新风险，再提交平仓。</p>:null}
   {view.metadata.strategy==='AI_RESEARCH'?<p>AI 研究账户创建后请到<Link href={`/research/ai?account=${view.account_id}`}>AI 研究启用或暂停自动模拟</Link>。未配置模型时只等待，不会用规则代替 AI；暂停保留持仓保护，手动仅可平仓。</p>:null}
   {view.position?<div className={styles.position}><h3>{view.position.direction==='LONG'?'多头':'空头'} · {view.position.quantity}</h3><dl><div><dt>入场价</dt><dd>{view.position.entry}</dd></div><div><dt>止损</dt><dd>{view.position.stop_loss}</dd></div><div><dt>止盈</dt><dd>{view.position.take_profit}</dd></div><div><dt>未实现盈亏</dt><dd>{money(view.unrealized_pnl)}</dd></div></dl><button disabled={busy||Boolean(view.pending)} onClick={()=>decide(true)}>提交仅平仓决策</button></div>:<p>当前无持仓。</p>}
   {view.pending?<p role="status">{view.pending.target} 决策已接收，等待严格晚于接收时间的后续报价。</p>:null}
   {view.metadata.strategy==='MANUAL'?<form onSubmit={event=>{event.preventDefault();if(canEnter&&validBracket)decide();}}><h3>结构化模拟决策</h3><fieldset disabled={!ready||busy||!canEnter}><div className={styles.fields}><label>方向<WorkspaceSelect label="模拟决策方向" value={target} disabled={busy||!canEnter} onValueChange={value=>setTarget(value as 'LONG'|'SHORT')} options={[{value:'LONG',label:'做多 LONG'},{value:'SHORT',label:'做空 SHORT'}]}/></label><label>数量 · {view.config.instrument==='XAU/USD'?'盎司':'基础币'}<input required type="number" min={view.config.quantity_step} max={view.config.max_quantity} step={view.config.quantity_step} value={quantity} onChange={event=>setQuantity(event.target.value)}/></label><label>绝对止损价<input required type="number" min="0.00000001" step="any" value={stopLoss} onChange={event=>setStopLoss(event.target.value)}/></label><label>绝对止盈价<input required type="number" min="0.00000001" step="any" value={takeProfit} onChange={event=>setTakeProfit(event.target.value)}/></label></div></fieldset><button type="submit" disabled={!ready||busy||!canEnter||!validBracket}>提交模拟决策</button><p>{canEnter?'保护价需位于当前可平仓报价两侧，成交时服务器会再次校验。':'当前不能新开仓，以上展示账户门禁原因；刷新不会绕过消息检查。'}</p></form>:null}
   <details><summary>成交记录 · 共 {view.fills_count} 笔 · 展示最近 100 笔</summary>{view.fills.length?view.fills.slice(-100).reverse().map((fill,index)=><article className={styles.fill} key={`${fill.quote_at}:${index}`}><strong>{fill.action==='OPEN'?'入场':'退出'} · {fill.direction==='LONG'?'多头':'空头'}</strong><span>{directionalReason(fill.reason)}</span><p>{fill.quantity} × {fill.price} · 手续费 {fill.fee}</p><small>{fill.quote_at} UTC</small></article>):<p>暂无模拟成交。被消息门禁拒绝的请求不会伪造成交。</p>}</details>
   <details><summary>冻结配置与账本</summary><p className={styles.digest}>账户 {view.account_id}<br/>版本 {view.revision} · 账本摘要 {view.journal_sha256??'暂无事件'}</p><p>单笔金额上限 {view.config.max_order_quote}；费率 {view.config.fee_rate}；单边不利滑点 {view.config.slippage}；价格步长 {view.config.tick_size}；数量步长 {view.config.quantity_step}。</p><p>消息前后禁入窗口 {view.config.news_pre_seconds}/{view.config.news_post_seconds} 秒。所有成本参数为此研究账户的明确假设。</p><p>下载包含当前状态与最多 1000 笔近期成交；服务器保留完整不可变操作记录。此研究账户在 10,000 次记账操作后停止新风险，已有持仓仍受保护，普通行情采样不消耗此预算。</p><button onClick={download}>下载当前确认快照</button></details>
   <div className={styles.actions}><button disabled={busy||view.status==='STOPPED'} onClick={()=>{setError('');setStopTarget(view);}}>停止此账户新风险</button></div>
  </section>:null}
  <section className={styles.card}><h2>创建独立双向账户</h2><p>独立虚拟资金，不承接或修改旧账户。配置创建后冻结。</p><form onSubmit={create}><fieldset disabled={!ready||!loaded||busy||Boolean(pending)}><div className={styles.fields}>
   <label>产品<WorkspaceSelect label="双向模拟产品" value={fields.instrument} disabled={!ready||!loaded||busy} onValueChange={value=>set('instrument',value)} options={status?.instruments.map(row=>({value:row.instrument,label:row.instrument}))??[]}/></label>
   <label>决策方式<WorkspaceSelect label="双向模拟决策方式" value={fields.strategy} disabled={!ready||!loaded||busy} onValueChange={value=>set('strategy',value)} options={[{value:'MANUAL',label:'手动结构化决策'},{value:'SMA20_QUOTES',label:'自动 SMA20 报价基线'},{value:'AI_RESEARCH',label:'AI 研究自动模拟'}]}/></label>
   {input('initial_cash','初始资金 · 对价币','1','1000000')}{input('max_order_quote','单笔对价金额上限','0.01','1000000')}{input('max_quantity','最大数量 · 黄金上限100','0.00000001',fields.instrument==='XAU/USD'?'100':'1000')}{input('tick_size','研究价格步长','0.00000001','1')}{input('quantity_step','研究数量步长','0.00000001','1')}{input('fee_rate','单边费率 · 0.001 = 0.1%','0','0.1')}{input('slippage','单边不利滑点 · 绝对价格','0','1000')}{input('news_pre_seconds','消息前禁入秒数','0','86400')}{input('news_post_seconds','消息后禁入秒数','0','86400')}
   {fields.strategy==='SMA20_QUOTES'?<>{input('quantity','自动每次入场数量','0.00000001','1000')}{input('stop_loss_percent','自动止损比例 · 0.01 = 1%','0.0001','0.5')}{input('take_profit_percent','自动止盈比例 · 0.02 = 2%','0.0001','0.9999')}</>:null}
  </div></fieldset><button type="submit" disabled={!ready||!loaded||busy||Boolean(pending)}>{busy?'正在核对…':'创建独立模拟账户'}</button></form></section>
  <p className={styles.footnote}>本页不会连接私有交易 API。AI 模型配置和自动模拟控制请到<Link href="/research/ai">AI 研究</Link>查看；模型建议须经服务器校验，不保证收益。<Link href="/paper/realtime">查看旧版现货账户</Link></p>
  {stopTarget?<StopDialog target={stopTarget} busy={busy} error={error} cancel={()=>{setStopTarget(null);setError('');}} confirm={()=>void submit(`accounts/${stopTarget.account_id}/stop`,{request_id:crypto.randomUUID(),expected_revision:stopTarget.revision})}/>:null}
 </main>;
}
