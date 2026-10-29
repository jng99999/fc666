import test from 'node:test';
import assert from 'node:assert/strict';
import {createRequire} from 'node:module';
import {fileURLToPath} from 'node:url';
import {build} from 'esbuild';
import {createElement} from 'react';
import {renderToStaticMarkup} from 'react-dom/server';

test('AI research controls wait for hydration and never offer automatic execution',async()=>{
 const output=await build({entryPoints:[fileURLToPath(new URL('../app/research/ai/page.tsx',import.meta.url))],bundle:true,write:false,platform:'node',format:'cjs',jsx:'automatic',packages:'external',external:['react','react/jsx-runtime'],plugins:[{name:'preview-link',setup(b){b.onResolve({filter:/^next\/link$/},()=>({path:'link',namespace:'preview'}));b.onResolve({filter:/\.css$/},()=>({path:'styles',namespace:'preview'}));b.onLoad({filter:/.*/,namespace:'preview'},({path})=>({contents:path==='styles'?'export default {}':"import {createElement} from 'react';export default function Link(props){return createElement('a',props)}",loader:'js'}));}}]});
 const module={exports:{}};new Function('require','module','exports',output.outputFiles[0].text)(createRequire(new URL('../package.json',import.meta.url)),module,module.exports);
 const html=renderToStaticMarkup(createElement(module.exports.default));
 assert.match(html,/<main[^>]*aria-busy="true"/);
 for(const [button] of html.matchAll(/<button\b[^>]*>/g))assert.match(button,/disabled=""/);
 assert.match(html,/保存研究快照/);assert.match(html,/运行 AI 研究/);
 assert.doesNotMatch(html,/提交模拟决策|确认下单|开启实盘/);
 assert.match(html,/href="\/paper\/directional"/);
});
