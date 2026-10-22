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
