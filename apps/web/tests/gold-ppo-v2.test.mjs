import {test} from 'node:test';
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {createRequire} from 'node:module';
import {fileURLToPath} from 'node:url';
import {build} from 'esbuild';
import {renderToStaticMarkup} from 'react-dom/server';
import {createElement} from 'react';

const raw=await readFile(new URL('../data/ppo-v2-study.json',import.meta.url),'utf8');
const actual=JSON.parse(raw);
async function render(study=actual){
 const built=await build({entryPoints:[fileURLToPath(new URL('../app/gold/ppo/v2/page.tsx',import.meta.url))],bundle:true,write:false,platform:'node',format:'cjs',jsx:'automatic',packages:'external',external:['react','react/jsx-runtime'],loader:{'.css':'empty'},plugins:[{name:'study-fixture',setup(b){
  b.onResolve({filter:/ppo-v2-study\.json$/},()=>({path:'study',namespace:'fixture'}));
  b.onLoad({filter:/.*/,namespace:'fixture'},()=>({contents:JSON.stringify(study),loader:'json'}));
  b.onResolve({filter:/^next\/link$/},()=>({path:'link',namespace:'link'}));
  b.onLoad({filter:/.*/,namespace:'link'},()=>({contents:"import {createElement} from 'react';export default function Link(props){return createElement('a',props)}",loader:'js'}));
 }}]});
 const module={exports:{}};
 new Function('require','module','exports',built.outputFiles[0].text)(createRequire(new URL('../package.json',import.meta.url)),module,module.exports);
 return renderToStaticMarkup(createElement(module.exports.default));
}

test('PPO v2 download is byte-identical to displayed data and retains both complete 13-case cost groups',async()=>{
 assert.equal(await readFile(new URL('../public/research/ppo-v2-study.json',import.meta.url),'utf8'),raw);
 assert.equal(actual.results.length,26);
 for(const scenario of ['configured_costs','double_cost_stress']){
  const rows=actual.results.filter(row=>row.scenario===scenario);
  assert.equal(rows.length,13);
  for(const arm of ['A','B','C'])assert.deepEqual(rows.filter(row=>row.arm===arm).map(row=>row.seed).sort((a,b)=>a-b),[7,19,42]);
  assert.deepEqual(rows.filter(row=>row.arm==='fixed').map(row=>row.fixed_action),[0,1,2]);
  assert.equal(rows.filter(row=>row.arm==='hold').length,1);
 }
 assert.equal(actual.protocol.timesteps_per_run,131072);
 assert.equal(actual.protocol.scaler.samples,5038);
 assert.equal(actual.protocol.validation_bars,5917);
 assert.equal(actual.results.find(row=>row.arm==='B'&&row.seed===19&&row.scenario==='double_cost_stress').metrics.total_return,'-0.012951409');
});

test('PPO v2 publishes failed hypotheses and exact provenance without machine paths or promotion',()=>{
 assert.equal(actual.conclusion.H1.supported,false);
 assert.equal(actual.conclusion.H2_market_increment.demonstrated,false);
 assert.equal(actual.conclusion.H1.median_return_delta,'-0.006204929');
 assert.equal(actual.candidate_eligible,false);
 assert.equal(actual.trading_enabled,false);
 assert.equal(actual.conclusion.promotion_authorized,false);
 assert.ok(actual.results.every(row=>row.trading_enabled===false));
 assert.ok(actual.numeric_audit.results.every(row=>row.candidate_eligible===false));
 assert.equal(actual.provenance.find(row=>row.path==='ppo-v2-frozen/summary.json').sha256,'e3a68deaa7f31506349ef31dcd7829a21acd355b5270942b6c9f8d7360185ab4');
 assert.equal(actual.provenance.find(row=>row.path==='ppo-v2-deployment-f64-complete/summary.json').sha256,'5d1a661276bff35127156808ea907e0c204468151d5d38f0546528213bbd8522');
 for(const source of actual.provenance){assert.match(source.sha256,/^[a-f0-9]{64}$/);assert.doesNotMatch(source.path,/^\/|\.\./);}
 assert.doesNotMatch(raw,/\/Users\/|\/tmp\/|api[_-]?key|cookie|token/i);
});

test('PPO v2 displays every seed, negative cost result, static baselines and no write controls',async()=>{
 const html=await render();
 assert.equal((html.match(/data-result-key=/g)??[]).length,26);
 for(const text of ['H1 未获支持','H2 未证明行情增量','-0.6205 pp','-1.30%','12.96%','固定 25% 目标','固定 50% 目标','50% 买入持有参照','种子 7','种子 19','种子 42','106,506','有限轨迹一致','首次运行曾真实触发推理超时','格式与数值兼容不能代替收益准入','href="/gold/automation"','download="ppo-v2-study.json"'])assert.ok(html.includes(text),text);
 assert.doesNotMatch(html,/<button|<form|\/Users\//);
});

test('PPO v2 rejects missing or duplicated cases instead of publishing a selected winner',async()=>{
 for(const mutate of [
  data=>data.results.pop(),
  data=>{data.results[1]=structuredClone(data.results[0]);},
  data=>{data.results[0].seed=null;},
  data=>{data.numeric_audit.results[1]=structuredClone(data.numeric_audit.results[0]);},
 ]){
  const data=structuredClone(actual);mutate(data);const html=await render(data);
  assert.ok(html.includes('未通过完整性格式校验'));assert.doesNotMatch(html,/data-result-key=|下载全部对照摘要/);
 }
});

test('PPO v2 fails closed for enabled candidates, invalid money and inconsistent numeric audit',async()=>{
 for(const mutate of [
  data=>{data.candidate_eligible=true;},
  data=>{data.trading_enabled=true;},
  data=>{data.conclusion.promotion_authorized=true;},
  data=>{data.conclusion.H1.supported=true;},
  data=>{data.numeric_audit.results[0].candidate_eligible=true;},
  data=>{data.results[0].metrics.total_return='NaN';},
  data=>{data.results[0].action_counts=[1,2];},
  data=>{data.numeric_audit.inference_steps++;},
  data=>{data.numeric_audit.all_native_metrics_equal=false;},
 ]){
  const data=structuredClone(actual);mutate(data);const html=await render(data);
  assert.ok(html.includes('未通过完整性格式校验'));assert.doesNotMatch(html,/data-result-key=|下载全部对照摘要/);
 }
});

test('PPO v2 numeric summary preserves finite-trajectory scope and all 18 candidates',()=>{
 const audit=actual.numeric_audit;
 assert.equal(audit.results.length,18);
 assert.equal(audit.inference_steps,106506);
 assert.equal(audit.native_action_differences,0);
 assert.equal(audit.numeric_argmax_differences,0);
 assert.equal(audit.low_margin_steps,0);
 assert.equal(audit.inference_timeouts,0);
 assert.equal(audit.all_native_metrics_equal,true);
 assert.equal(audit.cross_platform_bitwise_identity_claimed,false);
 assert.equal(audit.minimum_logit_margin,0.00000949426286306343);
 assert.ok(audit.first_run.outcome.includes('INFERENCE_TIMEOUT'));
 assert.notEqual(audit.first_run.path,'ppo-v2-deployment-f64-complete/');
});

test('PPO v2 real reproduction artifact binds all nine models, full results and frozen experiment inputs',()=>{
 const proof=actual.reproduction;
 assert.equal(proof.version,'gold-ppo-v2-independent-reproduction-v1');
 assert.equal(proof.scope,'same_cpu_and_frozen_dependencies');
 assert.equal(proof.original_source_commit,'5fc021ea1820b42c36ac2639594773157c684f10');
 assert.equal(proof.reproduction_source_commit,'5c0a33138eae518887b2d4cb8698e2f4d21a8319');
 assert.equal(proof.dataset,actual.protocol.dataset);
 assert.equal(proof.dataset,'ee9afe981db266959ebcc1557e6120ae6e60012f761265ed46d57ab57878276c');
 assert.equal(proof.timesteps_per_model,131072);assert.equal(proof.timesteps_per_model,actual.protocol.timesteps_per_run);
 assert.deepEqual(proof.seeds,[7,19,42]);
 assert.equal(proof.dependencies_sha256,'7849050d054806d56cef63365786028e9b1f140bb6d175dc31fe04ff819ff203');
 assert.deepEqual(proof.protocol_differences,['created_at','source_commit']);
 assert.equal(proof.model_count,9);assert.equal(proof.models.length,9);
 for(const field of ['actor_json_equal','policy_state_equal','optimizer_state_equal','pytorch_variables_equal'])assert.equal(proof[field],9,field);
 assert.equal(proof.result_count,26);assert.equal(proof.result_count,actual.results.length);
 assert.equal(proof.all_results_equal,true);assert.equal(proof.all_conclusions_equal,true);
 assert.equal(proof.source_files_equal,6);
 assert.equal(proof.promotion_authorized,false);assert.equal(proof.trading_enabled,false);
 assert.equal(proof.native_zip_containers_equal,0);
 assert.equal(proof.models.filter(row=>row.native_zip_bytes_equal).length,proof.native_zip_containers_equal);
 for(const arm of ['A','B','C'])assert.deepEqual(proof.models.filter(row=>row.arm===arm).map(row=>row.seed).sort((a,b)=>a-b),[7,19,42]);
 for(const model of proof.models)for(const field of ['actor_sha256','policy_state_sha256','optimizer_state_sha256','pytorch_variables_sha256'])assert.match(model[field],/^[a-f0-9]{64}$/,`${model.arm}-${model.seed}:${field}`);
 assert.equal(proof.models.find(row=>row.arm==='A'&&row.seed===7).actor_sha256,'74ca43c3679ce078f254c553fc5f8e0d5c39da9cc1f34b5c8aeb7765f66b441b');
 assert.equal(proof.original_manifest_sha256,actual.provenance.find(row=>row.path==='ppo-v2-frozen/manifest.json').sha256);
 assert.equal(proof.reproduction_manifest_sha256,'f07df075eb0c685979c6c54ef064413a194124c649b4649a77b25d188cdaa2bb');
});

test('PPO v2 renders independent reproduction scope without turning reproducibility into admission',async()=>{
 const html=await render();
 for(const text of ['独立复现：9 个模型与 26 项结果一致','纯 JSON actor 逐字节一致','策略与 critic 参数载荷一致',
  '优化器参数载荷一致','完整含成本结果一致','当前 CPU 与相同冻结依赖环境','不承诺跨设备训练权重逐字节相同',
  '保存时间、运行耗时和对象序列化元数据的差异已保留','复现的准入结论仍是失败','模型未注册 / 未启用',
  'H1 未获支持','H2 未证明行情增量'])assert.ok(html.includes(text),text);
 for(const model of actual.reproduction.models)assert.ok(html.includes(model.actor_sha256),`${model.arm}-${model.seed}`);
 assert.ok(html.includes(actual.reproduction.dependencies_sha256));
 assert.ok(html.includes(actual.reproduction.original_manifest_sha256));
 assert.ok(html.includes(actual.reproduction.reproduction_manifest_sha256));
 assert.equal((html.match(/data-result-key=/g)??[]).length,26);
 assert.doesNotMatch(html,/<button|<form|\/Users\//);
});

test('PPO v2 reproduction fails closed for missing or duplicate models and mismatched dataset or budget',async()=>{
 const mutations=[
  ['missing proof',data=>{delete data.reproduction;}],
  ['missing model',data=>{data.reproduction.models.pop();}],
  ['duplicate model',data=>{data.reproduction.models[1]=structuredClone(data.reproduction.models[0]);}],
  ['unexpected seed',data=>{data.reproduction.models[0].seed=999;}],
  ['seed order',data=>{data.reproduction.seeds=[42,19,7];}],
  ['different dataset',data=>{data.reproduction.dataset='0'.repeat(64);}],
  ['invalid dataset digest',data=>{data.reproduction.dataset='invalid';}],
  ['different budget',data=>{data.reproduction.timesteps_per_model=131073;}],
  ['protocol budget mismatch',data=>{data.protocol.timesteps_per_run=131073;}],
  ['both budgets changed',data=>{data.protocol.timesteps_per_run=131073;data.reproduction.timesteps_per_model=131073;}],
 ];
 for(const [label,mutate] of mutations){
  const data=structuredClone(actual);mutate(data);const html=await render(data);
  assert.ok(html.includes('未通过完整性格式校验'),label);
  assert.doesNotMatch(html,/data-result-key=|下载全部对照摘要|独立复现：9 个模型与 26 项结果一致/,label);
 }
});

test('PPO v2 reproduction never accepts partial equal counts, failed conclusions or live promotion',async()=>{
 const mutations=[
  ...['model_count','actor_json_equal','policy_state_equal','optimizer_state_equal','pytorch_variables_equal'].map(field=>[field,data=>{data.reproduction[field]=8;}]),
  ['result count',data=>{data.reproduction.result_count=25;}],
  ['source count',data=>{data.reproduction.source_files_equal=5;}],
  ['results differ',data=>{data.reproduction.all_results_equal=false;}],
  ['conclusions differ',data=>{data.reproduction.all_conclusions_equal=false;}],
  ['promotion enabled',data=>{data.reproduction.promotion_authorized=true;}],
  ['live trading enabled',data=>{data.reproduction.trading_enabled=true;}],
  ['invalid model hash',data=>{data.reproduction.models[0].policy_state_sha256='invalid';}],
 ];
 for(const [label,mutate] of mutations){
  const data=structuredClone(actual);mutate(data);const html=await render(data);
  assert.ok(html.includes('未通过完整性格式校验'),label);
  assert.doesNotMatch(html,/data-result-key=|下载全部对照摘要|独立复现：9 个模型与 26 项结果一致/,label);
 }
});

test('PPO v2 reproduction rejects ZIP totals that disagree with per-model byte comparisons',async()=>{
 for(const [label,mutate] of [
  ['false positive total',data=>{data.reproduction.native_zip_containers_equal=1;}],
  ['unaccounted equal model',data=>{data.reproduction.models[0].native_zip_bytes_equal=true;}],
  ['out of range',data=>{data.reproduction.native_zip_containers_equal=10;}],
  ['negative total',data=>{data.reproduction.native_zip_containers_equal=-1;}],
 ]){
  const data=structuredClone(actual);mutate(data);const html=await render(data);
  assert.ok(html.includes('未通过完整性格式校验'),label);
  assert.doesNotMatch(html,/data-result-key=|下载全部对照摘要|独立复现：9 个模型与 26 项结果一致/,label);
 }
});
