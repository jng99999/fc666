import {test} from 'node:test';
import assert from 'node:assert/strict';
import {technicalSeries} from '../lib/technical-analysis.ts';
const bars=Array.from({length:70},(_,i)=>({open_time:new Date(i*60000).toISOString(),open:String(i+100),high:String(i+102),low:String(i+98),close:String(i+100),volume:'2'}));
const near=(actual,expected)=>assert.ok(Math.abs(actual-expected)<1e-9,`${actual} != ${expected}`);
test('known linear window gives independent rolling means, stochastic, CCI, ROC and OBV',()=>{
 const rows=technicalSeries(bars),last=rows.at(-1);
 near(last.ma5,167);near(last.ma10,164.5);near(last.ma60,139.5);
 near(last.stochK,100*15/17);near(last.stochD,last.stochK);near(last.wr,last.stochK-100);
 near(last.cci,9.5/(.015*5));near(last.roc,(169/157-1)*100);near(last.obv,138);
 assert.equal(rows[58].ma60,null);near(rows[59].ma60,129.5);
 assert.equal(rows[12].stochK,null);assert.equal(rows[14].stochD,null);assert.equal(rows[18].cci,null);assert.equal(rows[11].roc,null);
});
test('flat zero-volume market reports undefined ratios and no fabricated momentum',()=>{
 const rows=technicalSeries(bars.map(b=>({...b,open:'100',high:'100',low:'100',close:'100',volume:'0'})));
 const last=rows.at(-1);assert.equal(last.stochK,null);assert.equal(last.stochD,null);assert.equal(last.wr,null);assert.equal(last.cci,null);assert.equal(last.obv,0);assert.equal(last.roc,0);assert.equal(last.ma60,100);
 assert.deepEqual(technicalSeries([]),[]);
 const fractional=technicalSeries(bars.map(b=>({...b,open:'0.1234',high:'0.1234',low:'0.1234',close:'0.1234',volume:'0'}))).at(-1);
 assert.equal(fractional.cci,null);assert.equal(fractional.stochK,null);
});
test('calculations are causal, preserve source and OBV treats unchanged closes as zero',()=>{
 const before=JSON.stringify(bars),prefix=technicalSeries(bars.slice(0,35));
 assert.deepEqual(technicalSeries(bars).slice(0,35),prefix);assert.equal(JSON.stringify(bars),before);
 const mixed=technicalSeries([bars[0],{...bars[1],close:'99'},{...bars[2],close:'99'},{...bars[3],close:'101'}]);
 assert.deepEqual(mixed.map(r=>r.obv),[0,-2,-2,0]);
});
