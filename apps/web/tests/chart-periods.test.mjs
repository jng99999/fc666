import test from 'node:test';
import assert from 'node:assert/strict';
import {build} from 'esbuild';
import {createRequire} from 'node:module';
import {fileURLToPath} from 'node:url';
const output=await build({entryPoints:[fileURLToPath(new URL('../lib/chart-market.ts',import.meta.url)),fileURLToPath(new URL('../lib/chart-periods.ts',import.meta.url))],bundle:true,write:false,outdir:'out',platform:'node',format:'cjs',packages:'external',define:{'process.env.NEXT_PUBLIC_MARKET_PROVIDER':'"bitget"'}});
const modules=output.outputFiles.map(file=>{const module={exports:{}};new Function('require','module','exports',file.text)(createRequire(new URL('../package.json',import.meta.url)),module,module.exports);return module.exports;});
const {loadChartMarket}=modules[0],{aggregateChartCandles,chartPeriodMs,chartBasePeriod,chartIndicatorRows}=modules[1];
const stamp=time=>new Date(time).toISOString();
const bars=(count=360)=>Array.from({length:count},(_,i)=>({instrument_id:'bitget:spot:BTC-USDT',source:'bitget.public',timeframe:'1m',open_time:stamp(i*60000),close_time:stamp((i+1)*60000),open:String(100+i),high:String(103+i),low:String(99+i),close:String(102+i),volume:'2',is_closed:true}));

test('minute/hour periods validate bounds and select an exact-divisor native source',()=>{
 for(const period of ['1m','2m','3m','5m','7m','59m','60m','1h','2h','3h','7h','24h','1d'])assert.ok(chartPeriodMs(period)>0);
 for(const period of ['0m','-1m','1.5m','01m','61m','25h','2d','NaNm','m',''])assert.equal(chartPeriodMs(period),0);
 assert.equal(chartBasePeriod('2m'),'1m');assert.equal(chartBasePeriod('30m'),'15m');assert.equal(chartBasePeriod('2h'),'1h');assert.equal(chartBasePeriod('24h'),'1d');
 assert.throws(()=>chartBasePeriod('999m'),/请选择/);
});
test('2m/3m aggregation preserves OHLCV and UTC boundaries and omits unfinished candles',()=>{
 const input=bars(7),result=aggregateChartCandles(input,'2m',7*60000);
 assert.equal(result.length,3);assert.deepEqual([result[0].open,result[0].high,result[0].low,result[0].close,result[0].volume],['100','104','99','103','4']);
 assert.equal(result[0].timeframe,'2m');assert.equal(result[0].open_time,stamp(0));assert.equal(result[0].close_time,stamp(120000));
 assert.equal(aggregateChartCandles(input,'3m',7*60000).length,2);
 assert.equal(aggregateChartCandles(input,'3m',9*60000).length,2,'a delayed final bucket is omitted, never padded; freshness is enforced by summarizeMarket');
 assert.equal(aggregateChartCandles(input.slice(1),'2m',7*60000)[0].open_time,stamp(120000));
 assert.deepEqual(input,bars(7),'source objects must not be mutated');
});
test('missing interior candles, duplicated times, source mixing and malformed prices fail closed',()=>{
 const input=bars(10);
 for(const change of [input.filter((_,i)=>i!==4),[...input.slice(0,4),input[3],...input.slice(4)],input.map((b,i)=>i===4?{...b,source:'other'}:b),input.map((b,i)=>i===4?{...b,high:'1'}:b)])assert.throws(()=>aggregateChartCandles(change,'2m',600000),/缺口|无效/);
 assert.throws(()=>aggregateChartCandles(input.filter((_,i)=>i<4||i>5),'2m',600000),/缺口/);
});
test('aggregated indicators use aggregated closes and confirmation clocks, with honest warmup',()=>{
 const result=aggregateChartCandles(bars(),'3m',360*60000),rows=chartIndicatorRows(result);
 assert.equal(rows.length,120);assert.equal(rows[0].values.sma,null);assert.equal(rows[19].values.sma,(Number(result[0].close)+Number(result[19].close))/2);
 assert.equal(rows.at(-1).available_at,result.at(-1).close_time);assert.equal(rows.at(-1).regime.available_at,result.at(-1).close_time);
 assert.equal(rows.at(-1).regime.label,'TREND_UP');assert.ok(rows.at(-1).values.atr>0);
 const prefix=chartIndicatorRows(result.slice(0,70));assert.deepEqual(prefix.map(r=>r.values),rows.slice(0,70).map(r=>r.values));
});
test('gold session gaps remain gaps and no incomplete gold period is fabricated',()=>{
 const input=bars(12).filter((_,i)=>i!==5);
 const result=aggregateChartCandles(input,'3m',12*60000,true);
 assert.deepEqual(result.map(b=>b.open_time),[stamp(0),stamp(6*60000),stamp(9*60000)]);
 assert.equal(result[1].volume,'6');
 assert.throws(()=>aggregateChartCandles(input,'3m',12*60000),/缺口/);
});
test('history paging requests earlier native data, honors cancellation and refuses non-progress',async t=>{
 const input=bars(4000),requests=[];
 t.mock.method(Date,'now',()=>4000*60000+1);
 t.mock.method(globalThis,'fetch',async url=>{const query=new URL(url,'https://local').searchParams;requests.push(query);const end=query.get('end');return Response.json(input.filter(b=>!end||b.open_time<end).slice(-Number(query.get('limit'))));});
 const result=await loadChartMarket('BTCUSDT','17m',new AbortController().signal);
 assert.equal(result.base,'1m');assert.equal(requests.length,3);assert.ok(requests[1].get('end'));assert.ok(result.bars.length>=119);
 const aborted=new AbortController();aborted.abort();await assert.rejects(loadChartMarket('BTCUSDT','2m',aborted.signal),{name:'AbortError'});
 globalThis.fetch=async()=>Response.json(input.slice(-1000));
 await assert.rejects(loadChartMarket('BTCUSDT','17m',new AbortController().signal),/没有前进/);
});
