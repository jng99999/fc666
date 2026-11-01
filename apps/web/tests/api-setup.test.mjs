import test from 'node:test';
import assert from 'node:assert/strict';
import {createRequire} from 'node:module';
import {fileURLToPath} from 'node:url';
import {build} from 'esbuild';
import {createElement} from 'react';
import {renderToStaticMarkup} from 'react-dom/server';

test('API setup checks administrator session before rendering the editor and never serializes credentials or backend URLs',async()=>{
 const output=await build({entryPoints:[fileURLToPath(new URL('../app/system/api/page.tsx',import.meta.url))],bundle:true,write:false,platform:'node',format:'cjs',jsx:'automatic',packages:'external',external:['react','react/jsx-runtime'],plugins:[{name:'preview-link',setup(builder){builder.onResolve({filter:/^next\/link$/},()=>({path:'link',namespace:'preview'}));builder.onResolve({filter:/\.css$/},()=>({path:'styles',namespace:'preview'}));builder.onLoad({filter:/.*/,namespace:'preview'},({path})=>({contents:path==='styles'?'export default {}':"import {createElement} from 'react';export default function Link(props){return createElement('a',props)}",loader:'js'}));}}]});
 const module={exports:{}};new Function('require','module','exports',output.outputFiles[0].text)(createRequire(new URL('../package.json',import.meta.url)),module,module.exports);
 const fixtures={FC666_TA_API_KEY:'SECRET_DO_NOT_RENDER_MODEL',DIRECTIONAL_NEWS_TOKEN:'SECRET_DO_NOT_RENDER_NEWS',FC666_GOLD_HISTORY_TOKEN:'SECRET_DO_NOT_RENDER_GOLD',API_BASE_URL:'https://private-backend.example',DIRECTIONAL_API_BASE_URL:'https://private-directional.example'};
 const original=Object.fromEntries(Object.keys(fixtures).map(key=>[key,process.env[key]]));
 try{
  Object.assign(process.env,fixtures);
  const html=renderToStaticMarkup(createElement(module.exports.default));
  for(const value of Object.values(fixtures))assert(!html.includes(value),'server credentials and configured backend URLs must not enter the client render');
  assert.match(html,/API 接入/);assert.match(html,/research-worker\.env/);assert.match(html,/directional-ai\.env/);
  assert.match(html,/href="\/research\/ai"/);assert.match(html,/href="\/paper\/directional"/);
  assert.match(html,/vercel\.com\/jng56\/fc666-test\/settings\/environment-variables/);
  assert.doesNotMatch(html,/<(?:input|form)\b/,'server render cannot display a configuration editor before session verification');
  assert.match(html,/正在检查管理员会话/);assert.match(html,/未确认登录前不读取或显示配置/);
  for(const [button] of html.matchAll(/<button\b[^>]*>/g))assert.match(button,/disabled=""/,'client-only actions wait for hydration');
 }finally{for(const [key,value]of Object.entries(original)){if(value===undefined)delete process.env[key];else process.env[key]=value;}}
});
