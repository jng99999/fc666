import test from 'node:test';
import assert from 'node:assert/strict';
import {chartPointInBounds} from '../lib/chart-landscape.ts';
import {chartCoordinates,zoomChartRange} from '../lib/chart-coordinates.ts';
import {chartTrendGeometry} from '../lib/chart-trend-geometry.ts';

test('quarter-turn drawing and gesture points map physical axes to actual chart pixels',()=>{
 const size={width:800,height:220},bounds={left:30,top:40,width:220,height:800};
 for(const [x,y] of [[0,0],[800,220],[215,72],[400,110]])assert.deepEqual(chartPointInBounds({x:bounds.left+220-y,y:bounds.top+x},bounds,size,true),{x,y});
 assert.deepEqual(chartPointInBounds({x:130,y:90},{left:30,top:40,width:400,height:110},size,false),{x:200,y:100});
 assert.ok(Number.isNaN(chartPointInBounds({x:1,y:1},{left:0,top:0,width:0,height:0},size,true).x));
});
test('gesture zoom preserves its anchor and rejects invalid factors',()=>{
 const range={from:10,to:110};assert.deepEqual(zoomChartRange(range,2,.25),{from:22.5,to:72.5});
 for(const factor of [0,-1,NaN,Infinity])assert.deepEqual(zoomChartRange(range,factor,.5),range);
 assert.equal(zoomChartRange(range,1e8,.5).to-zoomChartRange(range,1e8,.5).from,5);
});
test('chart gesture bridge pans logical bars, zooms at local x and inspects the actual series',()=>{
 let range={from:0,to:100},crosshair;
 const scale={width:()=>400,getVisibleLogicalRange:()=>range,setVisibleLogicalRange:value=>range=value,coordinateToTime:x=>x*10};
 const chart={timeScale:()=>scale,panes:()=>[{getHeight:()=>200}],setCrosshairPosition:(...args)=>crosshair=args},series={coordinateToPrice:y=>100-y};
 const bridge=chartCoordinates(chart,series,{},()=>[]);
 bridge.panByPixels(40);assert.deepEqual(range,{from:-10,to:90});
 bridge.zoomAt(2,200);assert.deepEqual(range,{from:15,to:65});
 bridge.inspectAt(20,30);assert.deepEqual(crosshair,[70,200,series]);bridge.dispose();
});
const channel={id:'channel',kind:'channel',label:'上行通道',start:0,end:100,confirmedAt:20,direction:'up',state:'ACTIVE',touches:2,low:10,high:16,explanation:'',rule:'fixed-parallel-channel-v1',points:[{time:0,price:10},{time:20,price:12},{time:0,price:14},{time:20,price:16}]};
test('up/down channel rails extend immutable anchors through observed lifetime and retain parallel width',()=>{
 for(const sign of [1,-1]){
  const input={...channel,points:channel.points.map(p=>({...p,price:sign===1?p.price:50-p.price}))},copy=structuredClone(input),result=chartTrendGeometry(input,40,90);
  assert.equal(result.start,40);assert.equal(result.end,90);assert.equal(result.points[1].time,90);assert.equal(result.points[3].time,90);
  assert.equal(Math.abs(result.points[2].price-result.points[0].price),4);assert.equal(Math.abs(result.points[3].price-result.points[1].price),4);assert.deepEqual(input,copy);
 }
});
test('broken channels stop at their actual break; neither future bars nor zero-length anchors extend them',()=>{
 const result=chartTrendGeometry({...channel,state:'BROKEN',end:45},0,100);assert.equal(result.end,45);assert.equal(result.points[1].time,45);
 const invalid={...channel,points:channel.points.map(p=>({...p,time:0}))};assert.equal(chartTrendGeometry(invalid,0,100),invalid);
});
