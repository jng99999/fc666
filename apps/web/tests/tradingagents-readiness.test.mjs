import test from 'node:test';
import assert from 'node:assert/strict';
import {createRequire} from 'node:module';
import {fileURLToPath} from 'node:url';
import {build} from 'esbuild';
import {createElement} from 'react';
import {renderToStaticMarkup} from 'react-dom/server';

test('AI research controls wait for hydration and never offer live execution',async()=>{
 const output=await build({entryPoints:[fileURLToPath(new URL('../app/research/ai/page.tsx',import.meta.url))],bundle:true,write:false,platform:'node',format:'cjs',jsx:'automatic',packages:'external',external:['react','react/jsx-runtime'],plugins:[{name:'preview-link',setup(b){b.onResolve({filter:/^next\/link$/},()=>({path:'link',namespace:'preview'}));b.onResolve({filter:/\.css$/},()=>({path:'styles',namespace:'preview'}));b.onLoad({filter:/.*/,namespace:'preview'},({path})=>({contents:path==='styles'?'export default {}':"import {createElement} from 'react';export default function Link(props){return createElement('a',props)}",loader:'js'}));}}]});
 const module={exports:{}};new Function('require','module','exports',output.outputFiles[0].text)(createRequire(new URL('../package.json',import.meta.url)),module,module.exports);
 const html=renderToStaticMarkup(createElement(module.exports.default));
 assert.match(html,/<main[^>]*aria-busy="true"/);
 for(const [button] of html.matchAll(/<button\b[^>]*>/g))assert.match(button,/disabled=""/);
 assert.match(html,/保存研究快照/);assert.match(html,/运行 AI 研究/);
 assert.doesNotMatch(html,/提交模拟决策|确认下单|开启实盘/);
 assert.match(html,/href="\/paper\/directional"/);
});

test('research boundary accepts real readiness, AUTO jobs and correlated paper receipts while rejecting live mode',async()=>{
 const output=await build({entryPoints:[fileURLToPath(new URL('../lib/tradingagents.ts',import.meta.url))],bundle:true,write:false,platform:'node',format:'cjs',packages:'external'});
 const module={exports:{}};new Function('require','module','exports',output.outputFiles[0].text)(createRequire(new URL('../package.json',import.meta.url)),module,module.exports);
 const {ResearchStatus,ResearchAutomation,ResearchJob,ResearchMarketEvidence,automationReason}=module.exports;
 const uuid='12345678-1234-4234-8234-123456789012',at='2026-11-02T10:00:00Z';
 const status={framework:'TradingAgents',upstream_commit:'c4544913a14ed3df5f1ed7b9e8483270ce20682a',runner_configured:true,model_configured:false,automation_supported:true,state:'MODEL_UNAVAILABLE',mode:'SHADOW_ONLY',automatic_execution:false,trading_enabled:false};
 assert.equal(ResearchStatus.parse(status).model_configured,false,'connection configuration is distinct from model readiness');
 assert.equal(ResearchStatus.safeParse({...status,trading_enabled:true}).success,false);
 const receipt={job_id:uuid,request_id:'ta:auto:'+uuid,reason:'ACCEPTED',status:'ACCEPTED',at,account_revision:8,fills_count:0};
 const control={account_id:uuid,enabled:true,epoch:3,interval_seconds:300,next_due:at,last_job:uuid,state:'WAIT_QUOTE',receipt,trading_enabled:false};
 assert.deepEqual(ResearchAutomation.parse(control).receipt,receipt);
 assert.equal(ResearchAutomation.parse({...control,receipt:{...receipt,status:'FILLED'}}).receipt.status,'FILLED');
 for(const patch of [{interval_seconds:299},{epoch:-1},{trading_enabled:true},{receipt:{...receipt,status:'SUCCESS'}}])assert.equal(ResearchAutomation.safeParse({...control,...patch}).success,false);
 const market={status:'UNAVAILABLE',reason:'HISTORICAL_CANDLES_UNAVAILABLE',bars:[],indicators:null,quote_source:'swissquote',candle_source:null,detail:'Same-source history unavailable'};
 assert.equal(ResearchMarketEvidence.parse(market).detail,market.detail,'source provenance must survive JSON downloads');
 const config={instrument:'XAU/USD',quote_source:'swissquote',initial_cash:'10000',tick_size:'0.01',quantity_step:'0.001',max_quantity:'10',max_order_quote:'5000',fee_rate:'0.001',slippage:'0.05',news_pre_seconds:1800,news_post_seconds:1800};
 const snapshot={sha256:'a'.repeat(64),snapshot:{version:'fc666-research-snapshot-v1',account_id:uuid,revision:8,at,valid_until:at,config,quote:null,recent_quotes:[],sample_type:'OBSERVED_QUOTES_NOT_CANDLES',entry_gate:'NEWS_UNAVAILABLE',unavailable:['social_sentiment'],market_evidence:market,trading_enabled:false}};
 assert.equal(ResearchJob.parse({job_id:uuid,account_id:uuid,mode:'AUTO',state:'QUEUED',snapshot,result:null,created_at:at,finished_at:null,trading_enabled:false}).mode,'AUTO');
 assert.match(automationReason('HISTORICAL_CANDLES_UNAVAILABLE'),/黄金历史行情尚未接入，暂停新开仓/);
 assert.match(automationReason('HISTORY_UNAVAILABLE'),/历史行情尚未就绪/);
});
