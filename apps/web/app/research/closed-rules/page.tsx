import Link from 'next/link';
import summary from '../../../public/research/closed-rules-backtest.json';
import {ClosedRulesStudy,type ClosedRuleStudyRow} from '../../../lib/closed-rules-study';
import {directionalRuleName} from '../../../lib/directional';
import styles from '../execution/page.module.css';
import local from './page.module.css';

export const metadata={title:'闭合规则回测与成本对比 · FC666'};
const percent=(value:string)=>(Number(value)*100).toFixed(2)+'%';
const amount=(value:string)=>Number(value).toLocaleString('zh-CN',{maximumFractionDigits:4});
function Result({row}:{row:ClosedRuleStudyRow}){
 const m=row.metrics;
 return <article className={styles.item}><h3>{directionalRuleName(row.rule)}</h3><span className={styles.badge}>{row.scenario==='BASE'?'基准成本':'压力成本'} · 离线诊断</span><div className={`${styles.result} ${Number(m.total_return)<0?styles.loss:styles.gain}`}>{percent(m.total_return)}</div><p>独立模拟账户净收益</p><dl className={`${styles.values} ${local.values}`}>
  <div><dt>最大收盘权益回撤</dt><dd>{percent(m.max_drawdown)}</dd></div><div><dt>净盈亏</dt><dd>{amount(m.net_pnl)}</dd></div><div><dt>手续费</dt><dd>{amount(m.fees)}</dd></div><div><dt>点差 / 滑点成本</dt><dd>{amount(m.spread_cost)} / {amount(m.slippage_cost)}</dd></div><div><dt>多 / 空入场</dt><dd>{row.long_entries} / {row.short_entries}</dd></div><div><dt>止损 / 止盈退出</dt><dd>{row.stop_exits} / {row.profit_exits}</dd></div><div><dt>成交 / 闭合交易</dt><dd>{m.fill_count} / {m.closed_trade_count}</dd></div><div><dt>期末未平数量</dt><dd>{amount(m.open_quantity)}</dd></div>
 </dl>{m.fill_count===0?<p>无成交，不能据此判断策略优劣。</p>:null}<details><summary>冻结参数与运行凭证</summary><p>每次 {row.costs.quantity}；单边费率 {percent(row.costs.fee_rate)}；完整点差 {row.costs.spread}；单边不利滑点 {row.costs.slippage}。止损 {percent(row.costs.stop_loss_percent)}、止盈 {percent(row.costs.take_profit_percent)}；单笔额度 {row.costs.max_order_quote}。</p><p className={styles.digest}>运行 {row.run_id}<br/>案例 {row.case_id}</p></details></article>;
}

export default function ClosedRuleResearch(){
 const parsed=ClosedRulesStudy.safeParse(summary);
 if(!parsed.success)return <main className={styles.page}><section className={styles.card}><h1>回测摘要暂不可用</h1><p role="alert">固定结果未通过校验，不显示收益。请保留原始记录后重新复核。</p><Link href="/paper/directional">返回模拟账户</Link></section></main>;
 const data=parsed.data,diagnostic=data.results.filter(row=>row.news_policy==='UNCOVERED_RESEARCH');
 const scopes=[...new Set(diagnostic.map(row=>`${row.instrument}|${row.timeframe}|${row.window}`))];
 return <main className={styles.page}>
  <header className={styles.heading}><div><p className={styles.eyebrow}>策略研究 / 闭合规则</p><h1>闭合规则回测</h1><p className={styles.muted}>真实历史 · 多空模型回测 · 实盘关闭</p></div><span className={styles.badge}>只读研究</span></header>
  <nav className={styles.navigation} aria-label="闭合规则研究导航"><a href="#scope">数据与假设</a><a href="#results">多策略对比</a><a href="#news">消息门禁</a><a href="#reproduce">复现与下载</a></nav>
  <section className={styles.card} id="scope"><h2>数据与验证范围</h2><p>共用线上三组规则与滚动 500 柱。每项独立资金、预热与冻结参数，保留全部盈亏。</p><p>收盘决策、下一柱开盘模型成交。费用、点差与滑点为假设，不代表真实历史盘口。</p><p><strong>缺少真实历史新闻。</strong>收益仅作未覆盖消息诊断，线上仍禁止开仓。</p><p>黄金：{data.gold_status.state==='AVAILABLE'?'真实 1m 来源外数据已加入，不等于 Swissquote 同源前向。':data.gold_status.detail}</p>
   <div className={styles.metrics}><div className={styles.metric}><small>数据窗口</small><strong>{data.datasets.length}</strong></div><div className={styles.metric}><small>回测案例</small><strong>{data.results.length}</strong></div><div className={styles.metric}><small>严格成交</small><strong>{data.strict_gate.fills}</strong></div></div>
   <details><summary>数据范围、来源与摘要</summary>{data.datasets.map(row=><article key={`${row.dataset_sha256}:${row.window}`}><h3>{row.instrument} · {row.timeframe} · {row.window}</h3><p>{row.start} 至 {row.end_exclusive}（结束不含） · {row.bars.toLocaleString('zh-CN')} 根 · {row.source}</p><p className={styles.digest}>{row.dataset_sha256}</p></article>)}{data.gold_provenance?<article><h3>黄金原始配对报价</h3><p>{data.gold_provenance.ticks.toLocaleString('zh-CN')} 条报价，完整分钟 {data.gold_provenance.complete_minutes.toLocaleString('zh-CN')}，覆盖不足排除 {data.gold_provenance.incomplete_minutes_excluded} 分钟。</p><p className={styles.digest}>原始 ZIP {data.gold_provenance.zip_sha256}<br/>CSV {data.gold_provenance.raw_csv_sha256}</p><a href={data.gold_provenance.page} target="_blank" rel="noreferrer">查看原始公开来源 ↗</a></article>:null}</details>
  </section>
  <section id="results" className={local.section}><h2>全部规则与成本对比</h2><p className={styles.muted}>不同产品数量不同，请在同产品、同周期、同窗口内比较，不当作共享资金组合。后 40% 是可见时间分段，不是密封盲测；黄金首尾窗口单独标记。</p>{scopes.map(scope=>{
   const [instrument,timeframe,window]=scope.split('|'),rows=diagnostic.filter(row=>`${row.instrument}|${row.timeframe}|${row.window}`===scope);
   return <details className={styles.card} key={scope} open={scope===scopes[0]}><summary>{instrument} · {timeframe} · {window==='FULL'?'完整区间':window==='LATER_40_PERCENT'?'后 40% 区间':window} · {rows.length} 组</summary><div className={local.cases}>{rows.map(row=><Result key={row.case_id} row={row}/>)}</div></details>;
  })}</section>
  <section className={styles.card} id="news"><h2>高影响消息窗口禁入</h2><p>严格模式在没有真实历史日历时，{data.strict_gate.cases} 组案例共 {data.strict_gate.fills} 笔成交。离线诊断结果不会改变线上门禁；已有持仓仍保留止盈止损。</p><p>高影响窗口、来源错误、过期和跨窗口旧意图使用独立隔离场景核验，不能把合成测试事件当成真实历史消息覆盖。</p><ul className={local.limits}>{data.limits.map(value=><li key={value}>{value}</li>)}</ul><Link href="/system/api">配置服务器可信新闻来源 →</Link></section>
  <section className={styles.card} id="reproduce"><h2>复现与下一步</h2>
   <p>固定协议：docs/closed-rules-backtest-protocol.json。运行脚本先校验数据摘要，再执行同一规则；默认不下载网络数据，显式 --fetch 才读取公开加密历史。黄金首次采集另运行 freeze_gold_rule_history，保留原始压缩包和摘要。</p>
   <code className={local.command}>python -m scripts.study_closed_rules --history /absolute/frozen-history --gold-history /absolute/gold-1m --output /absolute/study-results</code>
   <p className={styles.digest}>研究 {data.study_id}<br/>协议 {data.protocol_sha256}<br/>生成 {data.generated_at}</p>
   <details><summary>查看计算代码摘要</summary>{Object.entries(data.source_sha256).map(([file,hash])=><p className={styles.digest} key={file}>{file}<br/>{hash}</p>)}</details>
   <div className={styles.footer}><a className={styles.download} href="/research/closed-rules-backtest.json" download>下载全部结果摘要</a><a className={styles.download} href="/research/closed-rules-protocol.json" download>下载冻结参数</a><Link className={styles.download} href="/paper/directional">打开模拟账户</Link><Link className={styles.download} href="/research/execution">执行验证</Link></div>
  </section>
 </main>;
}
