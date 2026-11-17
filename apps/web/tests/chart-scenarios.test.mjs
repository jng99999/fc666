import test from 'node:test';
import assert from 'node:assert/strict';
import {calculatePlan,runChartStudies,summarizeExposure} from '../lib/chart-scenarios.ts';

const free={feeRate:0,spread:0,slippage:0,fundingPerDay:0};
const options={costs:free,quantity:1,capital:10000,now:999999999};
function history(count=260){return Array.from({length:count},(_,index)=>{const open=100+Math.sin(index*.38)*8+index*.025,close=100+Math.sin((index+1)*.38)*8+index*.025;return {time:index*60000,closedAt:(index+1)*60000,open,close,high:Math.max(open,close)+1,low:Math.min(open,close)-1,volume:100};});}
test('long and short both pay adverse spread, slippage and two-sided fees',()=>{
 const long={direction:'LONG',quantity:2,entry:100,stop:90,target:120};
 const short={direction:'SHORT',quantity:2,entry:100,stop:110,target:80};
 for(const leg of [long,short]){const before=calculatePlan(leg,free),after=calculatePlan(leg,{...free,feeRate:.001,spread:2,slippage:.5});assert.ok(after.risk>before.risk);assert.ok(after.reward<before.reward);assert.ok(after.roundTripCost>0);if(leg.direction==='LONG'){assert.equal(after.entryFill,101.5);assert.equal(after.targetFill,118.5);assert.ok(after.breakEven>100);}else{assert.equal(after.entryFill,98.5);assert.equal(after.targetFill,81.5);assert.ok(after.breakEven<100);}}
});
test('invalid, missing, negative and inverted plan inputs fail closed',()=>{
 const leg={direction:'LONG',quantity:1,entry:100,stop:90,target:110};
 for(const patch of [{quantity:0},{quantity:NaN},{entry:Infinity},{stop:110},{target:90},{direction:'FLAT'}])assert.throws(()=>calculatePlan({...leg,...patch},free));
 assert.throws(()=>calculatePlan(leg,{...free,feeRate:-.01}));
 assert.throws(()=>calculatePlan(leg,{...free,spread:500}));
 assert.throws(()=>calculatePlan({...leg,quantity:Number.MAX_VALUE},free),/范围/);
});
test('equal long and short quantity has zero linear shock and no net break-even',()=>{
 const result=summarizeExposure([{direction:'LONG',quantity:2,entry:100,mark:105,equity:1000},{direction:'SHORT',quantity:2,entry:110,mark:105,equity:1000}],-10);
 assert.equal(result.netQuantity,0);assert.equal(result.netBreakEven,null);assert.equal(result.shockPnl,0);assert.equal(result.stressedEquity,2000);assert.equal(result.longAverage,100);assert.equal(result.shortAverage,110);
 assert.throws(()=>summarizeExposure([{direction:'LONG',quantity:1,entry:100,mark:NaN,equity:1000}],-10));
});
test('future candles are excluded and cannot change the replay result',()=>{
 const bars=history(),now=bars.at(-1).closedAt;
 const before=runChartStudies(bars,{...options,now});
 const future={...bars.at(-1),time:now+60000,closedAt:now+120000,open:999,high:999,low:1,close:1};
 const after=runChartStudies([...bars,future],{...options,now});
 assert.deepEqual(after.results,before.results);assert.equal(after.bars,before.bars);
 for(const result of before.results)for(const trade of result.trades){assert.ok(trade.signalAt<=trade.entryAt);assert.ok(trade.entryAt<trade.exitAt);assert.ok(trade.exitAt<=now);}
});
test('fees increase costs without improving any fixed-quantity strategy result',()=>{
 const bars=history(),a=runChartStudies(bars,options),b=runChartStudies(bars,{...options,costs:{...free,feeRate:.003,fundingPerDay:.001}});
 assert.ok(a.results.reduce((sum,row)=>sum+row.trades.length,0)>0,'fixture must exercise real entries');
 for(let i=0;i<a.results.length;i++){assert.equal(a.results[i].trades.length,b.results[i].trades.length);assert.ok(b.results[i].net<=a.results[i].net);assert.ok(b.results[i].cost>=a.results[i].cost);}
});
test('missing history and malformed chronology refuse the study; history is bounded',()=>{
 assert.throws(()=>runChartStudies([],options),/60/);
 const bars=history();assert.throws(()=>runChartStudies([...bars.slice(0,70),bars[69],...bars.slice(70)],options),/乱序/);
 assert.throws(()=>runChartStudies(bars,{...options,quantity:NaN}));
 assert.throws(()=>runChartStudies(bars.map((bar,index)=>index===100?{...bar,low:-1}:bar),options));
 const report=runChartStudies(history(800),options);assert.equal(report.bars,500);assert.equal(report.excluded,300);
});
test('all-positive-price data with unknown volume can be researched without inventing traded volume',()=>{
 const report=runChartStudies(history().map(bar=>({...bar,volume:null})),options);
 assert.equal(report.results.length,4);assert.equal(report.bars,260);
});
test('time gaps force an explicit exit and require fresh lookback history before another entry',()=>{
 const bars=history().map((bar,index)=>index>=160?{...bar,time:bar.time+3600000,closedAt:bar.closedAt+3600000}:bar);
 const report=runChartStudies(bars,options);assert.ok(report.warnings.some(value=>value.includes('缺口')));
 const gapAt=bars[160].time,readyAt=bars[200].time;
 for(const result of report.results)for(const trade of result.trades)assert.ok(trade.entryAt<gapAt||trade.entryAt>=readyAt);
});
test('uncertain intrabar stop and target touches are counted as a stop, including entry bar',()=>{
 const bars=history();const first=runChartStudies(bars,options).results.find(row=>row.trades.length)?.trades[0];assert.ok(first);
 const index=bars.findIndex(bar=>bar.time===first.entryAt),ambiguous=bars.map((bar,i)=>i===index?{...bar,low:1,high:1000}:bar);
 const report=runChartStudies(ambiguous,options),trade=report.results.flatMap(row=>row.trades).find(row=>row.entryAt===first.entryAt&&row.ambiguous);
 assert.ok(trade);assert.equal(trade.reason,'止损');assert.ok(trade.net<0);
});
test('a gap inside initial warmup also blocks entry until a fully observed lookback exists',()=>{
 const bars=history().map((bar,index)=>index>=30?{...bar,time:bar.time+3600000,closedAt:bar.closedAt+3600000}:bar);
 const report=runChartStudies(bars,options),readyAt=bars[70].time;
 for(const result of report.results)for(const trade of result.trades)assert.ok(trade.entryAt>=readyAt);
});
