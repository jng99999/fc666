import {test} from 'node:test';
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {createHash} from 'node:crypto';
import {createRequire} from 'node:module';
import {fileURLToPath} from 'node:url';
import {build} from 'esbuild';
import {renderToStaticMarkup} from 'react-dom/server';
import {createElement} from 'react';

const raw=await readFile(new URL('../data/ppo-v2-study.json',import.meta.url),'utf8');
const actual=JSON.parse(raw);
const containerReportRaw=await readFile(new URL('../public/research/ppo-v2-container-audit.json',import.meta.url));
const containerReport=JSON.parse(containerReportRaw.toString('utf8'));
const containerDiagnosticRaw=await readFile(new URL('../public/research/ppo-v2-container-diagnostic.json',import.meta.url));
const containerDiagnostic=JSON.parse(containerDiagnosticRaw.toString('utf8'));
async function render(study=actual,diagnostic=containerDiagnostic){
 const built=await build({entryPoints:[fileURLToPath(new URL('../app/gold/ppo/v2/page.tsx',import.meta.url))],bundle:true,write:false,platform:'node',format:'cjs',jsx:'automatic',packages:'external',external:['react','react/jsx-runtime'],loader:{'.css':'empty'},plugins:[{name:'study-fixture',setup(b){
  b.onResolve({filter:/ppo-v2-study\.json$/},()=>({path:'study',namespace:'fixture'}));
  b.onLoad({filter:/.*/,namespace:'fixture'},()=>({contents:JSON.stringify(study),loader:'json'}));
  b.onResolve({filter:/ppo-v2-container-diagnostic\.json$/},()=>({path:'diagnostic',namespace:'diagnostic'}));
  b.onLoad({filter:/.*/,namespace:'diagnostic'},()=>({contents:JSON.stringify(diagnostic),loader:'json'}));
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

test('PPO v2 first container failure preserves full report bytes, exact counters and source identity',()=>{
 const audit=actual.container_audit;
 const digest=createHash('sha256').update(containerReportRaw).digest('hex');
 assert.equal(digest,'8c191c783e5c2417b2ab9098386b26c91cfa1236421b395dc05fef036f22428f');
 assert.equal(containerReportRaw.length,10142);
 assert.equal(audit.report_sha256,digest);
 assert.equal(audit.report_path,'/research/ppo-v2-container-audit.json');
 assert.equal(actual.provenance.find(row=>row.path==='container-oracle/remote-oracle-result.json').sha256,digest);
 assert.equal(audit.status,'FAILED');assert.equal(audit.exit_code,2);assert.equal(audit.fixed_inputs_verified,false);
 assert.equal(audit.steps,106506);assert.equal(audit.cases.length,18);
 assert.equal(audit.cases.reduce((sum,row)=>sum+row.steps,0),audit.steps);
 assert.deepEqual(audit.cases,containerReport.cases);
 for(const [key,value] of Object.entries({action_differences:10,refusal_differences:10,raw_difference_steps:3996,preprocess_difference_steps:2646,peak_difference_steps:0,timeouts:10,low_margin_refusals:0,raw_max_abs_difference:2.220446049250313e-16,preprocessed_max_abs_difference:6.661338147750939e-16})){
  assert.equal(audit[key],value,key);assert.equal(audit[key],containerReport[key],key);
 }
 assert.equal(audit.deadline_ms,100);assert.equal(audit.strict_float_equality,true);
 assert.equal(audit.first_differences_recorded,30);assert.equal(audit.recorded_timeout_examples,0);
 assert.equal(containerReport.first_differences.length,30);
 assert.ok(containerReport.first_differences.every(row=>row.actual_refusal===null));
 for(const field of ['runtime_source_sha256','runtime_helpers_ast_sha256','validator_sha256','uncompressed_input_sha256'])assert.equal(audit[field],containerReport[field],field);
 for(const field of ['candidate_eligible','all_numeric_argmax_equal_claimed','container_trade_metrics_equal_claimed','promotion_authorized','trading_enabled'])assert.equal(audit[field],false,field);
 assert.equal(containerReport.no_database_or_network_operations,true);assert.equal(containerReport.no_account_or_trade_execution,true);
 assert.doesNotMatch(containerReportRaw.toString('utf8'),/\/Users\/|\/tmp\/|api[_-]?key|password|cookie|token|account_id|session_id|Bearer/i);
});

test('PPO v2 distinguishes failed Linux container replay from successful Mac history without claiming trade or timeout causality',async()=>{
 const html=await render();
 for(const text of ['首轮容器实测未通过','Mac 同机 f64 数值对照：有限轨迹一致','Linux amd64 部署镜像','候选继续禁用','3,996','2,646','2.22044605e-16','6.66133815e-16','不放宽浮点容差或 100ms 预算来放行','不能据此证明它们逐条对应','未记录超时逐条明细','首轮记录不能证明首轮数值 argmax 全部相同','没有执行容器交易或计算容器成交、收益轨迹','download="ppo-v2-container-audit.json"',actual.container_audit.report_sha256])assert.ok(html.includes(text),text);
 assert.equal((html.match(/data-result-key=/g)??[]).length,26);
 assert.doesNotMatch(html,/<button|<form|容器实测通过|容器收益一致|动作差异全部由超时/);
});

test('PPO v2 container summary rejects counter, difference magnitude or timing inconsistent with immutable report',async()=>{
 const mutations=[
  data=>{data.container_audit.steps++;},
  data=>{data.container_audit.cases[0].steps--;},
  ...['raw_difference_steps','preprocess_difference_steps','action_differences','refusal_differences','timeouts'].map(key=>data=>{data.container_audit[key]--;}),
  data=>{data.container_audit.peak_difference_steps=1;},
  data=>{data.container_audit.low_margin_refusals=1;},
  data=>{data.container_audit.raw_difference_steps=data.container_audit.steps+1;},
  data=>{data.container_audit.raw_max_abs_difference=0;},
  data=>{data.container_audit.preprocessed_max_abs_difference=0;},
  data=>{data.container_audit.inference_timing.maximum_ms=99;},
  data=>{data.container_audit.first_differences_recorded=29;},
  data=>{data.container_audit.recorded_timeout_examples=10;},
 ];
 for(const mutate of mutations){const data=structuredClone(actual);mutate(data);const html=await render(data);assert.ok(html.includes('未通过完整性格式校验'));assert.doesNotMatch(html,/data-result-key=|下载首轮容器完整失败报告/);}
});

test('PPO v2 container failed evidence never accepts promoted flags, relaxed deadline or tolerance',async()=>{
 const mutations=[
  ...['fixed_inputs_verified','candidate_eligible','all_numeric_argmax_equal_claimed','container_trade_metrics_equal_claimed','promotion_authorized','trading_enabled'].map(key=>data=>{data.container_audit[key]=true;}),
  data=>{data.container_audit.no_database_or_network_operations=false;},
  data=>{data.container_audit.no_account_or_trade_execution=false;},
  data=>{data.container_audit.status='PASSED';},
  data=>{data.container_audit.exit_code=0;},
  data=>{data.container_audit.deadline_ms=101;},
  data=>{data.container_audit.strict_float_equality=false;},
  data=>{data.container_audit.approved=true;},
 ];
 for(const mutate of mutations){const data=structuredClone(actual);mutate(data);const html=await render(data);assert.ok(html.includes('未通过完整性格式校验'));assert.doesNotMatch(html,/data-result-key=|下载首轮容器完整失败报告/);}
});

test('PPO v2 container evidence requires all scenarios and valid, matching report hash',async()=>{
 const mutations=[
  data=>{delete data.container_audit;},
  data=>{data.container_audit.cases.pop();},
  data=>{data.container_audit.cases[1]=structuredClone(data.container_audit.cases[0]);},
  data=>{data.container_audit.cases[0].id='A-99-configured_costs';},
  data=>{data.container_audit.report_sha256='invalid';},
  data=>{data.container_audit.report_sha256='0'.repeat(64);},
  data=>{data.container_audit.report_path='/private/report.json';},
 ];
 for(const mutate of mutations){const data=structuredClone(actual);mutate(data);const html=await render(data);assert.ok(html.includes('未通过完整性格式校验'));assert.doesNotMatch(html,/data-result-key=|下载首轮容器完整失败报告/);}
});

test('PPO v2 independent container diagnostic preserves the failed gate and original first-run evidence',()=>{
 const digest=createHash('sha256').update(containerDiagnosticRaw).digest('hex');
 assert.equal(digest,'6ae9531a04f2e437e066e7992dddafed64f72377a5f51ab8a118f0b03f087849');
 assert.equal(containerDiagnosticRaw.length,23888);
 assert.equal(actual.provenance.find(row=>row.path==='container-oracle/run-03/remote-result.json').sha256,digest);
 assert.equal(containerDiagnostic.fixed_inputs_verified,false);assert.equal(containerDiagnostic.trading_enabled,false);assert.equal(containerDiagnostic.promotion_authorized,false);
 assert.equal(containerDiagnostic.steps,106506);assert.equal(containerDiagnostic.action_differences,0);assert.equal(containerDiagnostic.timeouts,0);
 assert.equal(containerDiagnostic.raw_difference_steps,3996);assert.equal(containerDiagnostic.preprocess_difference_steps,2646);
 assert.equal(containerDiagnostic.primary_and_numeric_cross_counts.numeric_actions_resolved,106506);
 assert.equal(containerDiagnostic.primary_and_numeric_cross_counts.numeric_actions_unresolved,0);
 assert.equal(containerDiagnostic.primary_and_numeric_cross_counts.numeric_diagnostic_action_differences,0);
 assert.equal(containerDiagnostic.refused_step_unbounded_numeric_diagnostic.attempts,0);
 assert.equal(containerDiagnostic.cases.length,18);
 assert.equal(containerDiagnostic.cases.reduce((total,row)=>total+row.numeric_actions_resolved,0),106506);
 assert.equal(containerDiagnostic.uncompressed_input_sha256,actual.container_audit.uncompressed_input_sha256);
 assert.equal(containerDiagnostic.runtime_source_sha256,actual.container_audit.runtime_source_sha256);
 assert.equal(createHash('sha256').update(containerReportRaw).digest('hex'),'8c191c783e5c2417b2ab9098386b26c91cfa1236421b395dc05fef036f22428f');
 assert.equal(createHash('sha256').update(JSON.stringify(actual.container_audit)).digest('hex'),'84893d3ce790bf2984592dae033a8245fb48f4f3396f741a67e0e30e248dc863');
 assert.doesNotMatch(containerDiagnosticRaw.toString('utf8'),/\/Users\/|\/tmp\/|api[_-]?key|password|cookie|token|account_id|session_id|Bearer/i);
});

test('PPO v2 keeps the new diagnostic inside first-run details and gives both reports shared touch links',async()=>{
 const html=await render();
 for(const text of ['后续独立诊断','全量数值动作差异 0','旁路尝试 0','严格核对仍未通过','新运行不能重建首轮 10 条超时明细或解释首轮原因','不覆盖首轮失败','首轮记录不能证明首轮数值 argmax 全部相同','download="ppo-v2-container-diagnostic.json"'])assert.ok(html.includes(text),text);
 const detail=html.slice(html.indexOf('<details>'),html.indexOf('</details>')+10);
 assert.ok(detail.includes('<h3>后续独立诊断</h3>'));
 assert.match(detail,/<div class="gold-ppo-links"><a class="secondary"[^>]+download="ppo-v2-container-audit\.json"/);
 assert.match(detail,/<a class="secondary"[^>]+download="ppo-v2-container-diagnostic\.json"/);
 assert.doesNotMatch(html,/<h2>后续独立诊断|容器实测通过|容器收益一致|<button|<form/);
});

test('PPO v2 suppresses invalid independent diagnostic claims while retaining first failure and original results',async()=>{
 for(const mutate of [
  data=>{data.fixed_inputs_verified=true;},
  data=>{data.trading_enabled=true;},
  data=>{data.promotion_authorized=true;},
  data=>{data.no_account_or_trade_execution=false;},
  data=>{data.steps=106505;},
  data=>{data.primary_and_numeric_cross_counts.numeric_actions_resolved=106505;},
 ]){
  const diagnostic=structuredClone(containerDiagnostic);mutate(diagnostic);const html=await render(actual,diagnostic);
  assert.ok(html.includes('后续独立诊断报告未通过格式与关闭状态校验'));
  assert.ok(html.includes('首轮容器实测未通过'));
  assert.equal((html.match(/data-result-key=/g)??[]).length,26);
  assert.doesNotMatch(html,/另一次相同固定输入诊断共/);
 }
});
