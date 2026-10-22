import {test} from 'node:test';
import assert from 'node:assert/strict';
import {createRequire} from 'node:module';
import {fileURLToPath} from 'node:url';
import {build} from 'esbuild';
import {createElement} from 'react';
import {renderToStaticMarkup} from 'react-dom/server';

test('analysis server preview disables native controls until event handlers can be ready',async()=>{
 const output=await build({entryPoints:[fileURLToPath(new URL('../app/analysis/page.tsx',import.meta.url))],bundle:true,write:false,platform:'node',format:'cjs',jsx:'automatic',packages:'external',external:['react','react/jsx-runtime'],loader:{'.css':'empty'},plugins:[{name:'server-preview',setup(b){
  b.onResolve({filter:/^next\/link$/},()=>({path:'link',namespace:'preview'}));
  b.onResolve({filter:/^\.\/analysis-chart$/},()=>({path:'chart',namespace:'preview'}));
  b.onLoad({filter:/.*/,namespace:'preview'},({path})=>({contents:path==='link'?"import {createElement} from 'react';export default function Link(props){return createElement('a',props)}":"export default function Chart(){return null}",loader:'js'}));
 }}]});
 const module={exports:{}};
 new Function('require','module','exports',output.outputFiles[0].text)(createRequire(new URL('../package.json',import.meta.url)),module,module.exports);
 const html=renderToStaticMarkup(createElement(module.exports.default));
 assert.match(html,/<main[^>]*aria-busy="true"/);
 assert.ok(html.includes('正在准备分析控件…'));
 const controls=[...html.matchAll(/<(?:button|select)\b([^>]*)>/g)];
 assert.ok(controls.length>=20,'all analysis control groups must be present in the preview');
 for(const [tag] of controls)assert.match(tag,/\bdisabled=""/,tag);
 assert.match(html,/href="\/research\?symbol=BTCUSDT"/,'native navigation remains available');
});
