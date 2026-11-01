'use client';
import Link from 'next/link';
import {useEffect,useRef,useState,type FormEvent} from 'react';
import {z} from 'zod';
import WorkspaceSelect from '../../../components/workspace-select';
import {OperatorStatus,OperatorConfiguration,OperatorAccounts,OperatorError,operatorRequest,draftFromConfig,configurationPayload,type OperatorSession,type OperatorConfig,type ConfigurationDraft} from '../../../lib/operator';
import ApiGuide from './api-guide';
import styles from './page.module.css';

type Frontend={hosted:boolean;apiConfigured:boolean;directionalConfigured:boolean;goldConfigured:boolean};
type SessionState={state:'checking'}|{state:'failed'}|{state:'ready';value:OperatorSession};
type Account=z.infer<typeof OperatorAccounts>['accounts'][number];
const errorMessage=(error:unknown)=>error instanceof z.ZodError?'填写内容或服务器响应未通过校验，请检查字段后重试。':error instanceof Error?error.message:'请求未完成，请刷新核对。';

export default function ApiConnections({frontend}:{frontend:Frontend}){
 const [session,setSession]=useState<SessionState>({state:'checking'}),[config,setConfig]=useState<OperatorConfig|null>(null),[draft,setDraft]=useState<ConfigurationDraft|null>(null);
 const [ready,setReady]=useState(false),[busy,setBusy]=useState(false),[error,setError]=useState(''),[notice,setNotice]=useState('');
 const [username,setUsername]=useState(''),[password,setPassword]=useState(''),[confirmation,setConfirmation]=useState(''),[setupToken,setSetupToken]=useState('');
 const [passwordOpen,setPasswordOpen]=useState(false),[currentPassword,setCurrentPassword]=useState(''),[newPassword,setNewPassword]=useState(''),[newConfirmation,setNewConfirmation]=useState('');
 const [accounts,setAccounts]=useState<Account[]>([]),[accountsError,setAccountsError]=useState('');
 const locked=useRef(false),mounted=useRef(false),invitation=useRef(''),status=session.state==='ready'?session.value:null;
 function clearSecrets(clearInvitation=true){if(clearInvitation)invitation.current='';setPassword('');setConfirmation('');setSetupToken('');setCurrentPassword('');setNewPassword('');setNewConfirmation('');setDraft(previous=>previous?{...previous,api_key:'',news_token:'',gold_token:''}:null);}
 function adopt(next:OperatorConfig){setConfig(next);setDraft(draftFromConfig(next));}
 function loseSession(){clearSecrets();setConfig(null);setDraft(null);setAccounts([]);setPasswordOpen(false);setSession(previous=>previous.state==='ready'?{state:'ready',value:{...previous.value,authenticated:false,username:null,csrf_token:null}}:{state:'failed'});}
 async function readAccounts(signal?:AbortSignal){
  try{const data=await operatorRequest('accounts',OperatorAccounts,undefined,undefined,signal);if(!signal?.aborted){setAccounts(data.accounts);setAccountsError('');}}
  catch{if(!signal?.aborted){setAccounts([]);setAccountsError('账户列表未读取成功；不自动改变已有授权，请刷新重试。');}}
 }
 async function read(signal?:AbortSignal){
  const next=await operatorRequest('status',OperatorStatus,undefined,undefined,signal);if(signal?.aborted)return;setSession({state:'ready',value:next});
  if(next.authenticated){const value=await operatorRequest('configuration',OperatorConfiguration,undefined,undefined,signal);if(!signal?.aborted)adopt(value);await readAccounts(signal);}
  else{setConfig(null);setDraft(null);setAccounts([]);}
 }
 useEffect(()=>{
  mounted.current=true;
  const token=new URLSearchParams(window.location.hash.slice(1)).get('setup');
  if(token){window.history.replaceState(null,'',window.location.pathname+window.location.search);if(/^[A-Za-z0-9_-]{43}$/.test(token))invitation.current=token;}
  if(invitation.current)setSetupToken(invitation.current);
  const controller=new AbortController();setReady(true);
  void read(controller.signal).catch(error=>{if(!controller.signal.aborted){setSession({state:'failed'});setError(errorMessage(error));}});
  const hide=()=>clearSecrets();window.addEventListener('pagehide',hide);
  return()=>{mounted.current=false;clearSecrets(false);controller.abort();window.removeEventListener('pagehide',hide);queueMicrotask(()=>{if(!mounted.current)invitation.current='';});};
 // Synchronize the initial session and erase the private bootstrap fragment.
 // eslint-disable-next-line react-hooks/exhaustive-deps
 },[]);
 useEffect(()=>{
  if(!status?.authenticated)return;
  const controller=new AbortController();
  async function verify(){
   try{const next=await operatorRequest('status',OperatorStatus,undefined,undefined,controller.signal);if(controller.signal.aborted)return;if(!next.authenticated){loseSession();setNotice('管理员会话已过期，请重新登录。');}}
   catch{/* Preserve the current form on a temporary connection failure; writes still require server authentication. */}
  }
  const timer=setInterval(()=>void verify(),60000),focus=()=>{if(document.visibilityState==='visible')void verify();};
  window.addEventListener('focus',focus);document.addEventListener('visibilitychange',focus);
  return()=>{controller.abort();clearInterval(timer);window.removeEventListener('focus',focus);document.removeEventListener('visibilitychange',focus);};
 // Check expiry when returning to the page without persisting credentials in the browser.
 // eslint-disable-next-line react-hooks/exhaustive-deps
 },[status?.authenticated]);
 async function task(action:()=>Promise<void>){
  if(locked.current)return;locked.current=true;setBusy(true);setError('');setNotice('');
  try{await action();}catch(error){if(error instanceof OperatorError&&error.status===401)loseSession();setError(errorMessage(error));}
  finally{locked.current=false;setBusy(false);}
 }
 async function authenticate(event:FormEvent){
  event.preventDefault();if(!status)return;
  if(!status.initialized&&(password.length<12||password!==confirmation)){setError('设置密码至少 12 个字符，两次输入需一致。');return;}
  await task(async()=>{try{
   const next=await operatorRequest(status.initialized?'login':'setup',OperatorStatus,status.initialized?{username:username.trim(),password}:{setup_token:setupToken,username:username.trim(),password});
   setSession({state:'ready',value:next});clearSecrets();if(next.authenticated){adopt(await operatorRequest('configuration',OperatorConfiguration));await readAccounts();}
   setNotice('管理员登录成功。密钥只保存到服务器，页面不会回显。');
  }finally{setPassword('');setConfirmation('');}});
 }
 function change<K extends keyof ConfigurationDraft>(key:K,value:ConfigurationDraft[K]){
  setDraft(previous=>{if(!previous)return null;const next={...previous,[key]:value};if(previous[key]!==value){if(key==='provider'||key==='base_url')next.api_key='';if(key==='news_url')next.news_token='';if(key==='gold_url')next.gold_token='';}return next;});setNotice('');
 }
 async function save(event:FormEvent){
  event.preventDefault();if(!draft||!config||!status?.authenticated)return;
  let body:ReturnType<typeof configurationPayload>;try{body=configurationPayload(draft,config.revision,config);}catch(error){setError(errorMessage(error));return;}
  await task(async()=>{const next=await operatorRequest('configuration',OperatorConfiguration,body,status.csrf_token);adopt(next);setSession({state:'ready',value:{...status,config_revision:next.revision}});setNotice('配置已保存到服务器，密钥输入已清空。自动模型已暂停；请重新检查，再到 AI 研究页明确启用模拟。原有止盈止损继续运行。');});
 }
 async function logout(){await task(async()=>{try{const next=await operatorRequest('logout',OperatorStatus,{},status?.csrf_token);setSession({state:'ready',value:next});}finally{clearSecrets();setConfig(null);setDraft(null);setAccounts([]);setPasswordOpen(false);}setNotice('已退出管理员登录。');});}
 async function changePassword(event:FormEvent){
  event.preventDefault();if(newPassword.length<12||newPassword!==newConfirmation){setError('新密码至少 12 个字符，两次输入需一致。');return;}
  await task(async()=>{const next=await operatorRequest('password',OperatorStatus,{current_password:currentPassword,new_password:newPassword},status?.csrf_token);clearSecrets();setPasswordOpen(false);setSession({state:'ready',value:next});if(!next.authenticated){setConfig(null);setDraft(null);setAccounts([]);}setNotice(next.authenticated?'密码已更新，其他管理员会话已撤销。':'密码已更新，旧会话已撤销，请重新登录。');});
 }
 function toggleAccount(id:string,checked:boolean){if(!draft)return;const selected=draft.accounts.split(/[\s,]+/).filter(Boolean);change('accounts',(checked?Array.from(new Set([...selected,id])):selected.filter(value=>value!==id)).join('\n'));}

 return <main className={styles.page} aria-busy={busy||session.state==='checking'}>
  <header className={styles.heading}><div><p className={styles.eyebrow}>系统 / 安全接入</p><h1>API 接入</h1><p>手机配置，服务器保存。先验证模型与数据，再开启自动模拟。</p></div><span className={styles.badge}>实盘关闭</span></header>
  <nav className={styles.navigation} aria-label="API接入导航"><Link href="/system">连接状态</Link><Link href="/research/ai">AI 研究与自动模拟</Link><Link href="/paper/directional">模拟账户</Link></nav>
  {error?<p className={styles.alert} role="alert">{error}</p>:null}{notice?<p className={styles.success} role="status">{notice}</p>:null}
  {session.state==='checking'?<section className={styles.card}><h2>正在检查管理员会话</h2><p>未确认登录前不读取或显示配置。</p></section>:session.state==='failed'?<section className={styles.card}><h2>管理员连接未确认</h2><p>原有服务器配置没有被修改。</p><button className={styles.button} disabled={!ready||busy} onClick={()=>void task(()=>read())}>重新检查管理员会话</button></section>:!status?.authenticated?<section className={`${styles.card} ${styles.loginCard}`}>
   <div className={styles.cardHeading}><span className={styles.number}>◈</span><div><p className={styles.eyebrow}>管理员专用</p><h2>{status?.initialized?'登录管理后台':'首次设置管理员'}</h2></div></div>
   <p>{status?.initialized?'登录后可配置模型、新闻与黄金历史接口。':'首次设置需要运营者私有凭证，公开访问不能注册管理员。请从本次交付的私有设置文件获取凭证。'}</p>
   <form onSubmit={event=>void authenticate(event)} className={styles.form}><fieldset disabled={!ready||busy}>
    {!status?.initialized?<label>首次设置凭证<input type="password" name="setup-token" autoComplete="off" value={setupToken} onChange={event=>setSetupToken(event.target.value)} required maxLength={128} spellCheck={false}/></label>:null}
    <label>管理员用户名<input name="username" autoComplete="username" value={username} onChange={event=>setUsername(event.target.value)} required minLength={3} maxLength={32} pattern={'[A-Za-z0-9][A-Za-z0-9_.\\-]{2,31}'} title="3–32 个字母、数字或 ._-，首位为字母或数字" autoCapitalize="none" spellCheck={false}/></label>
    <label>{status?.initialized?'登录密码':'设置密码 · 至少 12 个字符'}<input name="password" type="password" autoComplete={status?.initialized?'current-password':'new-password'} value={password} onChange={event=>setPassword(event.target.value)} required minLength={status?.initialized?1:12} maxLength={128}/></label>
    {!status?.initialized?<label>再次输入密码<input name="password-confirmation" type="password" autoComplete="new-password" value={confirmation} onChange={event=>setConfirmation(event.target.value)} required minLength={12} maxLength={128}/></label>:null}
   </fieldset><button className={styles.button} type="submit" disabled={!ready||busy}>{busy?'正在验证…':status?.initialized?'登录管理员':'完成首次设置'}</button></form>
  </section>:<>
   <section className={styles.sessionBar}><div><strong>已登录 · {status.username}</strong><p>密钥不回显，浏览器不保存 API Key。</p></div><div className={styles.actions}><button type="button" className={styles.secondary} disabled={busy} onClick={()=>{setPasswordOpen(value=>!value);setCurrentPassword('');setNewPassword('');setNewConfirmation('');}}>更改密码</button><button type="button" className={styles.secondary} disabled={busy} onClick={()=>void logout()}>退出登录</button></div></section>
   {passwordOpen?<section className={styles.card} aria-labelledby="change-password-heading"><h2 id="change-password-heading">更改管理员密码</h2><form className={styles.form} onSubmit={event=>void changePassword(event)}><fieldset disabled={busy}><label>当前密码<input type="password" autoComplete="current-password" required value={currentPassword} maxLength={128} onChange={event=>setCurrentPassword(event.target.value)}/></label><label>新密码<input type="password" autoComplete="new-password" required minLength={12} maxLength={128} value={newPassword} onChange={event=>setNewPassword(event.target.value)}/></label><label>再次输入新密码<input type="password" autoComplete="new-password" required minLength={12} maxLength={128} value={newConfirmation} onChange={event=>setNewConfirmation(event.target.value)}/></label></fieldset><p>更新后撤销旧管理员会话；若当前会话失效，请重新登录。</p><button className={styles.button} disabled={busy}>更新密码</button></form></section>:null}
   {!config||!draft?<section className={styles.card}><h2>正在读取受保护配置</h2><p>尚未收到服务器配置，不能保存。</p><button type="button" className={styles.secondary} disabled={busy} onClick={()=>void task(()=>read())}>重新读取配置</button></section>:<form onSubmit={event=>void save(event)} className={styles.configForm}>
    <fieldset disabled={busy} className={styles.grid}>
     <section className={styles.card} aria-labelledby="model-heading"><div className={styles.cardHeading}><span className={styles.number}>01</span><div><p className={styles.eyebrow}>模型决策</p><h2 id="model-heading">AI 模型 API</h2></div></div>
      <label>服务商<WorkspaceSelect label="模型服务商" value={draft.provider} disabled={busy} onValueChange={value=>{change('provider',value);if(value==='google'){change('base_url','');setNotice('Google 仅使用官方接口，已清空 Base URL。更换服务商需重填密钥或勾选清除旧密钥。');}}} options={[{value:'',label:'尚未选择'},{value:'openai',label:'OpenAI / OpenAI 兼容接口'},{value:'anthropic',label:'Anthropic'},{value:'google',label:'Google'}]}/></label>
      <label>真实模型名<input autoComplete="off" value={draft.model} maxLength={200} placeholder="按服务商提供的模型 ID 填写" onChange={event=>change('model',event.target.value)}/></label>
      {draft.provider!=='google'?<label>自定义 Base URL · 可选<input inputMode="url" autoComplete="off" autoCapitalize="none" spellCheck={false} value={draft.base_url} maxLength={2048} placeholder="https://服务商地址/v1" onChange={event=>change('base_url',event.target.value)}/></label>:<p>Google 使用官方默认接口。自定义兼容网关请选择 OpenAI。</p>}
      <label>API Key<input type="password" autoComplete="off" value={draft.api_key} maxLength={4096} placeholder={config.model.api_key_configured?'服务器已保存，留空保留':'尚未配置，填写后仅存服务器'} disabled={draft.clear_api_key||busy} onChange={event=>change('api_key',event.target.value)}/></label>
      <label className={styles.check}><input type="checkbox" checked={draft.clear_api_key} onChange={event=>{change('clear_api_key',event.target.checked);change('api_key','');}}/>清除服务器保存的模型密钥</label><p className={styles.status}>{config.model.api_key_configured?'模型密钥已保存 · 真实调用仍需验证':'服务器未保存模型密钥'}</p><p>更换服务商或 Base URL 时需重新填写密钥，或明确清除原密钥，不能把旧凭据发给新接口。</p>
     </section>
     <section className={styles.card} aria-labelledby="news-heading"><div className={styles.cardHeading}><span className={styles.number}>02</span><div><p className={styles.eyebrow}>事件过滤</p><h2 id="news-heading">新闻日历 API</h2></div></div>
      <label>新闻来源名称<input autoComplete="off" value={draft.news_source} maxLength={120} onChange={event=>change('news_source',event.target.value)}/></label><label>新闻 HTTPS 接口<input inputMode="url" autoComplete="off" autoCapitalize="none" spellCheck={false} value={draft.news_url} maxLength={2048} onChange={event=>change('news_url',event.target.value)}/></label><label>新闻 Bearer Token · 可选<input type="password" autoComplete="off" value={draft.news_token} maxLength={4096} placeholder={config.news.token_configured?'已保存，留空保留':'接口不需要认证可留空'} disabled={draft.clear_news_token||busy} onChange={event=>change('news_token',event.target.value)}/></label><label className={styles.check}><input type="checkbox" checked={draft.clear_news_token} onChange={event=>{change('clear_news_token',event.target.checked);change('news_token','');}}/>清除新闻接口 Token</label><p>更换接口需重填或清除 Token。高影响事件窗口、缺失、过期或覆盖不足时禁止开仓与加仓，已有止盈止损继续。</p>
     </section>
     <section className={styles.card} aria-labelledby="gold-heading"><div className={styles.cardHeading}><span className={styles.number}>03</span><div><p className={styles.eyebrow}>原市场历史</p><h2 id="gold-heading">黄金历史行情 API</h2></div></div>
      <label>黄金来源名称<input autoComplete="off" value={draft.gold_source} maxLength={120} onChange={event=>change('gold_source',event.target.value)}/></label><label>黄金 HTTPS 接口<input inputMode="url" autoComplete="off" autoCapitalize="none" spellCheck={false} value={draft.gold_url} maxLength={2048} onChange={event=>change('gold_url',event.target.value)}/></label><label>黄金 Bearer Token · 可选<input type="password" autoComplete="off" value={draft.gold_token} maxLength={4096} placeholder={config.gold.token_configured?'已保存，留空保留':'接口不需要认证可留空'} disabled={draft.clear_gold_token||busy} onChange={event=>change('gold_token',event.target.value)}/></label><label className={styles.check}><input type="checkbox" checked={draft.clear_gold_token} onChange={event=>{change('clear_gold_token',event.target.checked);change('gold_token','');}}/>清除黄金接口 Token</label><p>更换接口需重填或清除 Token。接入独立 XAU/USD 现货闭合历史，不用期货或当前报价替代。Bitget 公共现货行情无需密钥。</p>
     </section>
     <section className={styles.card} aria-labelledby="accounts-heading"><div className={styles.cardHeading}><span className={styles.number}>04</span><div><p className={styles.eyebrow}>模拟运行授权</p><h2 id="accounts-heading">AI 模拟账户</h2></div></div>
      {accountsError?<p role="alert" className={styles.error}>{accountsError}</p>:accounts.length===0?<p>当前管理员还没有可授权的 AI 研究账户。</p>:<div className={styles.accountChoices}>{accounts.map(account=><label className={styles.accountChoice} key={account.account_id}><input type="checkbox" checked={draft.accounts.split(/[\s,]+/).includes(account.account_id)} disabled={!draft.accounts.split(/[\s,]+/).includes(account.account_id)&&(account.strategy!=='AI_RESEARCH'||account.status==='STOPPED')} onChange={event=>toggleAccount(account.account_id,event.target.checked)}/><span><strong>{account.instrument}</strong><small>{account.account_id}</small><small>{account.status==='STOPPED'?'已停止新风险':account.strategy==='AI_RESEARCH'?'AI 研究账户':'非 AI 账户，不能授权'}</small></span></label>)}</div>}
      <details className={styles.details}><summary>手动输入账户 UUID</summary><label>每行一个 UUID，最多 20 个<textarea value={draft.accounts} rows={6} autoComplete="off" autoCapitalize="none" spellCheck={false} onChange={event=>change('accounts',event.target.value)}/></label></details><p>请先保存新闻来源，再创建并授权 AI 研究账户。账户的新闻来源在创建时冻结；更换来源后需新建账户，旧账本不变。</p><p>只授权当前管理员的 AI 账户。登录后新建账户可跨设备访问；旧浏览器账户仍保留，退出管理员后可回到原浏览器账户。授权不等于启动；保存后在 AI 研究页单独启用自动模拟。</p><Link href="/paper/directional" className={styles.inlineLink}>创建或查看 AI 模拟账户 →</Link>
     </section>
    </fieldset>
    <section className={styles.saveBar}><div><strong>保存配置 · 当前版本 {config.revision}</strong><p>保存会暂停自动模型，保留持仓保护。密钥留空保留，勾选“清除”才会删除。保存不代表供应商验证通过，也不会启用实盘。</p></div><div className={styles.actions}><button type="button" className={styles.secondary} disabled={busy} onClick={()=>void task(async()=>{clearSecrets();await read();setNotice('已重新读取服务器配置，未保存的输入已丢弃。');})}>放弃输入并刷新</button><button className={styles.button} disabled={busy} type="submit">{busy?'正在核对并保存…':'保存到服务器'}</button></div></section>
   </form>}
  </>}
  <ApiGuide frontend={frontend} ready={ready}/>
 </main>;
}
