import reportData from '../../../../public/research/ppo-v2-own-trajectories.json';
import {parseTrajectoryReport,trajectoryReportPath,trajectoryReportSha256,type TrajectoryCase} from '../../../../lib/gold-ppo-trajectories';

const number=(value:number)=>value.toLocaleString('zh-CN');
const percent=(value:string)=>`${(Number(value)*100).toFixed(2)}%`;
const money=(value:string)=>Number(value).toFixed(2);
const costNames={configured_costs:'配置成本',double_cost_stress:'双倍成本压力'} as const;
function name(row:TrajectoryCase){
 if(row.kind==='hold')return '50% 买入持有参照';
 if(row.kind==='fixed')return `固定 ${Number(row.id.split('-')[1])*25}% 目标`;
 const [arm,seed]=row.id.split('-');
 return `${arm} · 种子 ${seed}`;
}

export default function OwnTrajectoryEvidence(){
 const parsed=parseTrajectoryReport(reportData);
 if(!parsed.success)return <section className="gold-card" aria-labelledby="own-trajectory-heading"><h2 id="own-trajectory-heading">Linux 独立资金轨迹记录暂不可用</h2><p role="alert">新记录未通过完整性、摘要或关闭状态校验，未显示指标。原容器失败记录保留，候选与实盘继续关闭。</p></section>;
 const report=parsed.data,{run,evidence,independent_validation:validation}=report;
 return <section className="gold-card" aria-labelledby="own-trajectory-heading">
  <h2 id="own-trajectory-heading">Linux 独立资金轨迹：26 场景完整核对</h2>
  <p>本次每个场景从自己的现金、持仓和 peak 推进，完成2024年历史窗口的成交、费用与权益核对。全部26项的完整指标、成交和权益摘要，分别与原 native / f64 结果一致；收益准入仍然失败，模型未注册、未启用。</p>
  <dl className="gold-ppo-protocol">
   <div><dt>学习策略 / 固定目标 / 持有市场步</dt><dd>{number(run.steps.policy)} / {number(run.steps.fixed)} / {number(run.steps.hold)}</dd></div>
   <div><dt>原生 / f64 完整结果一致</dt><dd>{validation.comparison_counts.native.metrics} / 26 · {validation.comparison_counts.f64.metrics} / 26</dd></div>
   <div><dt>本次实际拒绝</dt><dd>{run.refusals}</dd></div>
   <div><dt>主推理预算 / 实测最大耗时</dt><dd>{report.protocol.deadline_ms} ms / {Number(run.max_inference_wall_ms).toFixed(3)} ms</dd></div>
   <div><dt>运行耗时 / 进程峰 RSS</dt><dd>{Number(run.elapsed_seconds).toFixed(2)} s / {(run.max_rss_kib/1024).toFixed(1)} MiB</dd></div>
   <div><dt>财务独立复核</dt><dd>{number(validation.financial_market_steps)} 个市场步</dd></div>
  </dl>
  <p className="gold-ppo-disclosure"><strong>仅隔离的历史完整轨迹，非在线、非真实前向，也未验证来源迁移。</strong> 原先两次容器固定输入核对记录各自保留，本次不会覆盖其失败或解释首次超时。逐位向量一致不是原收益准入门槛，完整结果一致也不代表未来盈利。</p>
  <p>本次实际拒绝为0，因此没有实测到 Linux 持仓拒绝保仓事件；合成拒绝测试属于另一类证据。独立复核已逐笔重建费用、现金、库存和全部市场估值，并拒绝现金回执篡改、权益估值篡改及“拒绝步却成交”三项离线反例。</p>
  {(['configured_costs','double_cost_stress'] as const).map(scenario=><details key={scenario}><summary>{costNames[scenario]} · 全部13场景与摘要</summary>
   {report.cases.filter(row=>row.scenario===scenario).map(row=><details key={row.id} data-own-trajectory-case={row.id}>
    <summary>{name(row)} · 净收益 {percent(row.metrics.total_return)} · 回撤 {percent(row.metrics.max_drawdown)}</summary>
    <dl className="gold-ppo-protocol"><div><dt>费用 / 点差 / 滑点 USD</dt><dd>{money(row.metrics.fees)} / {money(row.metrics.spread_cost)} / {money(row.metrics.slippage_cost)}</dd></div><div><dt>成交 / 实际拒绝</dt><dd>{number(row.metrics.fill_count)} / {row.refusal_count}</dd></div><div><dt>窗口市场步 / 保存权益点</dt><dd>{number(row.market_steps)} / {number(row.equity_marks)}</dd></div><div><dt>原生 / f64 完整三项摘要</dt><dd>{Object.values(row.references.native.equal).every(Boolean)?'全部一致':'存在差异'} / {Object.values(row.references.f64.equal).every(Boolean)?'全部一致':'存在差异'}</dd></div></dl>
    <dl className="gold-ppo-hashes"><div><dt>完整指标 SHA256</dt><dd>{row.hashes.metrics}</dd></div><div><dt>全部成交 SHA256</dt><dd>{row.hashes.fills}</dd></div><div><dt>全部权益点 SHA256</dt><dd>{row.hashes.equity}</dd></div></dl>
   </details>)}
  </details>)}
  <details><summary>运行环境、隔离与证据范围</summary>
   <p>Linux · Python {run.python}，Pydantic {run.dependencies.pydantic}；Mac冻结研究版本为 {run.local_frozen_dependencies.pydantic}。部署镜像仍为已锁定环境，本次只在临时隔离目录加入固定版本 NumPy / Gym 等五个已核官方摘要的包，未更改线上镜像或服务。版本差异尚无因果归因。</p>
   <p>无网络、只读根目录、无业务卷挂载、非root；1 CPU / 512 MiB，临时目录192 MiB。学习18项用AuditEnv、固定6项用GoldEnv；持有2项保留原simulate及248个采样权益点，均覆盖5,917个窗口市场步。模型输入的 peak 用自身原始 bid 估值，压力场景的财务 mark 扩大点差，沿用冻结口径，因此本次是完整资金轨迹，不是纯数值对照。</p>
   <dl className="gold-ppo-hashes"><div><dt>镜像 SHA256</dt><dd>{report.isolation.image}</dd></div><div><dt>完整28条原始记录 SHA256</dt><dd>{evidence.results_sha256}</dd></div><div><dt>输入包 SHA256</dt><dd>{evidence.input_pack_sha256}</dd></div><div><dt>独立财务复核 SHA256</dt><dd>{validation.report_sha256}</dd></div><div><dt>本页摘要 JSON SHA256</dt><dd>{trajectoryReportSha256}</dd></div><div><dt>原始记录 gzip SHA256</dt><dd>{report.raw_download.sha256}</dd></div></dl>
  </details>
  <div className="gold-ppo-links"><a className="secondary" href={trajectoryReportPath} download="ppo-v2-own-trajectories.json">下载26场景完整摘要 JSON</a><a className="secondary" href={validation.report_path} download="ppo-v2-own-trajectory-validation.json">下载独立财务复核记录</a><a className="secondary" href={report.raw_download.path} download={report.raw_download.filename}>下载原始运行记录 gzip · {(report.raw_download.bytes/1000000).toFixed(1)} MB</a></div>
 </section>;
}
