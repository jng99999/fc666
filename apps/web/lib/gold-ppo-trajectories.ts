import {createHash} from 'node:crypto';
import {z} from 'zod';

export const trajectoryReportSha256='9fd8b39b529bf05d9b10332f133a86dd7e3f43985e30b2003c45db6f5e174208';
export const trajectoryReportPath='/research/ppo-v2-own-trajectories.json';
const count=z.number().int().nonnegative();
const hash=z.string().regex(/^[a-f0-9]{64}$/);
const decimal=z.string().regex(/^-?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?$/).refine(value=>Number.isFinite(Number(value)));
const cost=z.object({initial_cash:decimal,allocation:decimal,commission:decimal,slippage:decimal,spread_multiplier:decimal}).strict();
const hashes=z.object({metrics:hash,fills:hash,equity:hash}).strict();
const equal=z.object({metrics:z.boolean(),fills:z.boolean(),equity:z.boolean()}).strict();
const reference=z.object({hashes,equal}).strict();
const metrics=z.object({total_return:decimal,max_drawdown:decimal.refine(value=>Number(value)>=0&&Number(value)<=1),
 final_equity:decimal,net_pnl:decimal,realized_pnl:decimal,open_ounces:decimal,fees:decimal,spread_cost:decimal,
 slippage_cost:decimal,fill_count:count,expired_gap_intents:count,unrealized_pnl:decimal.optional(),closed_trades:count.optional(),
 win_rate:decimal.nullable().optional(),profit_factor:decimal.nullable().optional(),exposure_bar_fraction:decimal.optional()}).strict();
const row=z.object({id:z.string(),kind:z.enum(['policy','fixed','hold']),scenario:z.enum(['configured_costs','double_cost_stress']),
 engine:z.enum(['AuditEnv','GoldEnv','core.gold.backtest.simulate']),costs:cost,market_steps:z.literal(5917),equity_marks:count,
 refusal_count:count,refusal_codes:z.record(z.string(),count),action_counts:z.tuple([count,count,count]).nullable(),metrics,hashes,
 result_sha256:hash,references:z.object({native:reference,f64:reference}).strict()}).strict();
const stepTotals=z.object({policy:count,fixed:count,hold:count}).strict();
const comparisonCounts=z.object({metrics:count,fills:count,equity:count}).strict();

function canonical(value:unknown):string{
 if(value===null||typeof value==='string'||typeof value==='boolean'||typeof value==='number')return JSON.stringify(value);
 if(Array.isArray(value))return `[${value.map(canonical).join(',')}]`;
 if(typeof value==='object')return `{${Object.entries(value).sort(([a],[b])=>a<b?-1:a>b?1:0).map(([key,item])=>`${JSON.stringify(key)}:${canonical(item)}`).join(',')}}`;
 throw new Error('Only JSON report values are supported');
}

const reportSchema=z.object({version:z.literal('gold-own-deployment-trajectories-public-v1'),status:z.literal('COMPLETED_OFFLINE_ONLY'),
 scope:z.literal('isolated_historical_own_account_trajectory'),not_blind:z.literal(true),candidate_eligible:z.literal(false),
 promotion_authorized:z.literal(false),trading_enabled:z.literal(false),online_verified:z.literal(false),forward_verified:z.literal(false),
 source_transfer_verified:z.literal(false),profitability_admitted:z.literal(false),bitwise_equality_is_admission_gate:z.literal(false),
 protocol:z.object({dataset:hash,window_start:z.literal(1704067200000),window_end:z.literal(1735689600000),cases:z.literal(26),models:z.literal(9),
  decisions_per_case:z.literal(5917),deadline_ms:z.literal(100),scenario_costs:z.object({configured_costs:cost,double_cost_stress:cost}).strict()}).strict(),
 run:z.object({exit_code:z.literal(0),elapsed_seconds:decimal,steps:stepTotals,refusals:count,max_inference_wall_ms:decimal,max_rss_kib:count,
  python:z.string(),platform:z.string(),dependencies:z.record(z.string(),z.string()),local_frozen_dependencies:z.record(z.string(),z.string())}).strict(),
 isolation:z.object({image:z.string().regex(/^sha256:[a-f0-9]{64}$/),readonly:z.literal(true),network:z.literal('none'),cpus:z.literal(1000000000),
  memory:z.literal(536870912),pids:z.literal(64),user:z.literal('10001:10001'),cap_drop:z.tuple([z.literal('ALL')]),
  security:z.tuple([z.literal('no-new-privileges:true')]),tmpfs:z.object({'/tmp':z.literal('rw,exec,nosuid,nodev,size=192m,mode=1777')}).strict(),mounts:z.array(z.never()).length(0)}).strict(),
 evidence:z.object({results_sha256:hash,results_bytes:count,records:z.literal(28),prior_records:z.literal(27),prior_records_sha256:hash,
  launch_manifest_sha256:hash,launcher_sha256:hash,command_sha256:hash,archive_sha256:hash,runner_sha256:hash,input_pack_sha256:hash,
  exit_sha256:hash,isolation_sha256:hash,source_sha256:z.record(z.string().regex(/^core\/(?!.*\.\.)[a-zA-Z0-9_/.]+\.py$/),hash),official_wheel_manifest_sha256:hash}).strict(),
 independent_validation:z.object({verified:z.literal(true),report_path:z.literal('/research/ppo-v2-own-trajectory-validation.json'),report_sha256:hash,
  validator_sha256:hash,financial_market_steps:count,actual_refusals:count,nonzero_inventory_refusals_verified:count,
  offline_tamper_counterexamples:z.array(z.object({case:z.string(),rejected:z.literal(true),actual_error:z.string()}).strict()).length(3),
  comparison_counts:z.object({native:comparisonCounts,f64:comparisonCounts}).strict()}).strict(),
 raw_download:z.object({path:z.literal('/research/ppo-v2-own-trajectories.jsonl.gz'),filename:z.literal('ppo-v2-own-trajectories.jsonl.gz'),
  gzip_mtime:z.literal(0),bytes:count,sha256:hash,uncompressed_sha256:hash}).strict(),cases:z.array(row).length(26),limitations:z.array(z.string()).min(1),
}).strict().superRefine((report,ctx)=>{
 const fail=(message:string)=>ctx.addIssue({code:'custom',message});
 const expected=['A','B','C'].flatMap(arm=>[7,19,42].flatMap(seed=>['configured_costs','double_cost_stress'].map(scenario=>`${arm}-${seed}-${scenario}`)));
 expected.push(...[0,1,2].flatMap(action=>['configured_costs','double_cost_stress'].map(scenario=>`fixed-${action}-${scenario}`)),...['configured_costs','double_cost_stress'].map(scenario=>`hold-${scenario}`));
 const ids=report.cases.map(item=>item.id);
 if(new Set(ids).size!==26||expected.some(id=>!ids.includes(id)))fail('完整资金轨迹场景缺失或重复');
 for(const item of report.cases){
  if((item.kind==='policy'&&(!/^[ABC]-(7|19|42)-/.test(item.id)||item.engine!=='AuditEnv'))||
   (item.kind==='fixed'&&(!/^fixed-[012]-/.test(item.id)||item.engine!=='GoldEnv'))||
   (item.kind==='hold'&&(!item.id.startsWith('hold-')||item.engine!=='core.gold.backtest.simulate')))fail('场景身份与原执行器不一致');
  if(!item.id.endsWith(item.scenario))fail('场景成本身份不一致');
  if(item.refusal_count!==Object.values(item.refusal_codes).reduce((sum,value)=>sum+value,0))fail('拒绝统计不一致');
  if(item.kind==='hold'?(item.action_counts!==null||item.equity_marks!==248):
   (item.action_counts===null||item.action_counts.reduce((sum,value)=>sum+value,0)+item.refusal_count!==5917||item.equity_marks!==5917))fail('市场步、权益采样或动作数量不一致');
  for(const key of ['initial_cash','allocation','commission','slippage','spread_multiplier'] as const){
   if(Number(item.costs[key])!==Number(report.protocol.scenario_costs[item.scenario][key]))fail('场景与冻结成本不一致');
  }
  for(const name of ['native','f64'] as const)for(const key of ['metrics','fills','equity'] as const){
   if(item.references[name].equal[key] !== (item.hashes[key]===item.references[name].hashes[key]))fail('完整结果摘要与比较标志不一致');
  }
 }
 for(const kind of ['policy','fixed','hold'] as const){
  if(report.run.steps[kind]!==report.cases.filter(item=>item.kind===kind).reduce((sum,item)=>sum+item.market_steps,0))fail('完整市场步汇总不一致');
 }
 const refusals=report.cases.reduce((sum,item)=>sum+item.refusal_count,0);
 if(report.run.refusals!==refusals||report.independent_validation.actual_refusals!==refusals||report.independent_validation.nonzero_inventory_refusals_verified>refusals)fail('实际拒绝覆盖范围不一致');
 if(report.independent_validation.financial_market_steps!==Object.values(report.run.steps).reduce((sum,value)=>sum+value,0))fail('独立财务重建步数不一致');
 for(const name of ['native','f64'] as const)for(const key of ['metrics','fills','equity'] as const){
  if(report.independent_validation.comparison_counts[name][key]!==report.cases.filter(item=>item.references[name].equal[key]).length)fail('全量比较计数不一致');
 }
 if(report.evidence.results_sha256!==report.raw_download.uncompressed_sha256)fail('原始结果下载摘要不一致');
 if(createHash('sha256').update(canonical(report)).digest('hex')!==trajectoryReportSha256)fail('研究下载快照摘要不一致');
});

export type TrajectoryReport=z.infer<typeof reportSchema>;
export type TrajectoryCase=TrajectoryReport['cases'][number];
export function parseTrajectoryReport(value:unknown){return reportSchema.safeParse(value);}
