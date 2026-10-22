import type {Metadata} from 'next';
import Link from 'next/link';
import {z} from 'zod';
import studyData from '../../../data/ppo-study.json';
import '../gold.css';
import './ppo.css';

export const metadata: Metadata = {title: 'PPO 强化学习研究 · FC666'};

const decimal = z.string().regex(/^-?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?$/).refine(value => Number.isFinite(Number(value)));
const resultSchema = z.object({
  strategy: z.string(), seed: z.number().int().nullable(),
  window: z.enum(['validation', 'evaluation']), scenario: z.enum(['configured_costs', 'double_cost_stress']),
  metrics: z.object({total_return: decimal, max_drawdown: decimal, fees: decimal, spread_cost: decimal, slippage_cost: decimal, fill_count: z.number().int().nonnegative()}),
  action_counts: z.array(z.number().int().nonnegative()).nullable(),
});
const studySchema = z.object({
  version: z.string(), generated_at: z.string(),
  dataset: z.object({id: z.string(), source: z.string(), start: z.string(), end: z.string(), bars: z.number().int().nonnegative()}),
  protocol: z.object({train_start: z.string(), train_end: z.string(), validation_end: z.string(), evaluation_end: z.string(), seeds: z.array(z.number().int()), timesteps: z.number().int().nonnegative(), actions: z.array(z.string()), costs: z.record(z.string(), z.unknown()), not_blind: z.boolean()}),
  gate: z.object({eligible: z.boolean(), reasons: z.array(z.string()), live_trading: z.literal(false)}),
  results: z.array(resultSchema), limitations: z.array(z.string()), artifacts_sha256: z.record(z.string(), z.string()),
});
type Result = z.infer<typeof resultSchema>;
const strategyNames: Record<string, string> = {ppo_target_v1: 'PPO 目标仓位', ema_long_flat_v1: 'EMA', sma_long_flat_v1: '双均线', rsi_long_flat_v1: 'RSI', bollinger_long_flat_v1: '布林带', channel_long_flat_v1: '通道突破', macd_long_flat_v1: 'MACD', roc_long_flat_v1: 'ROC 动量', stochastic_long_flat_v1: '随机指标', buy_hold_reference: '持有基准', cash_reference: '现金基准'};
const costNames: Record<string, string> = {initial_cash: '初始资金 · USD', allocation: '最大投入比例', commission: '单边费用 · USD/oz', slippage: '单边额外滑点 · USD/oz', spread_multiplier: '原始点差乘数'};
const scenarioNames = {configured_costs: '配置成本', double_cost_stress: '双倍成本压力'};
const windowNames = {validation: '验证窗口', evaluation: '后续评估窗口'};
const percent = (value: string) => `${(Number(value) * 100).toFixed(2)}%`;
const money = (value: string) => Number(value).toLocaleString('zh-CN', {minimumFractionDigits: 2, maximumFractionDigits: 2});
const day = (value: string) => value.slice(0, 10);

function ResultTable({rows, actions}: {rows: Result[]; actions: string[]}) {
  return <table className="gold-ppo-table">
    <caption>各策略使用独立资金，收益不能相加。金额单位为 USD，收益与回撤显示为百分比。</caption>
    <thead><tr><th scope="col">策略 / 随机种子</th><th scope="col">权益收益</th><th scope="col">最大回撤</th><th scope="col">费用 / 点差 / 滑点</th><th scope="col">模拟成交笔数</th><th scope="col">决策动作计数</th></tr></thead>
    <tbody>{rows.map((row, index) => <tr key={`${row.strategy}-${row.seed ?? 'baseline'}-${index}`}>
      <td data-label="策略 / 随机种子"><strong>{strategyNames[row.strategy] ?? row.strategy}</strong><small>{row.seed === null ? '规则基线' : `种子 ${row.seed}`}</small></td>
      <td data-label="权益收益" className={Number(row.metrics.total_return) < 0 ? 'gold-ppo-negative' : undefined}>{percent(row.metrics.total_return)}</td>
      <td data-label="最大回撤">{percent(row.metrics.max_drawdown)}</td>
      <td data-label="费用 / 点差 / 滑点">{money(row.metrics.fees)}<small>{money(row.metrics.spread_cost)} / {money(row.metrics.slippage_cost)}</small></td>
      <td data-label="模拟成交笔数">{row.metrics.fill_count.toLocaleString('zh-CN')}</td>
      <td data-label="决策动作计数" className="gold-ppo-counts">{row.action_counts === null ? '不适用' : row.action_counts.length !== actions.length ? '动作计数与定义不匹配' : row.action_counts.map((count, action) => <small key={`${actions[action]}-${action}`}>{actions[action]} · {count.toLocaleString('zh-CN')}</small>)}</td>
    </tr>)}</tbody>
  </table>;
}

export default function PpoResearch() {
  const parsed = studySchema.safeParse(studyData);
  if (!parsed.success) return <main className="gold-workspace gold-ppo"><section className="gold-card"><h1>PPO 研究摘要暂不可用</h1><p role="alert">研究文件未通过完整性格式校验，未显示指标，也不会启动模型或交易。</p><Link href="/gold/research">返回黄金策略研究</Link></section></main>;
  const study = parsed.data;
  const {protocol} = study;
  return <main className="gold-workspace gold-ppo">
    <header className="gold-heading"><div><p>黄金 · AI 策略实验</p><h1>PPO 强化学习研究</h1><span>冻结模型的历史评估 · 本页只读 · 实盘关闭</span></div><Link href="/gold/research">返回策略对比</Link></header>
    <section className="gold-card gold-ppo-gate" aria-labelledby="ppo-gate-heading">
      <div className="gold-ppo-status"><span className="gold-ppo-badge">只读研究</span><span className="gold-ppo-badge gold-ppo-disabled">实盘关闭</span></div>
      <h2 id="ppo-gate-heading">{study.gate.eligible ? '达到本次研究门槛' : '未达到模拟试运行门槛'}</h2>
      <p>{study.gate.eligible ? '研究门槛结果不等于已接入模拟账户。模型的实时执行仍需独立验收。' : '该实验结果保留用于复盘，未开放启用或自动下单入口。'}</p>
      {study.gate.reasons.length > 0 ? <ul className="gold-ppo-reasons">{study.gate.reasons.map((reason, index) => <li key={index}>{reason}</li>)}</ul> : null}
      <p className="gold-ppo-disclosure"><strong>{protocol.not_blind ? '这是非盲测历史评估。' : '请按下方冻结协议理解评估范围。'}</strong> 历史结果不代表未来盈利，页面不提供模型启用权限。</p>
    </section>
    <section className="gold-card" aria-labelledby="ppo-protocol-heading">
      <h2 id="ppo-protocol-heading">训练与选择协议</h2>
      <dl className="gold-ppo-protocol">
        <div><dt>训练窗口 · UTC</dt><dd>{day(protocol.train_start)} → {day(protocol.train_end)}</dd></div>
        <div><dt>验证窗口 · UTC</dt><dd>{day(protocol.train_end)} → {day(protocol.validation_end)}</dd></div>
        <div><dt>后续评估窗口 · UTC</dt><dd>{day(protocol.validation_end)} → {day(protocol.evaluation_end)}</dd></div>
        <div><dt>训练步数</dt><dd>{protocol.timesteps.toLocaleString('zh-CN')}</dd></div>
        <div><dt>随机种子</dt><dd>{protocol.seeds.length ? protocol.seeds.join('、') : '未记录'}</dd></div>
        <div><dt>模型动作定义</dt><dd>{protocol.actions.length ? protocol.actions.join(' / ') : '未记录'}</dd></div>
      </dl>
      <p>PPO 动作对应空仓、25% 或 50% 权益目标仓位，持仓期间会再平衡；不是每次追加买入 25% 或 50%。</p>
      <p>窗口结束时间不包含在对应区间内。不同种子分别展示，不能只按后续评估收益挑选赢家。</p>
      <details><summary>成本配置与数据来源</summary><dl className="gold-ppo-protocol gold-ppo-costs">{Object.entries(protocol.costs).map(([key, value]) => <div key={key}><dt>{costNames[key] ?? key}</dt><dd>{typeof value === 'string' || typeof value === 'number' ? String(value) : JSON.stringify(value)}</dd></div>)}</dl><p>{study.dataset.source} · {day(study.dataset.start)} → {day(study.dataset.end)} · {study.dataset.bars.toLocaleString('zh-CN')} 根完整柱</p><small className="gold-ppo-digest">数据 SHA256 {study.dataset.id}</small></details>
    </section>
    {(['validation', 'evaluation'] as const).map(window => <section key={window} className="gold-card gold-ppo-results" aria-labelledby={`ppo-${window}-heading`}>
      <h2 id={`ppo-${window}-heading`}>{windowNames[window]}</h2>
      {(['configured_costs', 'double_cost_stress'] as const).map(scenario => {
        const rows = study.results.filter(row => row.window === window && row.scenario === scenario);
        return <div className="gold-ppo-result-group" key={scenario}><h3>{scenarioNames[scenario]}</h3>{rows.length ? <ResultTable rows={rows} actions={protocol.actions}/> : <p>此窗口尚无该成本场景的结果，不以零收益代替。</p>}</div>;
      })}
    </section>)}
    <section className="gold-card" aria-labelledby="ppo-limits-heading"><h2 id="ppo-limits-heading">适用边界与可复核记录</h2><ul className="gold-ppo-reasons">{study.limitations.map((note, index) => <li key={index}>{note}</li>)}</ul><details><summary>查看模型与实验文件摘要</summary><dl className="gold-ppo-hashes">{Object.entries(study.artifacts_sha256).map(([name, hash]) => <div key={name}><dt>{name}</dt><dd>{hash}</dd></div>)}</dl></details><div className="gold-ppo-links"><a className="secondary" href="/research/ppo-study.json" download="ppo-study.json">下载公开研究摘要</a><Link href="/gold/research">现有黄金策略对比</Link></div><small className="gold-ppo-digest">{study.version} · 生成时间 {study.generated_at} · 此文件是发布时的固定结果，不是实时信号。</small></section>
  </main>;
}
