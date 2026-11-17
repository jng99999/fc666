import test from 'node:test';
import assert from 'node:assert/strict';
import {analyzePriceAction} from '../lib/price-action.ts';
import {chartPatterns} from '../lib/chart-patterns.ts';

const context={instrumentId:'XAU/USD',source:'test.closed',timeframe:'1m',volumeKind:'unavailable'};
const prices=[98,99,100,101,102,103,104,105,106,107,108,110,108,105,102,100,103,106,109.8,107,104];
const bars=(values=prices)=>values.map((close,i)=>({time:i*60000,closedAt:(i+1)*60000,open:close,high:close+.5,low:close-.5,close,volume:null}));
const run=rows=>chartPatterns(rows,analyzePriceAction(rows,context));

test('M candidate uses only confirmed pivots and fixed geometry; no volume is fabricated for gold',()=>{
 const rows=bars(),copy=structuredClone(rows),pattern=run(rows)[0];
 assert.equal(pattern.kind,'DOUBLE_TOP');assert.equal(pattern.state,'CANDIDATE');assert.equal(pattern.formedAt,21*60000);
 assert.equal(pattern.neckline,99.5);assert.ok(Math.abs(pattern.target-88.6)<1e-8);assert.deepEqual(rows,copy);
 for(let i=1;i<rows.length;i++)assert.equal(run(rows.slice(0,i)).length,0);
});
test('wick-only neckline excursions do not confirm; a close below buffer does',()=>{
 const wick=bars([...prices,101]);wick.at(-1).low=98;
 assert.equal(run(wick)[0].state,'CANDIDATE');
 const confirmed=run(bars([...prices,98]))[0];assert.equal(confirmed.state,'CONFIRMED');assert.equal(confirmed.breakAt,22*60000);
});
test('retest requires a later candle and closes on the breakout side',()=>{
 const rows=bars([...prices,98,98]);rows.at(-1).high=100;
 const result=run(rows)[0];assert.equal(result.state,'RETESTED');assert.equal(result.breakAt,22*60000);
});
test('reclaim invalidates the observed breakout and keeps its original target',()=>{
 const result=run(bars([...prices,98,101]))[0];assert.equal(result.state,'FAILED');assert.ok(Math.abs(result.target-88.6)<1e-8);
});
test('target is never scored on the confirmation bar; next-bar target touch is observed',()=>{
 const rows=bars([...prices,88]);assert.equal(run(rows)[0].state,'CONFIRMED');
 assert.equal(run(bars([...prices,98,88]))[0].state,'TARGET_REACHED');
});
test('same candle target and invalidation touch keeps intrabar sequence unknown',()=>{
 const rows=bars([...prices,98,95]);rows.at(-1).low=87;rows.at(-1).high=114;
 assert.equal(run(rows)[0].state,'AMBIGUOUS');
});
test('gaps expire an existing pattern at its last pre-gap bar; no cross-gap candidate forms',()=>{
 const rows=bars([...prices,98,97]);rows.at(-1).time+=60000;rows.at(-1).closedAt+=60000;
 const result=run(rows)[0];assert.equal(result.state,'EXPIRED');assert.equal(result.through,rows.at(-2).time);
 const split=bars();for(let i=14;i<split.length;i++){split[i].time+=60000;split[i].closedAt+=60000;}assert.equal(run(split).length,0);
});
test('W bottom mirrors M logic without assuming long-only prices',()=>{
 const rows=bars([...prices,98,98]).map(b=>({...b,open:220-b.open,high:220-b.low,low:220-b.high,close:220-b.close}));rows.at(-1).low=120;
 const result=run(rows)[0];assert.equal(result.kind,'DOUBLE_BOTTOM');assert.equal(result.direction,'up');assert.equal(result.state,'RETESTED');
});
test('a higher second top outside tolerance is not relabelled a double top',()=>{
 const altered=[...prices];altered[18]=115;assert.equal(run(bars(altered)).length,0);
 assert.equal(run([]).length,0);
});
