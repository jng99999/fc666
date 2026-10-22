import type {Metadata} from 'next';
import Link from 'next/link';
import summaryData from '../../../../public/research/ppo-training-cost-summary.json';
import {costStressSummaryPath,costStressSummarySha256,parseCostStressSummary,type CostStressCase} from '../../../../lib/gold-ppo-cost-stress';
import '../../gold.css';
import '../ppo.css';

export const metadata:Metadata={title:'PPO 训练成本研究 · FC666'};
const costs={configured_costs:'配置成本 1×',double_cost_stress:'双倍成本压力 2×'} as const;
const names={B:'原 B · 标准化行情',C:'原 C · 行情消融',D:'新 D · 压力训练',E:'新 E · 消融压力训练'} as const;
const percent=(value:string)=>`${(Number(value)*100).toFixed(2)}%`;
const money=(value:string)=>Number(value).toLocaleString('zh-CN',{minimumFractionDigits:2,maximumFractionDigits:2});
const integer=(value:number)=>value.toLocaleString('zh-CN');
function label(row:CostStressCase){
 if(row.arm==='hold')return '50% 买入持有参照';
 if(row.arm==='fixed')return `固定 ${row.fixed_action!*25}% 目标`;
 return `${names[row.arm]} · 种子 ${row.seed}`;
}
function actions(row:CostStressCase){
 if(row.action_counts===null)return <small>持有参照，无目标动作计数</small>;
 return row.action_counts.map((count,index)=><small key={index}>{index*25}%：{integer(count)}</small>);
}

export default function CostStressPage(){
 const parsed=parseCostStressSummary(summaryData);
 if(!parsed.success)return <main className="gold-workspace gold-ppo"><section className="gold-card"><h1>训练成本研究暂不可用</h1><p role="alert">研究摘要未通过完整性、判据、关闭状态或 SHA256 校验，未显示收益和下载。模型未注册，实盘继续关闭。</p><Link href="/gold/ppo/v2">返回输入对照研究</Link></section></main>;
 const study=parsed.data;
 return <main className="gold-workspace gold-ppo">
  <header className="gold-heading"><div><p>黄金 · PPO 训练成本研究</p><h1>训练成本对照</h1><span>6 个新模型 · 32 项完整对照 · 2024 已见探索</span></div><Link href="/gold/ppo/v2">输入对照研究</Link></header>
  <div className="gold-ppo-status" aria-label="研究与交易状态"><span className="gold-ppo-badge">一次性成本干预</span><span className="gold-ppo-badge gold-ppo-disabled">仅历史探索 · 未准入</span><span className="gold-ppo-badge gold-ppo-disabled">实盘关闭</span></div>
  <section className="gold-card gold-ppo-gate" aria-labelledby="cost-hypothesis"><h2 id="cost-hypothesis">H3 · {study.conclusion.H3.supported?'探索性支持':'未获支持'}</h2>
   <p>三个压力训练 D 种子必须分别比原 B 降低显式成交成本、提高压力场景净收益，并保留实际成交和至少两种动作。一个种子失败就不支持，不挑选冠军种子。</p>
   <dl className="gold-ppo-protocol"><div><dt>训练 / 探索窗口</dt><dd>2023 / 2024</dd></div><div><dt>训练成本 / 评价成本</dt><dd>2× / 原 1× 与 2×</dd></div><div><dt>新训练预算</dt><dd>6 × {integer(study.protocol.timesteps_per_run)} 步</dd></div><div><dt>全部随机种子</dt><dd>7 / 19 / 42</dd></div><div><dt>运行耗时</dt><dd>{study.elapsed_seconds.toFixed(2)} 秒</dd></div><div><dt>新模型部署与前向验证</dt><dd>尚未验证，未注册</dd></div></dl>
   <p className="gold-ppo-disclosure"><strong>2024 已被查看，属于探索，不是盲测或真实前向。</strong> H3 有支持也不等于策略准入。原收益、回撤和非退化要求保持不变；新模型没有旧模型的 f64、容器或未来行情证明，不能自动下单。</p>
   <table className="gold-ppo-table"><caption>全部三个种子 · 双倍成本压力下 D 与原 B 的配对结果</caption><thead><tr><th>种子</th><th>净收益 D / B</th><th>显式成本 D / B · USD</th><th>D 回撤 / 成交</th><th>D 目标动作</th><th>四项检查</th></tr></thead><tbody>
    {study.conclusion.H3.cases.map(check=>{const candidate=study.results.find(row=>row.arm==='D'&&row.seed===check.seed&&row.scenario===check.scenario)!;const control=study.results.find(row=>row.arm==='B'&&row.seed===check.seed&&row.scenario===check.scenario)!;
     return <tr key={check.seed} data-h3-seed={check.seed}><td data-label="种子">种子 {check.seed}</td><td data-label="净收益 D / B">{percent(candidate.metrics.total_return)} / {percent(control.metrics.total_return)}<small>差额 {percent(check.return_delta)}</small></td><td data-label="显式成本 D / B · USD">{money(check.new_explicit_cost)} / {money(check.control_explicit_cost)}</td><td data-label="D 回撤 / 成交">{percent(candidate.metrics.max_drawdown)} / {integer(candidate.metrics.fill_count)} 笔</td><td className="gold-ppo-counts" data-label="D 目标动作">{actions(candidate)}</td><td data-label="四项检查"><small>成本下降：{check.checks.explicit_cost_lt_control?'是':'否'}</small><small>收益提高：{check.checks.return_gt_control?'是':'否'}</small><small>两种动作：{check.checks.at_least_two_actions?'是':'否'}</small><small>实际成交：{check.checks.has_fills?'是':'否'}</small></td></tr>;})}
   </tbody></table>
  </section>
  <section className="gold-card"><h2>行情增益与原准入诊断</h2><p>市场增益：{study.conclusion.market_increment.demonstrated?'探索条件全部满足':'未获展示'}。必须每个 D 种子在两种评价成本下均超过配对 E 与最佳固定目标收益，且回撤不高于这两项参照。该判断与 H3 分别报告。</p>
   <table className="gold-ppo-table"><caption>全部三种子 × 双成本 · 探索条件不授权接入</caption><thead><tr><th>场景</th><th>收益超过 E / 固定目标</th><th>回撤不高于 E / 固定目标</th><th>原准入探索诊断</th></tr></thead><tbody>{study.conclusion.market_increment.cases.map(row=>{const gate=study.conclusion.legacy_gate_diagnostics.find(gate=>gate.seed===row.seed&&gate.scenario===row.scenario)!;
    return <tr key={`${row.seed}-${row.scenario}`} data-cost-diagnostic={`${row.seed}-${row.scenario}`}><td data-label="场景">种子 {row.seed}<small>{costs[row.scenario]}</small></td><td data-label="收益超过 E / 固定目标">{row.checks.return_gt_E?'是':'否'} / {row.checks.return_gt_best_fixed?'是':'否'}<small>最佳固定目标 {row.best_fixed_action*25}%</small></td><td data-label="回撤不高于 E / 固定目标">{row.checks.drawdown_le_E?'是':'否'} / {row.checks.drawdown_le_best_fixed?'是':'否'}</td><td data-label="原准入探索诊断"><small>正净收益：{gate.checks.positive_return?'是':'否'}</small><small>回撤不超过持有：{gate.checks.drawdown_le_hold?'是':'否'}</small><small>两种动作 / 有成交：{gate.checks.at_least_two_actions?'是':'否'} / {gate.checks.has_fills?'是':'否'}</small><strong>{gate.exploratory_conditions_met?'探索条件满足，仍未准入':'探索条件未满足'}</strong></td></tr>;})}</tbody></table>
  </section>
  <section className="gold-card gold-ppo-results" aria-labelledby="all-cost-results"><h2 id="all-cost-results">全部 32 项结果</h2><p>D/E 是本轮六个新模型；B/C、固定目标与持有参照复用原冻结结果，没有重新训练或挑选控制。以下保留全部费用和动作统计。</p>
   {(['configured_costs','double_cost_stress'] as const).map(scenario=><div className="gold-ppo-result-group" key={scenario}><h3>{costs[scenario]} · 全部16项</h3><table className="gold-ppo-table"><caption>净收益已计入手续费、历史 paired 点差及假设滑点；数值为历史模拟。</caption><thead><tr><th>模型 / 参照</th><th>净收益 / 最大回撤</th><th>显式成本 · USD</th><th>手续费 / 点差 / 滑点</th><th>成交 / 缺报价过期</th><th>目标动作分布</th></tr></thead><tbody>{study.results.filter(row=>row.scenario===scenario).map(row=><tr key={`${row.arm}-${row.seed??row.fixed_action??'hold'}`} data-cost-result={`${row.arm}-${row.seed??row.fixed_action??'hold'}-${scenario}`}><td data-label="模型 / 参照">{label(row)}</td><td data-label="净收益 / 最大回撤">{percent(row.metrics.total_return)} / {percent(row.metrics.max_drawdown)}</td><td data-label="显式成本 · USD">{money(row.total_explicit_cost)}</td><td data-label="手续费 / 点差 / 滑点">{money(row.metrics.fees)} / {money(row.metrics.spread_cost)} / {money(row.metrics.slippage_cost)}</td><td data-label="成交 / 缺报价过期">{integer(row.metrics.fill_count)} / {integer(row.metrics.expired_gap_intents)}</td><td className="gold-ppo-counts" data-label="目标动作分布">{actions(row)}</td></tr>)}</tbody></table></div>)}
  </section>
  <section className="gold-card"><h2>边界与证据</h2><ul className="gold-ppo-reasons"><li>成本干预包含原生环境中 mark、现金、定量、持仓与 peak 的变化，不能归因于单独一种费用。</li><li>2023 是唯一训练窗；不追加2025评价，不增加训练预算或挑选种子。</li><li>原生观测语义保持不变；未来须验证精确部署策略和同源真实行情。HistData 历史不能代替 Swissquote 在线暖机。</li><li>没有融资、真实深度、延迟或经纪商规格保证；这些结果不证明未来盈利。</li></ul>
   <details><summary>查看运行、模型与下载摘要</summary><dl className="gold-ppo-hashes"><div><dt>公开摘要 SHA256</dt><dd>{costStressSummarySha256}</dd></div><div><dt>预注册协议 SHA256</dt><dd>{study.run.protocol_sha256}</dd></div><div><dt>研究执行源码 SHA256</dt><dd>{study.run.runner_sha256}</dd></div><div><dt>源码提交</dt><dd>{study.run.source_commit}</dd></div><div><dt>冻结依赖 SHA256</dt><dd>{study.run.dependencies_sha256}</dd></div>{study.model_identities.map(model=><div key={`${model.study_arm}-${model.seed}`}><dt>{model.study_arm} · 种子 {model.seed} · actor SHA256</dt><dd>{model.actor_sha256}</dd></div>)}</dl><p>下载为规范化公开摘要，包含32项指标、全部判据和文件摘要。模型 ZIP、权重及原始报价不对外发布。</p></details>
   <div className="gold-ppo-links"><a className="secondary" href={costStressSummaryPath} download="ppo-training-cost-summary.json">下载全部32项真实研究摘要 JSON</a><Link href="/gold/ppo/v2">查看原 v2 失败结论与复现</Link><Link href="/gold/automation">查看模拟执行状态</Link></div>
  </section>
 </main>;
}
