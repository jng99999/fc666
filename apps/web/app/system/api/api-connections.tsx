'use client';

import Link from 'next/link';
import {useEffect,useState} from 'react';
import type {z} from 'zod';
import {DirectionalStatus,directionalRequest} from '../../../lib/directional';
import {ResearchStatus,automationReason} from '../../../lib/tradingagents';
import styles from './page.module.css';

type Check<T>={state:'checking'}|{state:'ready';data:T;checkedAt:string}|{state:'error'};
type Frontend={hosted:boolean;apiConfigured:boolean;directionalConfigured:boolean;goldConfigured:boolean};

const modelTemplate=`# 合并到服务器现有 research-worker.env，不覆盖原有 RUNNER_TOKEN。
# 按供应商实际资料填写，空字段不会启用模型。
FC666_TA_PROVIDER=
FC666_TA_MODEL=
FC666_TA_API_KEY=
# 仅在服务商需要自定义接口时配置：
# FC666_TA_BASE_URL=https://供应商接口地址
LIVE_TRADING=false`;
const accountTemplate=`# 合并到服务器现有 directional-ai.env。
# 把空数组改为允许使用模型的真实 AI 账户 UUID 数组，最多 20 个。
FC666_TA_ACCOUNT_IDS=[]
LIVE_TRADING=false`;
const newsTemplate=`# 合并到服务器现有 directional-ai.env。
# URL 必须返回下方所示 FC666 新闻日历契约。
DIRECTIONAL_NEWS_SOURCE=
DIRECTIONAL_NEWS_URL=
# 若服务需要 Bearer 认证，再配置：
# DIRECTIONAL_NEWS_TOKEN=
LIVE_TRADING=false`;
const goldTemplate=`# 合并到服务器现有 directional-ai.env。
# URL 必须返回独立 XAU/USD 现货历史契约。
FC666_GOLD_HISTORY_SOURCE=
FC666_GOLD_HISTORY_URL=
# 若服务需要 Bearer 认证，再配置：
# FC666_GOLD_HISTORY_TOKEN=
LIVE_TRADING=false`;
const frontendTemplate=`# Vercel 项目 Settings → Environment Variables
# 以下只填写已经部署的后端 HTTPS 地址，不填写模型或交易所密钥。
API_BASE_URL=
DIRECTIONAL_API_BASE_URL=
GOLD_API_BASE_URL=
LIVE_TRADING=false
# 公共实时行情连接按需配置，地址不得含密钥：
# NEXT_PUBLIC_MARKET_WS_URL=wss://后端地址/ws/v1/market`;

function Template({label,value,ready}:{label:string;value:string;ready:boolean}){
 const [copy,setCopy]=useState<'idle'|'copying'|'copied'|'error'>('idle');
 async function copyTemplate(){
  if(copy==='copying')return;setCopy('copying');
  try{await navigator.clipboard.writeText(value);setCopy('copied');}
  catch{setCopy('error');}
 }
 return <details className={styles.details}>
  <summary>{label}</summary>
  <pre className={styles.code}>{value}</pre>
  <button className={styles.secondary} type="button" disabled={!ready||copy==='copying'} onClick={()=>void copyTemplate()} aria-label={`复制${label}`}>{copy==='copying'?'正在复制…':'复制配置模板'}</button>
  {copy==='copied'?<p role="status">已复制模板；模板不含密钥，仍需在服务器填写并生效。</p>:null}
  {copy==='error'?<p role="alert">浏览器未允许复制。请长按上方模板手动复制。</p>:null}
 </details>;
}

export default function ApiConnections({frontend}:{frontend:Frontend}){
 const [ready,setReady]=useState(false),[revision,setRevision]=useState(0);
 const [model,setModel]=useState<Check<z.infer<typeof ResearchStatus>>>({state:'checking'});
 const [news,setNews]=useState<Check<z.infer<typeof DirectionalStatus>>>({state:'checking'});
 useEffect(()=>{
  setReady(true);const controller=new AbortController();
  async function check<T>(path:string,schema:z.ZodType<T>,update:(value:Check<T>)=>void){
   try{const data=await directionalRequest(path,schema,undefined,controller.signal);if(!controller.signal.aborted)update({state:'ready',data,checkedAt:new Date().toISOString()});}
   catch{if(!controller.signal.aborted)update({state:'error'});}
  }
  void check('research/status',ResearchStatus,setModel);
  void check('status',DirectionalStatus,setNews);
  return()=>controller.abort();
 },[revision]);
 const checking=model.state==='checking'||news.state==='checking';
 function refresh(){if(checking)return;setModel({state:'checking'});setNews({state:'checking'});setRevision(value=>value+1);}
 const modelText=model.state==='checking'?'正在读取模型配置状态…':model.state==='error'?'暂时无法读取模型状态':model.data.model_configured?'模型字段已配置，真实调用待验证':model.data.runner_configured?'研究服务已连接，等待配置模型':'研究服务尚未连接';
 const newsText=news.state==='checking'?'正在读取新闻配置状态…':news.state==='error'?'暂时无法读取新闻状态':news.data.news.configured?'新闻接口已配置，事件时效与覆盖按账户校验':'新闻日历未接入，禁止新开仓';

 return <main className={styles.page}>
  <header className={styles.heading}><div><p className={styles.eyebrow}>设置 / 接入与连接</p><h1>API 接入</h1><p>先连接 AI 与数据，再用独立模拟账户验证完整执行链路。</p></div><span className={styles.badge}>实盘关闭</span></header>
  <nav className={styles.navigation} aria-label="API接入导航"><Link href="/system">连接状态</Link><Link href="/research/ai">AI 研究与自动模拟</Link><Link href="/paper/directional">模拟账户</Link></nav>
  <section className={styles.intro} aria-labelledby="api-secret-location"><h2 id="api-secret-location">密钥填在哪里？</h2><p><strong>AI 模型密钥填在后端服务器的 <code>research-worker.env</code>，不是 Vercel。</strong>下方可查看每类接口的配置位置、复制模板和检查当前状态。</p><p>当前测试站未设置管理员登录，因此这里不提供公开的密钥保存表单。密钥只存服务器，不发到聊天、不写入网页或 Git；浏览器不能承担密钥保密和执行授权。</p><p>Bitget 公共行情不需要 API Key。本次不接入交易所私有交易密钥，实盘保持关闭。</p></section>
  <div className={styles.checkbar}><p>检查只读取当前配置状态，不调用收费模型、不下单。</p><button className={styles.button} type="button" disabled={!ready||checking} onClick={refresh}>{checking?'正在检查…':'重新检查接入状态'}</button></div>
  <div className={styles.grid}>
   <section className={styles.card} aria-labelledby="api-model-heading"><div className={styles.cardHeading}><span className={styles.number}>01</span><div><p className={styles.eyebrow}>AI 决策</p><h2 id="api-model-heading">AI 模型 API</h2></div></div><p className={styles.status} role="status">{modelText}</p>
    {model.state==='ready'?<p className={styles.meta}>{automationReason(model.data.state)} · 本次读取 {model.checkedAt.slice(11,19)} UTC</p>:null}
    {model.state==='error'?<p className={styles.error}>配置状态未确认，请检查双向模拟服务连接后重试；未发送模型请求。</p>:null}
    <p>需要服务商标识、真实模型名和 API Key；自定义兼容接口还需 Base URL。没有预设服务商，不能只填密钥就认为模型已接通。</p>
    <dl className={styles.location}><dt>填写位置 · 后端服务器</dt><dd><code>/opt/fc666-directional-test/config/research-worker.env</code></dd></dl>
    <Template label="AI 模型配置模板" value={modelTemplate} ready={ready}/>
    <p>保留现有 <code>FC666_TA_RUNNER_TOKEN</code>，它必须与账户服务中的值一致；私有 worker 不对公网开放。</p>
    <details className={styles.details}><summary>授权哪个模拟账户使用模型？</summary><p>在双向模拟页创建“AI 研究自动模拟”账户，复制其完整 UUID。在服务器的 <code>directional-ai.env</code> 中加入授权数组，公共创建账户不会自动获得模型开销权限。</p><Template label="AI 账户授权模板" value={accountTemplate} ready={ready}/><Link className={styles.inlineLink} href="/paper/directional">创建或查看模拟账户 →</Link></details>
   </section>
   <section className={styles.card} aria-labelledby="api-news-heading"><div className={styles.cardHeading}><span className={styles.number}>02</span><div><p className={styles.eyebrow}>事件过滤</p><h2 id="api-news-heading">新闻日历 API</h2></div></div><p className={styles.status} role="status">{newsText}</p>
    {news.state==='ready'?<p className={styles.meta}>{news.data.news.configured?'已配置不代表当前事件覆盖通过':'保留已有持仓的止盈止损'} · 本次读取 {news.checkedAt.slice(11,19)} UTC</p>:null}
    {news.state==='error'?<p className={styles.error}>配置状态未确认，不把请求失败视为“没有新闻”。</p>:null}
    <p>高影响事件窗口禁止开仓和加仓，已有持仓仍可止盈止损。源缺失、过期或覆盖不完整时继续禁止新风险。</p>
    <dl className={styles.location}><dt>填写位置 · 后端服务器</dt><dd><code>/opt/fc666-directional-test/config/directional-ai.env</code></dd></dl>
    <Template label="新闻日历配置模板" value={newsTemplate} ready={ready}/>
    <details className={styles.details}><summary>接口需要返回什么？</summary><p>GET 请求携带 <code>instrument</code> 参数；认证 Token 可选。接口必须适配以下字段，不能直接填任意新闻网页。</p><pre className={styles.code}>{`{
  "source": "与配置来源一致",
  "observed_at": "带时区的 ISO 时间",
  "coverage_start": "真实覆盖起点",
  "coverage_end": "真实覆盖终点",
  "events": [{
    "event_id": "稳定的事件 ID",
    "instruments": ["XAU/USD"],
    "impact": "HIGH",
    "scheduled_at": "事件时间",
    "known_at": "事件被获知时间"
  }]
}`}</pre><p>时间必须来自供应商。只有已确认覆盖内没有事件，才可返回空 events；服务故障不能伪造空日历。</p></details>
   </section>
   <section className={styles.card} aria-labelledby="api-gold-heading"><div className={styles.cardHeading}><span className={styles.number}>03</span><div><p className={styles.eyebrow}>黄金分析</p><h2 id="api-gold-heading">黄金历史行情 API</h2></div></div><p className={styles.status}>需通过黄金研究快照核验历史输入</p><p>这里接入 AI 使用的独立 XAU/USD 现货闭合 K 线。当前买卖报价不等于历史数据，AI 密钥也不能补齐黄金历史行情。</p>
    <dl className={styles.location}><dt>填写位置 · 后端服务器</dt><dd><code>/opt/fc666-directional-test/config/directional-ai.env</code></dd></dl>
    <Template label="黄金历史配置模板" value={goldTemplate} ready={ready}/>
    <details className={styles.details}><summary>接口与历史核验要求</summary><p>GET 参数：<code>instrument=XAU/USD</code>、<code>interval=1m</code>、<code>limit=200</code>、<code>end_time</code> 为当前毫秒时间戳。Token 认证可选。</p><pre className={styles.code}>{`{
  "instrument": "XAU/USD",
  "product_type": "SPOT",
  "currency": "USD",
  "candle_source": "与配置来源一致",
  "bars": [{
    "open_time": 1793570400000,
    "open": "4000", "high": "4001",
    "low": "3999", "close": "4000.5",
    "volume": null
  }]
}`}</pre><p>示例仅说明结构，不是真实行情。至少 100 根、最多 200 根连续已闭合 1 分钟柱；缺口、过期和未来柱会被拒绝。无成交量时保留 null，不能编成零。</p><p>当前没有独立的历史配置查询接口。配置后，请在 AI 研究页为黄金账户保存研究快照，核对来源、柱数和 EMA / RSI / ATR；这一步不调用模型。多年历史回测仍使用独立冻结数据集。</p></details>
    <Link className={styles.inlineLink} href="/research/ai">查看研究快照与历史输入 →</Link>
   </section>
   <section className={styles.card} aria-labelledby="api-frontend-heading"><div className={styles.cardHeading}><span className={styles.number}>04</span><div><p className={styles.eyebrow}>站点连接</p><h2 id="api-frontend-heading">Vercel 后端地址</h2></div></div><p>这里只配置站点到现有后端的连接地址。模型、新闻和黄金供应商密钥放在上方服务器文件；填在 Vercel 不会自动传给私有研究 worker。</p>
    <dl className={styles.configList}><div><dt>主数据服务</dt><dd>{frontend.apiConfigured?'已设置地址':'未设置专用地址'}</dd></div><div><dt>独立双向模拟</dt><dd>{frontend.directionalConfigured?'已设置地址':'未设置地址'}</dd></div><div><dt>黄金服务</dt><dd>{frontend.goldConfigured?'已设置地址或沿用主服务':'未设置专用地址'}</dd></div></dl><p className={styles.meta}>这里只确认变量是否存在，不代表后端请求成功。{frontend.hosted?'当前为 Vercel 部署。':'当前不是 Vercel 部署。'}</p>
    <a className={styles.button} href="https://vercel.com/jng56/fc666-test/settings/environment-variables" target="_blank" rel="noreferrer">打开 Vercel 环境配置 ↗</a><p className={styles.meta}>需登录有权管理 fc666-test 的 Vercel 账号。</p>
    <Template label="Vercel 后端连接模板" value={frontendTemplate} ready={ready}/><Link className={styles.inlineLink} href="/system">检查实际站点连接 →</Link>
   </section>
  </div>
  <section className={styles.card} aria-labelledby="api-next-heading"><h2 id="api-next-heading">配置完成后怎么验证？</h2><ol className={styles.steps}><li>把模板字段合并进服务器现有配置，保留原有 Runner Token 和其他配置，不覆盖整份文件。</li><li>只重建需要加载新配置的服务容器，保留数据卷；Vercel 地址调整后重新部署前端。</li><li>回到本页刷新状态，为 AI 模拟账户配置服务器授权，再去研究页保存新鲜快照。</li><li>确认新闻、历史数据与风险校验通过后，运行一次模型研究，核对建议、费用和模拟成交回执，再启用自动模拟。</li></ol><p>状态显示已配置不证明密钥有效或策略盈利。当前页面不会测试收费模型，也不会启用实盘。</p><div className={styles.actions}><Link className={styles.button} href="/research/ai">进入 AI 研究与自动模拟</Link><Link className={styles.secondary} href="/paper/directional">查看模拟账户与账本</Link></div></section>
 </main>;
}
