import test from 'node:test';
import assert from 'node:assert/strict';
import {analysisSnapshot,calculateIndicator,indicatorCatalog,initialIndicators,validateIndicator} from '../app/analysis/analysis-indicators.ts';

// Synthetic mathematical fixtures only; never used by the application or published as market data.
const bars=(values)=>values.map((close,i)=>({instrument_id:'bitget:spot:BTC-USDT',timeframe:'1h',open_time:new Date(i*3600000).toISOString(),close_time:new Date((i+1)*3600000).toISOString(),open:String(close),high:String(close+1),low:String(close-1),close:String(close),volume:'10',is_closed:true,source:'bitget.public'}));
const config=(id,params={})=>({id,visible:true,params:{...indicatorCatalog[id].defaults,...params}});
const last=(id,data,params={})=>calculateIndicator(data,config(id,params))[0].values.at(-1);

test('known linear OHLC verifies SMA, EMA, ATR, RSI, MACD and selected parameters independently',()=>{
 const data=bars(Array.from({length:80},(_,i)=>100+i));
 assert.equal(last('sma',data,{period:20}),169.5);
 assert.equal(last('sma',data,{period:10}),174.5);
 assert.equal(last('smaSlow',data,{period:60}),149.5);
 assert.ok(Math.abs(last('ema',data,{period:20})-169.5)<1e-10);
 assert.equal(last('atr',data),2);
 assert.equal(last('rsi',data),100);
 const macd=calculateIndicator(data,config('macd'));
 assert.ok(Math.abs(macd[1].values.at(-1)-7)<1e-10);
 assert.ok(Math.abs(macd[2].values.at(-1)-7)<1e-10);
 assert.ok(Math.abs(macd[0].values.at(-1))<1e-10);
 const state=analysisSnapshot(data,initialIndicators);
 assert.equal(state.trend,'up');assert.equal(state.fast,169.5);assert.equal(state.rsi,100);
 const changed=analysisSnapshot(data,initialIndicators.map(c=>c.id==='sma'?{...c,params:{period:10}}:c));
 assert.equal(changed.fast,174.5);assert.equal(changed.fastPeriod,10);
});

test('prewarm, flat prices, zero volume and same-window causal prefixes remain explicit',()=>{
 const data=bars(Array(80).fill(100)).map(b=>({...b,volume:'0'}));
 assert.equal(last('sma',data.slice(0,19)),null);
 assert.equal(last('rsi',data.slice(0,14)),null);
 assert.equal(last('rsi',data),50);
 assert.equal(last('cci',data),null);
 assert.equal(last('vwap',data),null);
 const original=structuredClone(data);
 for(const id of Object.keys(indicatorCatalog)){
  const full=calculateIndicator(data,config(id)),prefix=calculateIndicator(data.slice(0,60),config(id));
  prefix.forEach((line,i)=>{
   assert.deepEqual(line.values,full[i].values.slice(0,60),id);
   assert.ok(line.values.every(v=>v===null||Number.isFinite(v)),id);
  });
 }
 assert.deepEqual(data,original);
});

test('invalid, missing, non-finite, fractional integer periods and inverted MACD are rejected',()=>{
 for(const period of [0,1,241,NaN,Infinity,20.5]){
  assert.ok(validateIndicator('sma',{period}));
  assert.throws(()=>calculateIndicator([],config('sma',{period})),/参数无效/);
 }
 assert.ok(validateIndicator('sma',{}));
 assert.ok(validateIndicator('macd',{fast:26,slow:12,signal:9}));
 assert.ok(validateIndicator('boll',{period:20,multiplier:6}));
 assert.equal(validateIndicator('boll',{period:20,multiplier:2.5}),null);
});

test('hiding preserves rule parameters and removing restores an explicit default reference',()=>{
 const data=bars(Array.from({length:80},(_,i)=>100+i));
 const configured=initialIndicators.map(c=>c.id==='sma'?{...c,visible:false,params:{period:10}}:c);
 assert.equal(analysisSnapshot(data,configured).fastPeriod,10);
 assert.equal(configured.filter(c=>c.visible).some(c=>c.id==='sma'),false);
 const removed=configured.filter(c=>c.id!=='sma');
 assert.equal(analysisSnapshot(data,removed).fastPeriod,20);
 assert.equal(removed.some(c=>c.id==='sma'),false);
});
