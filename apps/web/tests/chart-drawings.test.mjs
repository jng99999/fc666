import test from 'node:test';
import assert from 'node:assert/strict';
import {decodeDrawings,drawingStorageKey,encodeDrawings,snapDrawingPoint,validDrawing} from '../lib/chart-drawings.ts';
import {chartCoordinates} from '../lib/chart-coordinates.ts';
const context={instrumentId:'bitget:spot:BTC-USDT',source:'bitget.public',timeframe:'1h',volumeKind:'trade'};
const drawing={id:'manual-test',kind:'box',points:[{time:1000,price:10},{time:2000,price:20}],locked:true,label:'研究箱体'};

test('drawings round trip; product, provider and interval are isolated, including gold',()=>{
 const text=encodeDrawings([drawing],context);
 assert.deepEqual(decodeDrawings(text,context),[drawing]);
 for(const other of [{...context,timeframe:'4h'},{...context,source:'other.public'},{...context,instrumentId:'bitget:spot:ETH-USDT'},{...context,instrumentId:'XAU/USD',source:'swissquote.public'}]){
  assert.notEqual(drawingStorageKey(context),drawingStorageKey(other));
  assert.throws(()=>decodeDrawings(text,other));
 }
});
test('malformed, infinite, duplicate, excessive and degenerate import cannot replace drawings',()=>{
 for(const value of [null,{}, {...drawing,kind:'__proto__'}, {...drawing,points:[]},{...drawing,points:[{time:1000,price:10},{time:1000,price:20}]},{...drawing,points:[{time:1000,price:10},{time:2000,price:10}]},{...drawing,locked:'yes'},{...drawing,points:[{time:1000,price:10},{time:2000,price:Infinity}]}])assert.equal(validDrawing(value),false);
 assert.throws(()=>decodeDrawings('invalid',context));
 assert.throws(()=>decodeDrawings(encodeDrawings([drawing,drawing],context),context));
 assert.throws(()=>decodeDrawings(encodeDrawings(Array.from({length:101},(_,i)=>({...drawing,id:String(i)})),context),context));
});
test('snap uses actual candle prices and never alters the input or creates another-market quote',()=>{
 const bars=[{time:1000,closedAt:2000,open:10,high:20,low:5,close:12,volume:4}],point={time:1000,price:18};
 assert.deepEqual(snapDrawingPoint(point,bars,true),{time:1000,price:20});
 assert.deepEqual(snapDrawingPoint(point,bars,false),point);
 assert.deepEqual(snapDrawingPoint({...point,time:4000},bars,true),{time:4000,price:18});
 assert.equal(point.price,18);
});
test('import rejects dates that would crash the anchor editor, before changing storage',()=>{
 for(const time of [1e100,8640000000000001,1000.5,NaN,Infinity,-1]){
  const bad={...drawing,points:[{time,price:10},{time:2000,price:20}]};
  assert.equal(validDrawing(bad),false);
  assert.throws(()=>decodeDrawings(encodeDrawings([bad],context),context));
 }
});
test('coordinate bridge follows chart logical spacing and scale, rejects unknown endpoints',()=>{
 let zoom=10,offset=5;const times=[1000,2000,6000];
 const scale={timeToCoordinate:()=>null,logicalToCoordinate:x=>offset+x*zoom,coordinateToLogical:x=>(x-offset)/zoom,width:()=>300};
 const chart={timeScale:()=>scale,panes:()=>[{getHeight:()=>200}]};
 const series={priceToCoordinate:p=>200-p*2,coordinateToPrice:y=>(200-y)/2};
 const bridge=chartCoordinates(chart,series,{},()=>times);
 assert.equal(bridge.timeToX(4000),20); // midway through the actual second/third candle indices
 assert.equal(bridge.xToTime(24),6000);assert.equal(bridge.priceToY(20),160);
 zoom=20;offset=-15;
 assert.equal(bridge.timeToX(4000),15);assert.equal(bridge.yToPrice(160),20);
 assert.equal(bridge.timeToX(0),null);assert.equal(bridge.timeToX(7000),null);
 assert.deepEqual(bridge.size(),{width:300,height:200});
 bridge.dispose();assert.equal(typeof bridge.subscribe(()=>{}),'function');
});
