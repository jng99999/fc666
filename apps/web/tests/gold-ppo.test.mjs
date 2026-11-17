import {test} from 'node:test';
import assert from 'node:assert/strict';
import {createRequire} from 'node:module';
import {fileURLToPath} from 'node:url';
import {build} from 'esbuild';
import {renderToStaticMarkup} from 'react-dom/server';
import {createElement} from 'react';

const fixture = {
 version:'ppo-test-v1',generated_at:'2026-10-22T00:00:00Z',
 dataset:{id:'a'.repeat(64),source:'Test paired quotes',start:'2023-01-01',end:'2026-01-01',bars:16940},
 protocol:{train_start:'2023-01-01',train_end:'2024-01-01',validation_end:'2025-01-01',evaluation_end:'2026-01-01',seeds:[7,19,42],timesteps:100,actions:['空仓','25%权益','50%权益'],costs:{initial_cash:'10000',allocation:'.5',commission:'.1',slippage:'.05',spread_multiplier:'1'},not_blind:true},
 gate:{eligible:false,reasons:['扣费后未达到冻结门槛'],live_trading:false},
 results:[{strategy:'ppo_target_v1',seed:7,window:'evaluation',scenario:'configured_costs',metrics:{total_return:'-.036727432',max_drawdown:'.1837',fees:'417.498',spread_cost:'912.11963',slippage_cost:'208.749',fill_count:2084},action_counts:[10,20,30]}],
 limitations:['历史源与实时源不同，不能保证成交。'],artifacts_sha256:{'model-7':'b'.repeat(64)},
};

async function render(study) {
 const output=await build({entryPoints:[fileURLToPath(new URL('../app/gold/ppo/page.tsx',import.meta.url))],bundle:true,write:false,platform:'node',format:'cjs',jsx:'automatic',packages:'external',external:['react','react/jsx-runtime'],loader:{'.css':'empty'},plugins:[{name:'study-fixture',setup(b){
  b.onResolve({filter:/ppo-study\.json$/},()=>({path:'study',namespace:'fixture'}));
  b.onLoad({filter:/.*/,namespace:'fixture'},()=>({contents:JSON.stringify(study),loader:'json'}));
  b.onResolve({filter:/^next\/link$/},()=>({path:'link',namespace:'link'}));
  b.onLoad({filter:/.*/,namespace:'link'},()=>({contents:"import {createElement} from 'react';export default function Link(props){return createElement('a',props)}",loader:'js'}));
 }}]});
 const module={exports:{}};
 new Function('require','module','exports',output.outputFiles[0].text)(createRequire(new URL('../package.json',import.meta.url)),module,module.exports);
 return renderToStaticMarkup(createElement(module.exports.default));
}

test('PPO summary preserves negative percentages, costs and per-seed target allocation meaning',async()=>{
 const html=await render(fixture);
 assert.ok(html.includes('-3.67%'));assert.ok(html.includes('18.37%'));
 for(const text of ['417.50','912.12 / 208.75','2,084','种子 7','25%权益 · 20','非盲测历史评估','不是每次追加买入','扣费后未达到冻结门槛'])assert.ok(html.includes(text),text);
 assert.ok(html.includes('href="/research/ppo-study.json"'));assert.ok(html.includes('download="ppo-study.json"'));
 assert.doesNotMatch(html,/<button|<form/);
});

test('PPO missing result windows are explicit and have no fabricated zero returns',async()=>{
 const html=await render({...fixture,results:[]});
 assert.equal((html.match(/此窗口尚无该成本场景的结果/g)??[]).length,4);
 assert.doesNotMatch(html,/0\.00%/);
});

test('PPO summary rejects a live-trading flag or invalid metric before rendering results',async()=>{
 for(const study of [{...fixture,gate:{...fixture.gate,live_trading:true}},{...fixture,results:[{...fixture.results[0],metrics:{...fixture.results[0].metrics,total_return:'NaN'}}]}]){
  const html=await render(study);assert.ok(html.includes('未通过完整性格式校验'));assert.doesNotMatch(html,/-3\.67%|下载公开研究摘要/);
 }
});

test('PPO malformed action counts never acquire misleading action labels',async()=>{
 const html=await render({...fixture,results:[{...fixture.results[0],action_counts:[1,2]}],limitations:['<script>unsafe</script>']});
 assert.ok(html.includes('动作计数与定义不匹配'));assert.doesNotMatch(html,/空仓 · 1/);
 assert.ok(html.includes('&lt;script&gt;unsafe&lt;/script&gt;'));assert.doesNotMatch(html,/<script>unsafe/);
});
