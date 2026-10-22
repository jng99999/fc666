import {test} from 'node:test';
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {createHash} from 'node:crypto';
import {createRequire} from 'node:module';
import {fileURLToPath} from 'node:url';
import {build} from 'esbuild';
import {createElement} from 'react';
import {renderToStaticMarkup} from 'react-dom/server';

// SYNTHETIC FIXTURES ONLY. No file under public/research is read, generated or published here.
const require=createRequire(new URL('../package.json',import.meta.url));
const protocol=JSON.parse(await readFile(new URL('../../../docs/research/ppo-cost-stress-protocol.json',import.meta.url),'utf8'));
const digest=value=>createHash('sha256').update(value).digest('hex');
const canonical=value=>value===null||typeof value!=='object'?JSON.stringify(value):Array.isArray(value)?`[${value.map(canonical).join(',')}]`:`{${Object.entries(value).sort(([a],[b])=>a<b?-1:a>b?1:0).map(([key,item])=>`${JSON.stringify(key)}:${canonical(item)}`).join(',')}}`;
const seeds=[7,19,42],scenarios=['configured_costs','double_cost_stress'];
const configured={initial_cash:'10000',allocation:'.5',commission:'.10',slippage:'.05',spread_multiplier:'1'};
const pressure={...configured,commission:'.20',slippage:'.10',spread_multiplier:'2'};
const protocolSha='3dfc0621c4fb0bda412b42c5d5670b7294fbd395371bef03c9bf25d46c017763';
function fixture(){
 const results=[];
 function row(arm,seed,scenario,fixed_action){
  const policy=['B','C','D','E'].includes(arm);
  const gain=arm==='D'?'.04':arm==='B'?'.02':arm==='C'||arm==='E'?'.01':arm==='hold'?'.03':['0','.02','.03'][fixed_action];
  const dd=arm==='D'?'.02':arm==='B'?'.05':arm==='C'||arm==='E'?'.03':arm==='hold'?'.04':['0','.03','.04'][fixed_action];
  const fee=arm==='D'?'200':arm==='B'?'300':arm==='C'||arm==='E'?'250':arm==='hold'?'1':String(fixed_action);
  const counts=arm==='hold'?null:policy?[40,30,30]:[0,1,2].map(action=>action===fixed_action?100:0);
  return {arm,seed,scenario,...(arm==='fixed'?{fixed_action}:{}),window:'validation_2024',trading_enabled:false,
   effective_start:1704067200000,effective_end:1735689600000,action_counts:counts,
   ...(arm==='hold'?{}:{json_actor_parity_steps:policy?100:0}),total_explicit_cost:fee,
   metrics:{total_return:gain,max_drawdown:dd,final_equity:String(10000*(1+Number(gain))),net_pnl:String(10000*Number(gain)),
    realized_pnl:'0',open_ounces:'0',fees:fee,spread_cost:'0',slippage_cost:'0',fill_count:policy?10:arm==='fixed'&&fixed_action===0?0:1,expired_gap_intents:0}};
 }
 for(const scenario of scenarios){
  for(const arm of ['B','C','D','E'])for(const seed of seeds)results.push(row(arm,seed,scenario));
  for(const action of [0,1,2])results.push(row('fixed',null,scenario,action));
  results.push(row('hold',null,scenario));
 }
 const models=['D','E'].flatMap(arm=>seeds.map(seed=>({study_version:protocol.study_version,study_arm:arm,native_input_arm:arm==='D'?'B':'C',seed,
  dataset:protocol.dataset,protocol_sha256:protocolSha,training_costs:pressure,evaluation_costs:{configured_costs:configured,double_cost_stress:pressure},
  timesteps:131072,actor_file:`actor-${arm}-seed-${seed}.json`,actor_sha256:digest(`synthetic-actor-${arm}-${seed}`),
  ppo_file:`ppo-${arm}-seed-${seed}.zip`,ppo_sha256:digest(`synthetic-zip-${arm}-${seed}`),deployment_evidence_inherited:false,promotion_authorized:false,live_trading:false})));
 const transform={training_shape:[200,7],dtype:'float32',training_sha256:'a'.repeat(64),exploration_prefix_sha256:'a'.repeat(64),equal:true};
 const data={study_version:protocol.study_version,protocol,run:{protocol_sha256:protocolSha,runner_sha256:'b'.repeat(64),source_commit:'c'.repeat(40),
  dependencies_sha256:protocol.dependencies_sha256,baseline_sha256:protocol.baseline_sha256,frozen_code_sha256:protocol.frozen_code_sha256,
  prefix_proof:{raw_equal:true,raw_training_sha256:'d'.repeat(64),raw_exploration_prefix_sha256:'d'.repeat(64),transforms:{B:transform,C:transform},scaler_sha256:protocol.baseline_sha256['scaler.json']},
  control_actor_origins:{original_manifest_sha256:'e'.repeat(64),actor_sha256:Object.fromEntries(['B','C'].flatMap(arm=>seeds.map(seed=>[`actor-${arm}-seed-${seed}.json`,digest(`synthetic-control-${arm}-${seed}`)]))),loaded_pickle_or_executed_controls:false,new_model_deployment_evidence:false},promotion_authorized:false,live_trading:false},
  model_identities:models,results,elapsed_seconds:1,limitations:['SYNTHETIC FIXTURE ONLY. Not real training, performance or qualification.'],promotion_authorized:false,live_trading:false,trading_enabled:false};
 data.conclusion=conclusion(data);
 return structuredClone(data);
}
function conclusion(data){
 const get=(arm,seed,scenario)=>data.results.find(row=>row.arm===arm&&row.seed===seed&&row.scenario===scenario);
 const active=row=>({at_least_two_actions:row.action_counts.filter(count=>count>0).length>=2,has_fills:row.metrics.fill_count>0});
 const h3=seeds.map(seed=>{const candidate=get('D',seed,scenarios[1]),control=get('B',seed,scenarios[1]);return {seed,scenario:scenarios[1],new_explicit_cost:candidate.total_explicit_cost,control_explicit_cost:control.total_explicit_cost,
  return_delta:String(Number(candidate.metrics.total_return)-Number(control.metrics.total_return)),checks:{explicit_cost_lt_control:Number(candidate.total_explicit_cost)<Number(control.total_explicit_cost),return_gt_control:Number(candidate.metrics.total_return)>Number(control.metrics.total_return),...active(candidate)}};});
 const market=[],legacy=[];
 for(const scenario of scenarios)for(const seed of seeds){
  const candidate=get('D',seed,scenario),ablated=get('E',seed,scenario),hold=get('hold',null,scenario);
  const best=data.results.filter(row=>row.arm==='fixed'&&row.scenario===scenario).reduce((a,b)=>Number(b.metrics.total_return)>Number(a.metrics.total_return)?b:a);
  const checks={return_gt_E:Number(candidate.metrics.total_return)>Number(ablated.metrics.total_return),return_gt_best_fixed:Number(candidate.metrics.total_return)>Number(best.metrics.total_return),
   drawdown_le_E:Number(candidate.metrics.max_drawdown)<=Number(ablated.metrics.max_drawdown),drawdown_le_best_fixed:Number(candidate.metrics.max_drawdown)<=Number(best.metrics.max_drawdown)};
  market.push({seed,scenario,best_fixed_action:best.fixed_action,checks});
  const gates={positive_return:Number(candidate.metrics.total_return)>0,drawdown_le_hold:Number(candidate.metrics.max_drawdown)<=Number(hold.metrics.max_drawdown),...active(candidate)};
  legacy.push({arm:'D',seed,scenario,checks:gates,exploratory_conditions_met:Object.values(gates).every(Boolean),admission_authorized:false});
 }
 return {H3:{supported:h3.every(row=>Object.values(row.checks).every(Boolean)),all_seeds_required:true,cases:h3},market_increment:{demonstrated:market.every(row=>Object.values(row.checks).every(Boolean)),cases:market},legacy_gate_diagnostics:legacy,not_blind:true,promotion_authorized:false,live_trading:false};
}

async function compile(entry,data,{expected=digest(canonical(data))}={}){
 const compiled=await build({entryPoints:[fileURLToPath(entry)],bundle:true,write:false,platform:'node',format:'cjs',jsx:'automatic',packages:'external',plugins:[{name:'explicit-synthetic-cost-study',setup(builder){
  builder.onLoad({filter:/gold-ppo-cost-stress\.ts$/},async args=>({contents:(await readFile(args.path,'utf8')).replace(/export const costStressSummarySha256='[^']*';/,`export const costStressSummarySha256='${expected}';`),loader:'ts'}));
  builder.onResolve({filter:/ppo-training-cost-summary\.json$/},()=>({path:'synthetic-only',namespace:'synthetic-cost-study'}));
  builder.onLoad({filter:/.*/,namespace:'synthetic-cost-study'},()=>({contents:JSON.stringify(data),loader:'json'}));
  builder.onResolve({filter:/\.css$/},()=>({path:'empty-css',namespace:'synthetic-css'}));
  builder.onLoad({filter:/.*/,namespace:'synthetic-css'},()=>({contents:'',loader:'js'}));
 }}]});
 const module={exports:{}};
 new Function('require','module','exports',compiled.outputFiles[0].text)(require,module,module.exports);
 return module.exports;
}
const sample=fixture();
const library=await compile(new URL('../lib/gold-ppo-cost-stress.ts',import.meta.url),sample);
const valid=data=>library.costStressSummarySchema.safeParse(data).success;

test('synthetic-only fixture covers all 32 unique cases and six explicit identities',()=>{
 assert.equal(valid(sample),true);
 assert.equal(library.parseCostStressSummary(sample).success,true);
 assert.equal(sample.results.length,32);
 assert.equal(sample.model_identities.length,6);
 for(const scenario of scenarios)assert.equal(sample.results.filter(row=>row.scenario===scenario).length,16);
 assert.equal(library.canonicalCostStressSummary(sample),canonical(sample));
});

test('missing, repeated, wrong-source, invalid-number and enabled synthetic reports fail closed',()=>{
 for(const mutate of [
  data=>{data.results.pop();},data=>{data.results[1]=structuredClone(data.results[0]);},data=>{data.results[0].seed=null;},
  data=>{data.results[0].metrics.total_return='NaN';},data=>{data.results[0].metrics.total_return='1e9999999';},
  data=>{data.results[0].metrics.total_return='1e-9999999';},data=>{data.results[0].metrics.fees='-1';},
  data=>{data.results[0].metrics.max_drawdown='1.1';},data=>{data.results[0].action_counts[0]=-1;},
  data=>{data.results[0].action_counts[0]++;},data=>{data.results[0].total_explicit_cost='999';},
  data=>{data.results[0].effective_end=1767225600000;},data=>{data.model_identities[1]=structuredClone(data.model_identities[0]);},
  data=>{data.model_identities[0].native_input_arm='C';},data=>{data.model_identities[0].training_costs.commission='.10';},
  data=>{data.model_identities[0].evaluation_costs.double_cost_stress.spread_multiplier='4';},
  data=>{data.model_identities[0].deployment_evidence_inherited=true;},data=>{data.run.dependencies_sha256='f'.repeat(64);},
  data=>{data.run.prefix_proof.raw_exploration_prefix_sha256='f'.repeat(64);},
  data=>{delete data.run.control_actor_origins.actor_sha256['actor-B-seed-7.json'];},
  data=>{data.conclusion.H3.cases[1]=structuredClone(data.conclusion.H3.cases[0]);},
  data=>{data.conclusion.market_increment.cases[1]=structuredClone(data.conclusion.market_increment.cases[0]);},
  data=>{data.conclusion.legacy_gate_diagnostics[1]=structuredClone(data.conclusion.legacy_gate_diagnostics[0]);},
  data=>{data.conclusion.legacy_gate_diagnostics[0].admission_authorized=true;},
  data=>{data.promotion_authorized=true;},data=>{data.live_trading=true;},data=>{data.trading_enabled=true;},
  data=>{data.run.promotion_authorized=true;},data=>{data.conclusion.live_trading=true;},
 ]){
  const data=structuredClone(sample);mutate(data);assert.equal(valid(data),false,mutate.toString());
 }
});

test('one failed seed cannot be hidden by an aggregate success or an empty cash policy',()=>{
 const data=fixture(),row=data.results.find(row=>row.arm==='D'&&row.seed===42&&row.scenario===scenarios[1]);
 row.metrics.fees='400';row.total_explicit_cost='400';data.conclusion=conclusion(data);
 assert.equal(data.conclusion.H3.supported,false);assert.equal(valid(data),true);
 data.conclusion.H3.supported=true;assert.equal(valid(data),false);
 const cash=fixture(),candidate=cash.results.find(row=>row.arm==='D'&&row.seed===19&&row.scenario===scenarios[1]);
 candidate.action_counts=[100,0,0];candidate.metrics.fill_count=0;candidate.metrics.fees='0';candidate.total_explicit_cost='0';cash.conclusion=conclusion(cash);
 assert.equal(cash.conclusion.H3.supported,false);assert.equal(valid(cash),true);
 cash.conclusion.H3.cases.find(row=>row.seed===19).checks.at_least_two_actions=true;assert.equal(valid(cash),false);
});

test('individual checks, totals and exact decimal cost comparisons are recomputed',()=>{
 for(const mutate of [
  data=>{data.conclusion.H3.cases[0].checks.return_gt_control=false;},
  data=>{data.conclusion.H3.cases[0].return_delta='.021';},
  data=>{data.conclusion.market_increment.cases[0].checks.drawdown_le_E=false;},
  data=>{data.conclusion.market_increment.demonstrated=false;},
  data=>{data.conclusion.legacy_gate_diagnostics[0].checks.positive_return=false;},
  data=>{data.conclusion.legacy_gate_diagnostics[0].exploratory_conditions_met=false;},
 ]){const data=fixture();mutate(data);assert.equal(valid(data),false);}
 const data=fixture(),candidate=data.results.find(row=>row.arm==='D'&&row.seed===7&&row.scenario===scenarios[1]);
 candidate.metrics.fees='300.0000000000000000000000000001';candidate.total_explicit_cost=candidate.metrics.fees;
 assert.equal(valid(data),false,'binary float rounding must not preserve a lower-cost check');
});

test('a structurally valid snapshot with altered bytes fails its published digest',()=>{
 const data=structuredClone(sample);data.limitations.push('SYNTHETIC altered bytes');
 assert.equal(valid(data),true);
 assert.equal(library.parseCostStressSummary(data).success,false);
});

test('synthetic page renders all seeds, all result cards, limits and JSON download without execution controls',async()=>{
 const page=await compile(new URL('../app/gold/ppo/cost-stress/page.tsx',import.meta.url),sample);
 const html=renderToStaticMarkup(createElement(page.default));
 assert.equal((html.match(/data-cost-result=/g)??[]).length,32);
 assert.equal((html.match(/data-h3-seed=/g)??[]).length,3);
 assert.equal((html.match(/data-cost-diagnostic=/g)??[]).length,6);
 for(const text of ['2024 已被查看','未注册','实盘关闭','尚未验证','32 项','2× / 原 1× 与 2×','至少两种动作','没有旧模型的 f64'])assert.ok(html.includes(text),text);
 assert.match(html,/download="ppo-training-cost-summary.json"/);
 assert.doesNotMatch(html,/<button|<form|<script|\.zip"|\/Users\//);
});

test('damaged or enabled synthetic page shows an error with no returns or downloads',async()=>{
 for(const data of [({...sample,promotion_authorized:true}),({...sample,elapsed_seconds:2})]){
  const page=await compile(new URL('../app/gold/ppo/cost-stress/page.tsx',import.meta.url),data,{expected:digest(canonical(sample))});
  const html=renderToStaticMarkup(createElement(page.default));
  assert.ok(html.includes('未通过完整性、判据、关闭状态或 SHA256 校验'));
  assert.doesNotMatch(html,/data-cost-result=|data-h3-seed=|download=|4\.00%/);
 }
});
