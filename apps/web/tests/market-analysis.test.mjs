import {test} from 'node:test';
import assert from 'node:assert/strict';
import {summarizeMarket} from '../lib/market-analysis.ts';
import {getCandles,getIndicators,MarketProvider} from '../lib/market.ts';
const identity='bitget:spot:BTC-USDT';
const stamp=value=>new Date(value).toISOString();
function input(){
 const bars=Array.from({length:21},(_,i)=>({instrument_id:identity,source:'bitget.public',timeframe:'1m',open_time:stamp(i*60000),close_time:stamp((i+1)*60000),open:'100',high:'102',low:'99',close:'101',volume:i===20?'40':'20',is_closed:true}));
 const row={open_time:bars.at(-1).open_time,available_at:bars.at(-1).close_time,regime:{label:'TREND_UP',available_at:bars.at(-1).close_time}};
 return {bars,row,now:21*60000+1000};
}
test('closed window and volume exclude current unfinished candle',()=>{
 const {bars,row,now}=input();
 const pending={...bars.at(-1),open_time:stamp(21*60000),close_time:stamp(22*60000),close:'9999',is_closed:false};
 const result=summarizeMarket([...bars,pending],row,'1m',now,identity);
 assert.equal(result.closedCount,21);assert.equal(result.volumeRatio,2);assert.equal(result.high,102);assert.equal(result.low,99);assert.ok(Math.abs(result.change-1)<1e-10);
});
test('stale, gapped, short and misaligned data do not produce analysis',()=>{
 const {bars,row,now}=input();
 assert.throws(()=>summarizeMarket(bars,row,'1m',now+75000,identity),/过期/);
 assert.throws(()=>summarizeMarket(bars.filter((_,i)=>i!==3),row,'1m',now,identity),/不足/);
 assert.throws(()=>summarizeMarket([...bars.slice(0,10),{...bars[10],open_time:stamp(60001)},...bars.slice(11)],row,'1m',now,identity),/不连续/);
 assert.throws(()=>summarizeMarket(bars,{...row,available_at:stamp(1)},'1m',now,identity),/时点不同/);
});
test('zero historical volume is unavailable, not infinity',()=>{
 const {bars,row,now}=input();
 const result=summarizeMarket(bars.map(bar=>({...bar,volume:'0'})),row,'1m',now,identity);
 assert.equal(result.volumeRatio,null);
});
test('mixed source, instrument and regime clocks are rejected',()=>{
 const {bars,row,now}=input();
 assert.throws(()=>summarizeMarket(bars,row,'1m',now,'bitget:spot:ETH-USDT'),/交易对或来源/);
 assert.throws(()=>summarizeMarket(bars.map((bar,i)=>i===3?{...bar,source:'binance.public'}:bar),row,'1m',now,identity),/交易对或来源/);
 assert.throws(()=>summarizeMarket(bars,{...row,regime:{...row.regime,available_at:stamp(0)}},'1m',now,identity),/时点不同/);
});
test('API helpers reject responses for another symbol or indicator configuration',async t=>{
 const signal=new AbortController().signal;
 t.mock.method(globalThis,'fetch',async()=>new Response(JSON.stringify([{...input().bars[0],instrument_id:`${MarketProvider}:spot:ETH-USDT`,source:`${MarketProvider}.public`}]),{status:200}));
 await assert.rejects(getCandles('BTCUSDT','1m',signal),/市场或周期不一致/);
 globalThis.fetch=async()=>new Response(JSON.stringify({symbol:'ETHUSDT',timeframe:'1m',period:20}),{status:200});
 await assert.rejects(getIndicators('BTCUSDT','1m',signal),/市场或周期不一致/);
 globalThis.fetch=async()=>new Response(JSON.stringify({symbol:'BTCUSDT',timeframe:'1m',period:10}),{status:200});
 await assert.rejects(getIndicators('BTCUSDT','1m',signal,120,20),/市场或周期不一致/);
 globalThis.fetch=async()=>new Response(JSON.stringify({schema_version:1,symbol:'BTCUSDT',timeframe:'1m',period:20,closed_only:true,seed_start:null,rows:[]}),{status:200});
 assert.deepEqual((await getIndicators('BTCUSDT','1m',signal)).rows,[]);
});
