import {test} from 'node:test';
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {createHash} from 'node:crypto';
import {gunzipSync} from 'node:zlib';
import {createRequire} from 'node:module';
import {fileURLToPath} from 'node:url';
import {build} from 'esbuild';
import {renderToStaticMarkup} from 'react-dom/server';
import {createElement} from 'react';

const require=createRequire(new URL('../package.json',import.meta.url));
const bytes=await readFile(new URL('../public/research/ppo-v2-own-trajectories.json',import.meta.url));
const report=JSON.parse(bytes.toString('utf8'));
const digest=value=>createHash('sha256').update(value).digest('hex');
const compiled=await build({entryPoints:[fileURLToPath(new URL('../lib/gold-ppo-trajectories.ts',import.meta.url))],bundle:true,write:false,platform:'node',format:'cjs',packages:'external'});
const library={exports:{}};
new Function('require','module','exports',compiled.outputFiles[0].text)(require,library,library.exports);
const {parseTrajectoryReport,trajectoryReportSha256}=library.exports;

async function render(data=report){
 const built=await build({entryPoints:[fileURLToPath(new URL('../app/gold/ppo/v2/OwnTrajectoryEvidence.tsx',import.meta.url))],bundle:true,write:false,platform:'node',format:'cjs',jsx:'automatic',packages:'external',plugins:[{name:'own-trajectory-fixture',setup(builder){
  builder.onResolve({filter:/ppo-v2-own-trajectories\.json$/},()=>({path:'report',namespace:'fixture'}));
  builder.onLoad({filter:/.*/,namespace:'fixture'},()=>({contents:JSON.stringify(data),loader:'json'}));
 }}]});
 const component={exports:{}};
 new Function('require','module','exports',built.outputFiles[0].text)(require,component,component.exports);
 return renderToStaticMarkup(createElement(component.exports.default));
}

test('Linux own-trajectory public snapshot is bound to complete raw bytes and independent proof',async()=>{
 assert.equal(digest(bytes),trajectoryReportSha256);
 assert.equal(parseTrajectoryReport(report).success,true);
 const compressed=await readFile(new URL('../public/research/ppo-v2-own-trajectories.jsonl.gz',import.meta.url));
 const raw=gunzipSync(compressed);
 assert.equal(compressed.length,report.raw_download.bytes);
 assert.equal(digest(compressed),report.raw_download.sha256);
 assert.equal(compressed.readUInt32LE(4),0);
 assert.equal(raw.length,report.evidence.results_bytes);
 assert.equal(digest(raw),report.evidence.results_sha256);
 assert.equal(digest(raw),report.raw_download.uncompressed_sha256);
 const lines=raw.toString('utf8').trimEnd().split('\n');
 assert.equal(lines.length,28);
 const end=JSON.parse(lines.at(-1));
 assert.equal(digest(lines.slice(0,-1).join('\n')+'\n'),end.jsonl_prior_sha256);
 const proofBytes=await readFile(new URL('../public/research/ppo-v2-own-trajectory-validation.json',import.meta.url));
 assert.equal(digest(proofBytes),report.independent_validation.report_sha256);
 const proof=JSON.parse(proofBytes.toString('utf8'));
 assert.equal(proof.validation_passed,true);
 assert.equal(proof.results_sha256,digest(raw));
 assert.equal(proof.unique_cases,26);
 assert.equal(proof.actual_refusals,0);
 assert.equal(proof.nonzero_inventory_refusals_verified,0);
});

test('all 26 complete financial comparisons and both cost groups remain visible without qualification',()=>{
 assert.equal(report.cases.length,26);
 assert.deepEqual(report.run.steps,{fixed:35502,hold:11834,policy:106506});
 for(const scenario of ['configured_costs','double_cost_stress'])assert.equal(report.cases.filter(row=>row.scenario===scenario).length,13);
 for(const row of report.cases){
  for(const name of ['native','f64'])assert.deepEqual(row.references[name].equal,{equity:true,fills:true,metrics:true});
  assert.equal(row.market_steps,5917);
  assert.equal(row.refusal_count,0);
  assert.equal(row.equity_marks,row.kind==='hold'?248:5917);
 }
 assert.equal(report.cases.find(row=>row.id==='B-19-double_cost_stress').metrics.total_return,'-0.012951409');
 for(const key of ['candidate_eligible','promotion_authorized','trading_enabled','online_verified','forward_verified','source_transfer_verified','profitability_admitted','bitwise_equality_is_admission_gate'])assert.equal(report[key],false);
 assert.equal(report.run.dependencies.pydantic,'2.13.5');
 assert.equal(report.run.local_frozen_dependencies.pydantic,'2.12.3');
 assert.doesNotMatch(bytes.toString('utf8'),/\/Users\/|dataset_gzip_base64|audit_source_utf8|"layers"|"weight"|api_key|cookie/);
});

test('server evidence section renders compact all-case details, honest limits and explicit downloads only',async()=>{
 const html=await render();
 assert.equal((html.match(/data-own-trajectory-case=/g)??[]).length,26);
 for(const text of ['Linux 独立资金轨迹','106,506','35,502','11,834','非在线、非真实前向','本次实际拒绝为0','逐位向量一致不是原收益准入门槛','9.7 MB','-1.30%','Pydantic 2.13.5','收益准入仍然失败','运行耗时 / 进程峰 RSS','peak 用自身原始 bid 估值','压力场景的财务 mark 扩大点差'])assert.ok(html.includes(text),text);
 for(const filename of ['ppo-v2-own-trajectories.json','ppo-v2-own-trajectory-validation.json','ppo-v2-own-trajectories.jsonl.gz'])assert.ok(html.includes(`download="${filename}"`));
 assert.doesNotMatch(html,/<button|<form|<script|\/Users\//);
});

test('missing cases, identity/cost/hash/metric edits and enabled flags fail closed',()=>{
 for(const mutate of [
  data=>{data.cases.pop();},data=>{data.cases[1]=structuredClone(data.cases[0]);},
  data=>{data.cases[0].kind='hold';},data=>{data.cases[0].costs.slippage='0';},
  data=>{data.cases[0].metrics.net_pnl='999';},data=>{data.cases[0].hashes.equity='0'.repeat(64);},
  data=>{data.cases[0].references.native.equal.fills=false;},data=>{data.run.steps.policy++;},
  data=>{data.run.refusals=1;},data=>{data.independent_validation.verified=false;},
  data=>{data.independent_validation.nonzero_inventory_refusals_verified=1;},
  data=>{data.raw_download.uncompressed_sha256='0'.repeat(64);},
  data=>{data.candidate_eligible=true;},data=>{data.promotion_authorized=true;},data=>{data.trading_enabled=true;},
  data=>{data.online_verified=true;},data=>{data.isolation.network='bridge';},
 ]){
  const data=structuredClone(report);mutate(data);assert.equal(parseTrajectoryReport(data).success,false);
 }
});

test('invalid evidence renders an explicit error without metrics or download links',async()=>{
 const data=structuredClone(report);data.trading_enabled=true;
 const html=await render(data);
 assert.ok(html.includes('未通过完整性、摘要或关闭状态校验'));
 assert.doesNotMatch(html,/data-own-trajectory-case=|download=|106,506/);
});

test('new own-trajectory evidence preserves both original fixed-input reports byte for byte',async()=>{
 for(const [filename,expected] of [['ppo-v2-container-audit.json','8c191c783e5c2417b2ab9098386b26c91cfa1236421b395dc05fef036f22428f'],['ppo-v2-container-diagnostic.json','6ae9531a04f2e437e066e7992dddafed64f72377a5f51ab8a118f0b03f087849']]){
  const raw=await readFile(new URL(`../public/research/${filename}`,import.meta.url));
  assert.equal(digest(raw),expected);
  assert.equal(JSON.parse(raw.toString('utf8')).fixed_inputs_verified,false);
 }
 const page=await readFile(new URL('../app/gold/ppo/v2/page.tsx',import.meta.url),'utf8');
 assert.ok(page.includes('该轮未验证，见下方独立资金轨迹记录'));
 assert.equal((page.match(/<OwnTrajectoryEvidence\/>/g)??[]).length,1);
});
