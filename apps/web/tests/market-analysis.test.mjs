import {test} from 'node:test';
import assert from 'node:assert/strict';
import {summarizeMarket} from '../lib/market-analysis.ts';
const stamp=value=>new Date(value).toISOString();
function input(){
 const bars=Array.from({length:21},(_,i)=>({timeframe:'1m',open_time:stamp(i*60000),close_time:stamp((i+1)*60000),open:'100',high:'102',low:'99',close:'101',volume:i===20?'40':'20',is_closed:true}));
 const row={available_at:bars.at(-1).close_time,regime:{label:'TREND_UP'}};
 return {bars,row,now:21*60000+1000};
}
test('closed window and volume exclude current unfinished candle',()=>{
 const {bars,row,now}=input();
 const pending={...bars.at(-1),open_time:stamp(21*60000),close_time:stamp(22*60000),close:'9999',is_closed:false};
 const result=summarizeMarket([...bars,pending],row,'1m',now);
 assert.equal(result.closedCount,21);assert.equal(result.volumeRatio,2);assert.equal(result.high,102);assert.equal(result.low,99);assert.equal(result.change,1.0000000000000009);
});
test('stale, gapped, short and misaligned data do not produce analysis',()=>{
 const {bars,row,now}=input();
 assert.throws(()=>summarizeMarket(bars,row,'1m',now+75000),/过期/);
 assert.throws(()=>summarizeMarket(bars.filter((_,i)=>i!==3),row,'1m',now),/不足/);
 assert.throws(()=>summarizeMarket([...bars.slice(0,10),{...bars[10],open_time:stamp(60001)},...bars.slice(11)],row,'1m',now),/不连续/);
 assert.throws(()=>summarizeMarket(bars,{...row,available_at:stamp(1)},'1m',now),/时点不同/);
});
test('zero historical volume is unavailable, not infinity',()=>{
 const {bars,row,now}=input();
 const result=summarizeMarket(bars.map(bar=>({...bar,volume:'0'})),row,'1m',now);
 assert.equal(result.volumeRatio,null);
});
