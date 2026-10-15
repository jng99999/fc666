import {test} from 'node:test';
import assert from 'node:assert/strict';
import {drawdownReport} from '../lib/drawdown-report.ts';
const rows=values=>values.map((value,i)=>({as_of:new Date(Date.UTC(2026,0,1,i)).toISOString(),drawdown:String(value)}));
test('episodes separate recovery from right-censored decline and preserve loss sign',()=>{
 const input=rows([0,.1,.2,.1,0,.05,.1]);const before=JSON.stringify(input);
 const report=drawdownReport(input,'-0.1');
 assert.equal(report.episodes.length,2);assert.equal(report.episodes[0].recovered,true);
 assert.equal(report.episodes[0].seconds,3*3600);assert.equal(report.episodes[0].trough,input[2].as_of);
 assert.equal(report.episodes[1].recovered,false);assert.equal(report.episodes[1].seconds,3600);
 assert.equal(report.longestSeconds,10800);assert.equal(report.underwaterFraction,5/7);
 assert.equal(report.returnToDrawdown,-.5);assert.ok(Math.abs(report.ulcerIndex-Math.sqrt(.0725/7))<1e-12);
 assert.equal(JSON.stringify(input),before);
});
test('flat equity has no episodes and no invented infinite ratio',()=>{
 const report=drawdownReport(rows([0,0,0]),'0');assert.equal(report.ulcerIndex,0);
 assert.equal(report.returnToDrawdown,null);assert.deepEqual(report.episodes,[]);assert.equal(report.longestSeconds,0);
});
test('leading and final underwater observations remain explicitly unrecovered',()=>{
 const report=drawdownReport(rows([.1,.2]),'-.2');assert.equal(report.episodes[0].start,rows([.1])[0].as_of);
 assert.equal(report.episodes[0].seconds,3600);assert.equal(report.episodes[0].recovered,false);
});
test('one underwater observation ending the sample has zero observed duration',()=>{
 const report=drawdownReport(rows([0,.1]),'-.1');assert.equal(report.episodes[0].seconds,0);assert.equal(report.episodes[0].recovered,false);
});
test('empty, short, unordered and corrupt data fail closed',()=>{
 for(const input of [[],rows([0]),rows([0,.1]).reverse(),[rows([0])[0],rows([0])[0]],rows([0,NaN]),rows([0,-.1]),rows([0,1.1]),[{as_of:'2026-01-01',drawdown:'0'},rows([0,.1])[1]],[{as_of:'bad',drawdown:'0'},rows([0,.1])[1]],[rows([0])[0],{...rows([0,.1])[1],drawdown:''}]])assert.equal(drawdownReport(input,'0'),null);
 for(const value of ['','NaN','Infinity'])assert.equal(drawdownReport(rows([0,.1]),value),null);
});
test('calendar elapsed time respects timezone offsets',()=>{
 const report=drawdownReport([{as_of:'2026-01-01T08:00:00+08:00',drawdown:'.1'},{as_of:'2026-01-01T01:00:00Z',drawdown:'0'}],'0');
 assert.equal(report.episodes[0].seconds,3600);assert.equal(report.episodes[0].recovered,true);
});
