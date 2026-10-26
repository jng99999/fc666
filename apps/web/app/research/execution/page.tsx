import Link from 'next/link';
import summary from '../../../public/research/execution-validation.json';
import {ExecutionValidation} from '../../../lib/execution-validation';
import styles from './page.module.css';

export const metadata={title:'执行验证与交易员观察 · FC666'};
const number=(value:number)=>value.toLocaleString('zh-CN');
const percent=(value:string)=>(Number(value)*100).toFixed(2)+'%';

export default function ExecutionResearch(){
 const parsed=ExecutionValidation.safeParse(summary);
 if(!parsed.success)return <main className={styles.page}><section className={styles.card}><h1>研究摘要暂不可用</h1><p role="alert">证据文件未通过校验，未显示收益或交易员统计。</p><Link href="/research">返回策略研究</Link></section></main>;
 const data=parsed.data,diagnostic=data.gold.results.filter(row=>row.scenario==='DIAGNOSTIC_NO_REAL_NEWS');
 const bars=diagnostic.reduce((sum,row)=>sum+row.bars,0),fills=data.traders.accounts.reduce((sum,row)=>sum+row.fills,0);
 return <main className={styles.page}>
  <header className={styles.heading}><div><p className={styles.eyebrow}>策略研究 / 执行验证</p><h1>先验证规则，再接入账户</h1><p className={styles.muted}>双向模拟、保护退出与公开交易行为的固定研究记录。</p></div><span className={styles.badge}>只读研究</span></header>
  <nav className={styles.navigation} aria-label="执行研究导航"><a href="#gold-history">黄金历史</a><a href="#trader-observation">交易员观察</a><a href="#entry-model">三条件入场</a><a href="#execution-status">接入状态</a></nav>
  <section className={styles.card}><h2>本次验证</h2><p>以下是发布时的固定结果，既有模拟账户仍按各自策略运行。本页不会创建账户、开启模型或下单。</p><div className={styles.metrics}><div className={styles.metric}><small>源码测试通过</small><strong>{number(data.tests.passed)}</strong></div><div className={styles.metric}><small>真实历史柱</small><strong>{number(bars)}</strong></div><div className={styles.metric}><small>观测公开成交</small><strong>{number(fills)}</strong></div></div></section>
  <section className={styles.card} id="gold-history"><h2>黄金双向模拟 · 2023—2025</h2><p>历史真实 bid/ask，独立年度 10,000 USD，固定 1oz；SMA20、止损 1%、止盈 2%，单边费率 0.01%、额外滑点 0.05 USD/oz。参数是机械验证假设。</p><p><strong>没有真实历史新闻。</strong>必须新闻源的三个场景全部零开仓；下列诊断场景使用明确标记的合成空日历，只检验模拟成交和成本计算。</p>
   <div className={styles.grid}>{diagnostic.map(row=><article className={styles.item} key={row.year}><h3>{row.year} 年</h3><div className={`${styles.result} ${Number(row.return)<0?styles.loss:styles.gain}`}>{percent(row.return)}</div><p>期末标记权益变化</p><dl className={styles.values}><div><dt>完整观测柱</dt><dd>{number(row.bars)}</dd></div><div><dt>多头 / 空头入场</dt><dd>{row.long_entries} / {row.short_entries}</dd></div><div><dt>止损 / 止盈退出</dt><dd>{row.stop_exits} / {row.profit_exits}</dd></div><div><dt>实际模拟费用 USD</dt><dd>{Number(row.fees).toFixed(4)}</dd></div></dl></article>)}</div>
   <p>各年度末均有未平仓，未扣假设终止平仓成本。保护仅在真实采样点检查，小时内触发、融资和深度未重建。亏损年份完整保留，结果不证明未来盈利。</p><details><summary>查看历史假设与数据摘要</summary><ul>{data.gold.assumptions.map(value=><li key={value}>{value}</li>)}</ul><p className={styles.digest}>数据 SHA256 {data.gold.dataset_id}</p></details>
  </section>
  <section className={styles.card} id="trader-observation"><h2>公开交易员观察</h2><p>采样截止 <time dateTime={data.traders.as_of}>{new Date(data.traders.as_of).toISOString().slice(0,19).replace('T',' ')} UTC</time> · {data.traders.requests} 次公开查询 · 尚未开启实时订阅。</p><p>来源为 Hyperliquid 公开账户；原始品种含现货、永续及其他市场，不把代币或其他合约改称 XAU/USD 黄金。</p>
   <div className={styles.grid}>{data.traders.accounts.map((account,index)=><article className={styles.item} key={account.address}><h3>公开账户 {index+1}</h3><span className={styles.badge}>{account.pagination_truncated?'分页预算已满':account.fills===0?'本次无可见成交':'局部样本'}</span><dl className={styles.values}><div><dt>去重成交</dt><dd>{number(account.fills)}</dd></div><div><dt>原始品种标识</dt><dd>{account.assets}</dd></div><div><dt>可证明完整持仓周期</dt><dd>{account.complete_episodes}</dd></div><div><dt>未形成完整周期的成交</dt><dd>{number(account.unmatched_fills)}</dd></div></dl><details><summary>查看公开地址与费用单位</summary><p className={styles.digest}>{account.address}</p>{Object.entries(account.fees_by_token).map(([token,fee])=><p key={token}>{token} 手续费：{fee}</p>)}{account.fills===0?<p>没有可用于费用分析的成交。</p>:null}</details></article>)}</div>
   <p>样本均无法证明完整历史，不能计算可靠账户回报或虚构持仓时长。当前只描述方向、规模和时段，不能从成交直接恢复隐藏止损、入场指标或完整策略。黄金交易员来源尚未接入。</p><a href="https://hyperliquid.gitbook.io/hyperliquid-docs/for-developers/api/info-endpoint" target="_blank" rel="noreferrer">查看公开数据接口说明 ↗</a>
  </section>
  <section className={styles.card} id="entry-model"><h2>视频研究 · 三条件入场</h2><p>已通过未登录页面核对「研习生」原视频及关键画面。案例为 Bybit ETH 永续的 TradingView 回放，不能当作黄金实盘交易记录。</p><div className={styles.stage}><strong>① 结构转变</strong><span>先识别原结构，等待反方向突破。实例使用收盘突破，避免把单次影线穿越直接当作确认。</span></div><div className={styles.stage}><strong>② 三柱 FVG</strong><span>关注推动段中第一、第三根 K 线影线之间留下的价格区间；第三根未收盘时，该条件仍未确认。</span></div><div className={styles.stage}><strong>③ 回撤重叠</strong><span>视频图示明确为 0.5–0.618 回撤区与 FVG 重叠，等待价格回到该区域；不把任意缺口触碰都当入场。</span></div><p>图示止损在中间第 2 根 K 线的外侧，多单下方、空单上方。结构识别窗口、回撤锚点和完整失效规则尚未固定，本模型尚未回测，也未接入自动下单。展示过的高收益案例不代表固定止盈目标或胜率。</p><a href="https://www.douyin.com/video/7698597213601090850" target="_blank" rel="noreferrer">查看原视频与作者 ↗</a></section>
  <section className={styles.card} id="execution-status"><h2>自动执行接入状态</h2><div className={styles.stage}><strong>既有模拟账户</strong><span>现货多头 / 空仓；加密公开入口 EMA/SMA，黄金实时 SMA20。卖出库存不等于开空。</span></div><div className={styles.stage}><strong>本地已验证</strong><span>独立双向研究内核、绝对止盈止损、费用守恒、迟到报价防回填、高影响消息窗口拦截。</span></div><div className={styles.stage}><strong>下一接入阶段</strong><span>独立双向账户存储与后台执行、可靠新闻适配、持续公开交易观察、网页配置及完整集成验证。</span></div><p>新闻门禁要求：高影响事件期间禁止开仓和加仓；已有持仓仍允许止盈止损。消息源缺失、过期或覆盖不足时，不认定为安全。该新内核尚未接入现有账户。</p></section>
  <section className={styles.card}><h2>复核与下载</h2><p className={styles.digest}>研究源码 {data.source_commit}</p><details><summary>查看证据摘要</summary>{Object.entries(data.source_sha256).map(([file,hash])=><p className={styles.digest} key={file}>{file}<br/>{hash}</p>)}</details><div className={styles.footer}><a className={styles.download} href="/research/execution-validation.json" download="fc666-execution-validation.json">下载固定研究摘要</a><Link className={styles.download} href="/research">策略回测</Link><Link className={styles.download} href="/gold/research">黄金策略对比</Link><Link className={styles.download} href="/paper/realtime">现有实时模拟</Link></div></section>
 </main>;
}
