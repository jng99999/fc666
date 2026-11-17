import test from 'node:test';
import assert from 'node:assert/strict';
import {build} from 'esbuild';
import {createRequire} from 'node:module';
import {fileURLToPath} from 'node:url';
import {createElement} from 'react';
import {renderToStaticMarkup} from 'react-dom/server';

test('scene server preview prevents edits before browser selection and form handlers are ready',async()=>{
 const output=await build({entryPoints:[fileURLToPath(new URL('../app/portfolio/history/page.tsx',import.meta.url))],bundle:true,write:false,platform:'node',format:'cjs',jsx:'automatic',packages:'external',external:['react','react/jsx-runtime'],loader:{'.css':'empty'},plugins:[{name:'scene-server-preview',setup(b){
  b.onResolve({filter:/^next\/link$/},()=>({path:'link',namespace:'preview'}));
  b.onLoad({filter:/.*/,namespace:'preview'},()=>({contents:"import {createElement} from 'react';export default function Link(props){return createElement('a',props)}",loader:'js'}));
 }}]});
 const module={exports:{}};
 new Function('require','module','exports',output.outputFiles[0].text)(createRequire(new URL('../package.json',import.meta.url)),module,module.exports);
 const html=renderToStaticMarkup(createElement(module.exports.default));
 assert.match(html,/<main[^>]*aria-busy="true"/);
 assert.match(html,/role="status">正在恢复账户选择与场景表单…/);
 for(const label of ['场景名称','历史总敞口阈值','历史单币种阈值']){
  const input=[...html.matchAll(/<input\b[^>]*>/g)].find(([tag])=>tag.includes(`aria-label="${label}"`))?.[0];
  assert.ok(input,`${label} remains in the server preview`);
  assert.match(input,/\bdisabled=""/,`${label} must not accept an edit that hydration would discard`);
 }
 assert.match(html,/<button\b[^>]*disabled=""[^>]*>冻结并保存场景<\/button>/);
 assert.match(html,/href="\/portfolio"/,'the recovery navigation remains usable without hydration');
});
