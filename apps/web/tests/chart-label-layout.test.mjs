import test from 'node:test';
import assert from 'node:assert/strict';
import {layoutChartLabels} from '../lib/chart-label-layout.ts';
const label=(id,extra={})=>({id,text:'供应 / OB候选',title:'供应 / OB 候选完整依据',x:260,y:140,color:'#d84c70',priority:1,...extra});
const overlaps=(a,b)=>a.left<b.left+b.width&&a.left+a.width>b.left&&a.top<b.top+b.height&&a.top+a.height>b.top;

test('crowded mobile labels do not overlap or leave the main price pane; selected is retained first',()=>{
 const input=Array.from({length:40},(_,i)=>label(String(i))),selected=label('selected',{priority:100,text:'选中很长的供应与订单块候选区域名称'});
 const result=layoutChartLabels([...input,selected],{width:300,height:220});
 assert.equal(result[0].id,'selected');assert.ok(result[0].displayText.endsWith('…'));
 assert.ok(result.length<input.length);assert.ok(result.length>1);
 for(const a of result){assert.ok(a.left>=4&&a.top>=4&&a.left+a.width<=296&&a.top+a.height<=216);for(const b of result)if(a!==b)assert.equal(overlaps(a,b),false);}
 assert.deepEqual(layoutChartLabels([...input,selected],{width:300,height:220}),result);
 assert.equal(selected.x,260);assert.equal(selected.y,140);
});
test('offscreen and invalid anchors are omitted; empty or collapsed charts do not create invalid geometry',()=>{
 for(const viewport of [{width:0,height:220},{width:300,height:20},{width:NaN,height:200},{width:30,height:100}])assert.deepEqual(layoutChartLabels([label('a')],viewport),[]);
 assert.deepEqual(layoutChartLabels([label('above',{y:-1}),label('below',{y:221}),label('invalid',{x:NaN})],{width:300,height:220}),[]);
 assert.deepEqual(layoutChartLabels([],{width:300,height:220}),[]);
});
test('labels clamp to both horizontal edges without modifying chart anchors or source objects',()=>{
 const source=[label('left',{x:-50,y:0}),label('right',{x:900,y:220})],snapshot=structuredClone(source);
 const result=layoutChartLabels(source,{width:300,height:220});assert.equal(result.length,2);
 assert.equal(result[0].left,4);assert.equal(result[1].left+result[1].width,296);
 assert.deepEqual(source,snapshot);assert.equal(result[0].x,-50);assert.equal(result[1].x,900);
});
