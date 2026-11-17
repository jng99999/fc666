import test from 'node:test';
import assert from 'node:assert/strict';
import {directionalCreateBody,DirectionalAccount,DirectionalPriceAnalysis} from '../lib/directional.ts';

const id='11111111-1111-4111-8111-111111111111',time='2026-11-15T00:00:00Z';
const fields={instrument:'BTCUSDT',strategy:'PRICE_ACTION',initial_cash:'10000',tick_size:'0.01',quantity_step:'0.001',max_quantity:'10',max_order_quote:'5000',fee_rate:'0.001',slippage:'0.05',news_pre_seconds:'1800',news_post_seconds:'1800',quantity:'1',stop_loss_percent:'0.01',take_profit_percent:'0.02',rule:'STRUCTURE_FVG',timeframe:'15m'};
const analysis={rule_version:'price-action-v1',state:'WARMUP',samples:12,required:64,target:null,available_at:time,reason:'CLOSED_HISTORY_WARMUP',evidence:{ema20:null,market:{instrument:'BTCUSDT',source:'bitget.public',timeframe:'15m'}},decision_status:'NO_SIGNAL',evaluated_at:time,source:'bitget.public',bar_sha256:'a'.repeat(64)};
function account(strategy='PRICE_ACTION'){return {account_id:id,revision:1,status:'RUNNING',created_at:time,updated_at:time,mode:'RESEARCH_PAPER',trading_enabled:false,config:{instrument:'BTCUSDT',quote_source:'bitget.public:spot:orderbook:step0',initial_cash:'10000',tick_size:'0.01',quantity_step:'0.001',max_quantity:'10',max_order_quote:'5000',fee_rate:'0.001',slippage:'0.05',news_pre_seconds:1800,news_post_seconds:1800},metadata:{strategy,...(strategy==='PRICE_ACTION'?{rule:'STRUCTURE_FVG',timeframe:'15m',quantity:'1',stop_loss_percent:'0.01',take_profit_percent:'0.02'}:{})},balance:'10000',equity:'10000',fees:'0',gross_pnl:'0',unrealized_pnl:'0',entry_halted:false,position:null,pending:null,fills:[],fills_count:0,fills_truncated:false,events:0,journal_sha256:null,last_quote:null,entry_gate:'NEWS_UNAVAILABLE',recent_quotes:[],last_outcome:null};}

test('price action creates only explicit approved settings, never client candles/news/clocks',()=>{
 const payload=directionalCreateBody({...fields,bar:'untrusted',news:'untrusted',available_at:time},id);
 assert.equal(payload.strategy,'PRICE_ACTION');assert.equal(payload.rule,'STRUCTURE_FVG');assert.equal(payload.timeframe,'15m');assert.equal(payload.quantity,'1');assert.equal(payload.news_pre_seconds,1800);
 for(const key of ['bar','news','available_at'])assert.equal(key in payload,false);
});
test('legacy manual/SMA/AI bodies retain their old field shape without rule/timeframe',()=>{
 for(const strategy of ['MANUAL','SMA20_QUOTES','AI_RESEARCH']){const payload=directionalCreateBody({...fields,strategy},id);assert.equal('rule'in payload,false);assert.equal('timeframe'in payload,false);if(strategy==='MANUAL'){assert.equal(payload.quantity,null);assert.equal(payload.stop_loss_percent,null);}else if(strategy==='SMA20_QUOTES')assert.equal(payload.quantity,'1');else for(const key of ['quantity','stop_loss_percent','take_profit_percent'])assert.equal(key in payload,false);}
});
test('gold permits only real 1m quote sampling and checks quantity limits',()=>{
 assert.equal(directionalCreateBody({...fields,instrument:'XAU/USD',timeframe:'1m'},id).timeframe,'1m');
 assert.throws(()=>directionalCreateBody({...fields,instrument:'XAU/USD'},id),/1m/);
 assert.throws(()=>directionalCreateBody({...fields,instrument:'XAU/USD',timeframe:'1m',max_quantity:'101'},id),/100/);
});
test('automatic quantity uses exact decimal step multiples instead of floating point modulo',()=>{
 assert.equal(directionalCreateBody({...fields,quantity:'0.3',quantity_step:'0.1'},id).quantity,'0.3');
 assert.equal(directionalCreateBody({...fields,quantity:'1e-3'},id).quantity,'1e-3');
 assert.throws(()=>directionalCreateBody({...fields,quantity:'0.30000000000000004',quantity_step:'0.1'},id),/整数倍/);
 assert.throws(()=>directionalCreateBody({...fields,quantity:'11'},id),/最大数量/);
});
test('bad fields, bounds, protective ratios and unsupported rules refuse creation locally',()=>{
 for(const patch of [{quantity:''},{quantity:'NaN'},{quantity:'Infinity'},{fee_rate:'-1'},{stop_loss_percent:'0'},{take_profit_percent:'1'},{news_pre_seconds:'1.5'},{news_post_seconds:'86401'},{max_order_quote:'10001'},{rule:'UNKNOWN'},{timeframe:'2h'},{instrument:'XAUUSDT'}])assert.throws(()=>directionalCreateBody({...fields,...patch},id));
});
test('new server analysis retains its evidence, checkpoint and frozen strategy metadata',()=>{
 const parsed=DirectionalAccount.parse({...account(),analysis});
 assert.equal(parsed.metadata.rule,'STRUCTURE_FVG');assert.deepEqual(parsed.analysis,analysis);
 assert.equal(DirectionalPriceAnalysis.safeParse({...analysis,target:'FLAT'}).success,false);
 assert.equal(DirectionalPriceAnalysis.safeParse({...analysis,evaluated_at:'bad'}).success,false);
 assert.equal(DirectionalPriceAnalysis.safeParse({...analysis,state:'FAKE_SUCCESS'}).success,false);
});
test('manual, SMA and AI account reads remain compatible with absent/null/legacy analysis',()=>{
 for(const strategy of ['MANUAL','SMA20_QUOTES','AI_RESEARCH']){assert.equal(DirectionalAccount.safeParse(account(strategy)).success,true);assert.equal(DirectionalAccount.safeParse({...account(strategy),analysis:null}).success,true);}
 const legacy={state:'READY',samples:20,required:20,target:'FLAT',sma:'100',available_at:time,sample_type:'OBSERVED_QUOTES_NOT_CANDLES'};
 assert.deepEqual(DirectionalAccount.parse({...account('SMA20_QUOTES'),analysis:legacy}).analysis,legacy);
});
