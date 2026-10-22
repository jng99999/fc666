import {createHash} from 'node:crypto';
import {z} from 'zod';

// Completed real study snapshot; any changed result or conclusion fails closed.
export const costStressSummarySha256='7ddddc0799837d413415df8c0a0dc6c4520e685e28c24e3cd323b904ff3f2175';
export const costStressSummaryPath='/research/ppo-training-cost-summary.json';
export const costStressProtocolSha256='3dfc0621c4fb0bda412b42c5d5670b7294fbd395371bef03c9bf25d46c017763';
const version='gold-ppo-training-cost-intervention-v1';
const seeds=[7,19,42] as const;
const scenarios=['configured_costs','double_cost_stress'] as const;
const count=z.number().int().nonnegative();
const hash=z.string().regex(/^[a-f0-9]{64}$/);
const seed=z.union([z.literal(7),z.literal(19),z.literal(42)]);
const scenario=z.enum(scenarios);
const decimalPattern=/^-?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?$/;
const decimal=z.string().max(256).regex(decimalPattern)
 .refine(value=>Number.isFinite(Number(value))&&Math.abs(Number(value.split(/[eE]/)[1]??0))<=1000);

// Keep comparisons exact: a rounded display number must never change a hypothesis check.
function parts(value:string){
 const [mantissa,exponent='0']=value.toLowerCase().split('e');
 if(value.length>256||!decimalPattern.test(value)||!Number.isFinite(Number(value))||Math.abs(Number(exponent))>1000)return null;
 const [whole,fraction='']=mantissa.split('.');
 return {coefficient:BigInt(`${whole||'0'}${fraction}`),scale:fraction.length-Number(exponent)};
}
function compare(a:string,b:string){
 const left=parts(a),right=parts(b);
 if(left===null||right===null)return NaN;
 const scale=Math.max(left.scale,right.scale);
 const difference=left.coefficient*10n**BigInt(scale-left.scale)-right.coefficient*10n**BigInt(scale-right.scale);
 return difference<0n?-1:difference>0n?1:0;
}
function sumEquals(values:string[],expected:string){
 const parsed=[...values.map(parts),parts(expected)],numbers=parsed.filter(number=>number!==null);
 if(numbers.length!==parsed.length)return false;
 const scale=Math.max(...numbers.map(number=>number.scale));
 const aligned=numbers.map(number=>number.coefficient*10n**BigInt(scale-number.scale));
 return aligned.slice(0,-1).reduce((total,value)=>total+value,0n)===aligned.at(-1);
}
const nonnegativeDecimal=decimal.refine(value=>compare(value,'0')>=0);
const cost=z.object({initial_cash:decimal,allocation:decimal,commission:decimal,slippage:decimal,spread_multiplier:decimal});
const configured={initial_cash:'10000',allocation:'.5',commission:'.10',slippage:'.05',spread_multiplier:'1'};
const pressure={...configured,commission:'.20',slippage:'.10',spread_multiplier:'2'};
function sameCost(actual:z.infer<typeof cost>,expected:typeof configured){
 return (Object.keys(expected) as (keyof typeof expected)[]).every(key=>compare(actual[key],expected[key])===0);
}
const flags={promotion_authorized:z.literal(false),live_trading:z.literal(false)};
const protocol=z.object({study_version:z.literal(version),declared_on:z.literal('2026-10-22'),
 dataset:z.literal('ee9afe981db266959ebcc1557e6120ae6e60012f761265ed46d57ab57878276c'),
 train_start:z.literal('2023-01-01'),train_end:z.literal('2024-01-01'),exploratory_end:z.literal('2025-01-01'),not_blind:z.literal(true),
 seeds:z.tuple([z.literal(7),z.literal(19),z.literal(42)]),timesteps_per_run:z.literal(131072),new_training_runs:z.literal(6),
 arms:z.object({D:z.object({input_arm:z.literal('B'),control_arm:z.literal('B')}),E:z.object({input_arm:z.literal('C'),control_arm:z.literal('C')})}),
 configured_costs:cost,training_costs:cost,evaluation_scenarios:z.tuple([z.literal(scenarios[0]),z.literal(scenarios[1])]),
 frozen_code_sha256:z.record(z.string(),hash),baseline_sha256:z.record(z.string(),hash),dependencies_sha256:hash,
 H3:z.object({all_seeds_required:z.literal(true),scenario:z.literal('double_cost_stress')}),
 evaluation_cases:z.object({new:z.literal(12),frozen_controls_and_references:z.literal(20),total:z.literal(32)}),...flags,
}).passthrough();
const metrics=z.object({total_return:decimal.refine(value=>compare(value,'-1')>0),
 max_drawdown:nonnegativeDecimal.refine(value=>compare(value,'1')<=0),final_equity:decimal.refine(value=>compare(value,'0')>0),
 net_pnl:decimal,realized_pnl:decimal,open_ounces:nonnegativeDecimal,fees:nonnegativeDecimal,spread_cost:nonnegativeDecimal,
 slippage_cost:nonnegativeDecimal,fill_count:count,expired_gap_intents:count,unrealized_pnl:decimal.optional(),closed_trades:count.optional(),
 win_rate:decimal.nullable().optional(),profit_factor:decimal.nullable().optional(),exposure_bar_fraction:decimal.optional(),
});
const result=z.object({arm:z.enum(['B','C','D','E','fixed','hold']),seed:seed.nullable(),fixed_action:z.number().int().min(0).max(2).optional(),
 scenario,window:z.literal('validation_2024'),trading_enabled:z.literal(false),effective_start:count,effective_end:count,
 action_counts:z.tuple([count,count,count]).nullable(),metrics,total_explicit_cost:nonnegativeDecimal,
 json_actor_parity_steps:count.optional(),
}).passthrough();
const identity=z.object({study_version:z.literal(version),study_arm:z.enum(['D','E']),native_input_arm:z.enum(['B','C']),seed,
 dataset:hash,protocol_sha256:z.literal(costStressProtocolSha256),training_costs:cost,evaluation_costs:z.object({configured_costs:cost,double_cost_stress:cost}),
 timesteps:z.literal(131072),actor_file:z.string(),actor_sha256:hash,ppo_file:z.string(),ppo_sha256:hash,
 deployment_evidence_inherited:z.literal(false),...flags,
});
const activity={at_least_two_actions:z.boolean(),has_fills:z.boolean()};
const h3Checks=z.object({explicit_cost_lt_control:z.boolean(),return_gt_control:z.boolean(),...activity}).strict();
const marketChecks=z.object({return_gt_E:z.boolean(),return_gt_best_fixed:z.boolean(),drawdown_le_E:z.boolean(),drawdown_le_best_fixed:z.boolean()}).strict();
const legacyChecks=z.object({positive_return:z.boolean(),drawdown_le_hold:z.boolean(),...activity}).strict();
const transformProof=z.object({training_shape:z.tuple([z.number().int().positive(),z.literal(7)]),dtype:z.literal('float32'),
 training_sha256:hash,exploration_prefix_sha256:hash,equal:z.literal(true)});
const prefixProof=z.object({raw_equal:z.literal(true),raw_training_sha256:hash,raw_exploration_prefix_sha256:hash,
 transforms:z.object({B:transformProof,C:transformProof}),scaler_sha256:hash});

export function canonicalCostStressSummary(value:unknown):string{
 if(value===null||typeof value==='string'||typeof value==='boolean'||typeof value==='number')return JSON.stringify(value);
 if(Array.isArray(value))return `[${value.map(canonicalCostStressSummary).join(',')}]`;
 if(typeof value==='object')return `{${Object.entries(value).sort(([a],[b])=>a<b?-1:a>b?1:0)
  .map(([key,item])=>`${JSON.stringify(key)}:${canonicalCostStressSummary(item)}`).join(',')}}`;
 throw new Error('Only JSON study values are supported');
}

export const costStressSummarySchema=z.object({study_version:z.literal(version),protocol,
 run:z.object({protocol_sha256:z.literal(costStressProtocolSha256),runner_sha256:hash,source_commit:z.string().regex(/^[a-f0-9]{40}$/),
  dependencies_sha256:hash,baseline_sha256:z.record(z.string(),hash),frozen_code_sha256:z.record(z.string(),hash),prefix_proof:prefixProof,
  control_actor_origins:z.object({original_manifest_sha256:hash,actor_sha256:z.record(z.string(),hash),
   loaded_pickle_or_executed_controls:z.literal(false),new_model_deployment_evidence:z.literal(false)}),
  ...flags,
 }).passthrough(),model_identities:z.array(identity).length(6),results:z.array(result).length(32),
 conclusion:z.object({H3:z.object({supported:z.boolean(),all_seeds_required:z.literal(true),cases:z.array(z.object({seed,
   scenario:z.literal('double_cost_stress'),checks:h3Checks,new_explicit_cost:nonnegativeDecimal,control_explicit_cost:nonnegativeDecimal,return_delta:decimal})).length(3)}),
  market_increment:z.object({demonstrated:z.boolean(),cases:z.array(z.object({seed,scenario,best_fixed_action:z.number().int().min(0).max(2),checks:marketChecks})).length(6)}),
  legacy_gate_diagnostics:z.array(z.object({arm:z.literal('D'),seed,scenario,checks:legacyChecks,
   exploratory_conditions_met:z.boolean(),admission_authorized:z.literal(false)})).length(6),not_blind:z.literal(true),...flags,
 }),elapsed_seconds:z.number().finite().nonnegative(),limitations:z.array(z.string().min(1)).min(1),trading_enabled:z.literal(false),...flags,
}).superRefine((report,ctx)=>{
 const fail=(message:string)=>ctx.addIssue({code:'custom',message});
 const key=(row:CostStressCase)=>`${row.arm}-${row.seed??row.fixed_action??'hold'}-${row.scenario}`;
 const expected=['B','C','D','E'].flatMap(arm=>seeds.flatMap(seed=>scenarios.map(cost=>`${arm}-${seed}-${cost}`)));
 expected.push(...[0,1,2].flatMap(action=>scenarios.map(cost=>`fixed-${action}-${cost}`)),...scenarios.map(cost=>`hold-hold-${cost}`));
 const keys=report.results.map(key);
 if(new Set(keys).size!==32||expected.some(key=>!keys.includes(key))){fail('32项结果缺失、重复或身份不完整');return;}
 const indexed=new Map(report.results.map(row=>[key(row),row]));
 const get=(arm:string,seed:number|string,cost:string)=>indexed.get(`${arm}-${seed}-${cost}`)!;
 for(const row of report.results){
  const policy=['B','C','D','E'].includes(row.arm);
  if((policy&&(row.seed===null||row.fixed_action!==undefined))||(!policy&&row.seed!==null)
   ||(row.arm==='hold'&&row.fixed_action!==undefined))fail('模型、种子和固定参照身份不一致');
  if(row.arm==='hold'?row.action_counts!==null:row.action_counts===null||row.action_counts.reduce((a,b)=>a+b,0)<=0)fail('动作统计非法');
  if(row.action_counts){
   const decisions=row.action_counts.reduce((a,b)=>a+b,0);
   if(row.json_actor_parity_steps!==(row.arm==='fixed'?0:decisions))fail('序列化模型的逐步核对数量不一致');
  }
  if(row.effective_start<1704067200000||row.effective_start>=row.effective_end||row.effective_end>1735689600000)fail('结果超出已声明2024探索窗');
  if(!sumEquals([row.metrics.fees,row.metrics.spread_cost,row.metrics.slippage_cost],row.total_explicit_cost))fail('显式成本合计不一致');
 }
 if(!sameCost(report.protocol.configured_costs,configured)||!sameCost(report.protocol.training_costs,pressure))fail('训练或评价成本偏离预注册');
 for(const field of ['baseline_sha256','frozen_code_sha256'] as const){
  if(canonicalCostStressSummary(report.run[field])!==canonicalCostStressSummary(report.protocol[field]))fail('运行与冻结来源摘要不一致');
 }
 if(report.run.dependencies_sha256!==report.protocol.dependencies_sha256)fail('依赖摘要不一致');
 const proof=report.run.prefix_proof;
 if(proof.raw_training_sha256!==proof.raw_exploration_prefix_sha256||proof.scaler_sha256!==report.protocol.baseline_sha256['scaler.json'])fail('原始训练前缀或尺度摘要不一致');
 for(const native of ['B','C'] as const)if(proof.transforms[native].training_sha256!==proof.transforms[native].exploration_prefix_sha256)fail('变换后的训练前缀不一致');
 const controlNames=['B','C'].flatMap(arm=>seeds.map(seed=>`actor-${arm}-seed-${seed}.json`));
 const origins=Object.keys(report.run.control_actor_origins.actor_sha256);
 if(origins.length!==6||controlNames.some(name=>!origins.includes(name)))fail('六个旧控制模型身份缺失或重复');
 const modelKeys=report.model_identities.map(model=>`${model.study_arm}-${model.seed}`);
 if(new Set(modelKeys).size!==6||['D','E'].some(arm=>seeds.some(seed=>!modelKeys.includes(`${arm}-${seed}`))))fail('六个新模型身份缺失或重复');
 for(const model of report.model_identities){
  if(model.native_input_arm!==(model.study_arm==='D'?'B':'C')||model.dataset!==report.protocol.dataset
   ||model.actor_file!==`actor-${model.study_arm}-seed-${model.seed}.json`||model.ppo_file!==`ppo-${model.study_arm}-seed-${model.seed}.zip`
   ||!sameCost(model.training_costs,pressure)||!sameCost(model.evaluation_costs.configured_costs,configured)
   ||!sameCost(model.evaluation_costs.double_cost_stress,pressure))fail('新模型研究身份或成本角色不一致');
 }
 const active=(row:CostStressCase)=>({at_least_two_actions:row.action_counts!==null&&row.action_counts.filter(count=>count>0).length>=2,has_fills:row.metrics.fill_count>0});
 const matches=(actual:Record<string,boolean>,expected:Record<string,boolean>)=>Object.keys(expected).every(name=>actual[name]===expected[name]);
 const all=(checks:Record<string,boolean>)=>Object.values(checks).every(Boolean);
 const h3Seeds=report.conclusion.H3.cases.map(row=>row.seed);
 if(new Set(h3Seeds).size!==3||seeds.some(seed=>!h3Seeds.includes(seed)))fail('H3必须包括全部三个种子');
 for(const row of report.conclusion.H3.cases){
  const candidate=get('D',row.seed,row.scenario),control=get('B',row.seed,row.scenario);
  const checks={explicit_cost_lt_control:compare(candidate.total_explicit_cost,control.total_explicit_cost)<0,
   return_gt_control:compare(candidate.metrics.total_return,control.metrics.total_return)>0,...active(candidate)};
  if(!matches(row.checks,checks)||compare(row.new_explicit_cost,candidate.total_explicit_cost)!==0
   ||compare(row.control_explicit_cost,control.total_explicit_cost)!==0
   ||!sumEquals([row.return_delta,control.metrics.total_return],candidate.metrics.total_return))fail('H3单种子判据或配对差额与实际结果不一致');
 }
 if(report.conclusion.H3.supported!==report.conclusion.H3.cases.every(row=>all(row.checks)))fail('H3总判定掩盖了失败种子');
 const expectedChecks=seeds.flatMap(seed=>scenarios.map(cost=>`${seed}-${cost}`));
 for(const rows of [report.conclusion.market_increment.cases,report.conclusion.legacy_gate_diagnostics]){
  const keys=rows.map(row=>`${row.seed}-${row.scenario}`);
  if(new Set(keys).size!==6||expectedChecks.some(key=>!keys.includes(key)))fail('全部种子和双成本诊断缺失或重复');
 }
 for(const row of report.conclusion.market_increment.cases){
  const candidate=get('D',row.seed,row.scenario),ablated=get('E',row.seed,row.scenario);
  const best=[0,1,2].map(action=>get('fixed',action,row.scenario)).reduce((best,item)=>compare(item.metrics.total_return,best.metrics.total_return)>0?item:best);
  const checks={return_gt_E:compare(candidate.metrics.total_return,ablated.metrics.total_return)>0,
   return_gt_best_fixed:compare(candidate.metrics.total_return,best.metrics.total_return)>0,
   drawdown_le_E:compare(candidate.metrics.max_drawdown,ablated.metrics.max_drawdown)<=0,
   drawdown_le_best_fixed:compare(candidate.metrics.max_drawdown,best.metrics.max_drawdown)<=0};
  if(row.best_fixed_action!==best.fixed_action||!matches(row.checks,checks))fail('市场增益判据与实际结果不一致');
 }
 if(report.conclusion.market_increment.demonstrated!==report.conclusion.market_increment.cases.every(row=>all(row.checks)))fail('市场增益总判定掩盖了失败场景');
 for(const row of report.conclusion.legacy_gate_diagnostics){
  const candidate=get('D',row.seed,row.scenario),hold=get('hold','hold',row.scenario);
  const checks={positive_return:compare(candidate.metrics.total_return,'0')>0,
   drawdown_le_hold:compare(candidate.metrics.max_drawdown,hold.metrics.max_drawdown)<=0,...active(candidate)};
  if(!matches(row.checks,checks)||row.exploratory_conditions_met!==all(checks))fail('原准入探索诊断与实际结果不一致');
 }
});

export type CostStressSummary=z.infer<typeof costStressSummarySchema>;
export type CostStressCase=z.infer<typeof result>;
export function parseCostStressSummary(value:unknown){
 const parsed=costStressSummarySchema.safeParse(value);
 if(!parsed.success)return parsed;
 if(createHash('sha256').update(canonicalCostStressSummary(value)).digest('hex')!==costStressSummarySha256){
  return {success:false as const,error:new z.ZodError([{code:'custom',path:[],message:'公开研究摘要SHA256不一致'}])};
 }
 return parsed;
}
